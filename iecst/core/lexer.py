"""Tokenizer for IEC 61131-3 Structured Text.

Structured Text is deliberately hostile to naive tokenizers, mostly because of
``#``.  The same character introduces an integer base (``16#FF``), a typed
literal (``INT#5``) and every time/date literal (``T#1m30s``, ``DT#2024-01-01-12:00``).
The disambiguation rule used here is: an identifier or keyword immediately
followed by ``#`` is a literal prefix, and *which* literal it introduces is
decided by the prefix itself.

Identifiers are case-insensitive in IEC 61131-3, so their text is normalised to
upper case.  The original spelling is kept in ``Token.raw`` for error messages.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass

from iecst.core.errors import IecSyntaxError

# --- keyword tables -------------------------------------------------------

KEYWORDS: frozenset[str] = frozenset(
    """
    PROGRAM END_PROGRAM FUNCTION END_FUNCTION FUNCTION_BLOCK END_FUNCTION_BLOCK
    CLASS END_CLASS INTERFACE END_INTERFACE METHOD END_METHOD
    PROPERTY END_PROPERTY ACTION END_ACTION
    NAMESPACE END_NAMESPACE EXTENDS IMPLEMENTS OVERLAP
    FINAL ABSTRACT
    PRIVATE PUBLIC PROTECTED INTERNAL
    VAR VAR_INPUT VAR_OUTPUT VAR_IN_OUT VAR_GLOBAL VAR_EXTERNAL VAR_TEMP
    VAR_STAT VAR_CONFIG RETAIN NON_RETAIN PERSISTENT CONSTANT END_VAR AT
    TYPE END_TYPE STRUCT END_STRUCT UNION END_UNION ARRAY OF
    POINTER REFERENCE
    IF THEN ELSIF ELSE END_IF CASE END_CASE FOR TO BY DO END_FOR
    WHILE END_WHILE REPEAT UNTIL END_REPEAT EXIT CONTINUE RETURN
    AND OR XOR NOT MOD
    GE LE EQ NE LT GT
    TRUE FALSE NULL
    BOOL BYTE WORD DWORD LWORD
    SINT INT DINT LINT USINT UINT UDINT ULINT
    REAL LREAL TIME DATE TOD DT DATE_AND_TIME TIME_OF_DAY
    STRING WSTRING CHAR WCHAR ANY
    """.split()
)

TYPE_NAMES: frozenset[str] = frozenset(
    """
    BOOL BYTE WORD DWORD LWORD
    SINT INT DINT LINT USINT UINT UDINT ULINT
    REAL LREAL TIME DATE TOD DT DATE_AND_TIME TIME_OF_DAY
    STRING WSTRING CHAR WCHAR
    """.split()
)

# Literal prefixes.  ``T`` is not a reserved word but ``T#1s`` is a valid time
# literal, so the prefix tables are checked before the type-name table.
TIME_PREFIXES = frozenset({"T", "TIME"})
DATE_PREFIXES = frozenset({"D", "DATE"})
TOD_PREFIXES = frozenset({"TOD", "TIME_OF_DAY"})
DT_PREFIXES = frozenset({"DT", "DATE_AND_TIME"})

# Longest match first: ``ms`` must win over ``m``.
_TIME_UNITS: tuple[tuple[str, int], ...] = (
    ("ms", 1_000_000),
    ("us", 1_000),
    ("ns", 1),
    ("d", 86_400_000_000_000),
    ("h", 3_600_000_000_000),
    ("m", 60_000_000_000),
    ("s", 1_000_000_000),
)

_INT_BASES = {"2": 2, "8": 8, "16": 16, "10": 10}

_OPERATORS: tuple[str, ...] = (
    ":=", "=>", "<>", "<=", ">=", "**", "..", "::",
    "+", "-", "*", "/", "=", "<", ">", "(", ")", "[", "]", ".", ",", ";", ":", "&", "^", "#",
)


@dataclass(slots=True)
class Token:
    """A single lexical token."""

    kind: str
    value: object
    line: int
    col: int
    raw: str = ""
    type_name: str | None = None

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Token({self.kind!r}, {self.value!r}, {self.line}:{self.col})"


class Lexer:
    def __init__(self, text: str, filename: str = "<st>") -> None:
        self.text = text
        self.filename = filename
        self.pos = 0
        self.line = 1
        self.line_start = 0
        self.tokens: list[Token] = []

    # -- low level helpers -------------------------------------------------

    def error(self, message: str, line: int | None = None, col: int | None = None) -> IecSyntaxError:
        if line is None:
            line = self.line
        if col is None:
            col = self.pos - self.line_start + 1
        return IecSyntaxError(message, source=self.filename, line=line, col=col)

    def _peek(self, offset: int = 0) -> str:
        i = self.pos + offset
        return self.text[i] if i < len(self.text) else ""

    def _starts(self, s: str) -> bool:
        return self.text.startswith(s, self.pos)

    def _advance(self, n: int = 1) -> None:
        for _ in range(n):
            if self.pos >= len(self.text):
                return
            if self.text[self.pos] == "\n":
                self.line += 1
                self.line_start = self.pos + 1
            self.pos += 1

    def _newline_in(self, s: str) -> None:
        """Account for newlines inside a chunk that is consumed wholesale."""
        count = s.count("\n")
        if count:
            self.line += count
            self.line_start = self.pos - (len(s) - s.rfind("\n") - 1)

    # -- skippers ----------------------------------------------------------

    def _skip_trivia(self) -> None:
        text = self.text
        while self.pos < len(text):
            ch = text[self.pos]
            if ch in " \t\r\n":
                self._advance()
            elif self._starts("//"):
                while self.pos < len(text) and text[self.pos] != "\n":
                    self._advance()
            elif self._starts("(*"):
                self._skip_bracketed_comment()
            elif self._starts("/*"):
                self._skip_line_comment_block()
            elif ch == "{":
                # CODESYS pragma such as {attribute 'hide'}; not semantically
                # meaningful to the interpreter, so it is dropped.
                end = text.find("}", self.pos)
                if end < 0:
                    raise self.error("unterminated pragma '{'")
                chunk = text[self.pos : end + 1]
                self._advance(len(chunk))
            else:
                return

    def _skip_bracketed_comment(self) -> None:
        # IEC 61131-3 allows (* nested (* comments *) *), and CODESYS relies on
        # it, so a depth counter is required rather than a plain find('*)').
        start_line, start_col = self.line, self.pos - self.line_start + 1
        depth = 0
        while self.pos < len(self.text):
            if self._starts("(*"):
                depth += 1
                self._advance(2)
            elif self._starts("*)"):
                depth -= 1
                self._advance(2)
                if depth == 0:
                    return
            else:
                self._advance()
        raise self.error("unterminated comment '(*'", start_line, start_col)

    def _skip_line_comment_block(self) -> None:
        start_line, start_col = self.line, self.pos - self.line_start + 1
        end = self.text.find("*/", self.pos + 2)
        if end < 0:
            raise self.error("unterminated comment '/*'", start_line, start_col)
        self._advance(end + 2 - self.pos)

    # -- literal readers ---------------------------------------------------

    def _read_digits(self, alphabet: str) -> str:
        start = self.pos
        while self.pos < len(self.text) and (self.text[self.pos] in alphabet or self.text[self.pos] == "_"):
            self._advance()
        return self.text[start : self.pos].replace("_", "")

    def _read_number(self) -> Token:
        """Read an integer (any base) or a real literal."""
        line, col = self.line, self.pos - self.line_start + 1
        start = self.pos

        if self._peek() == "0" and self._peek(1) in "xXbBoO":
            # Convenience spelling, not IEC, but harmless to accept.
            radix_char = self._peek(1).lower()
            base = {"x": 16, "b": 2, "o": 8}[radix_char]
            self._advance(2)
            alphabet = {16: "0123456789abcdefABCDEF", 2: "01", 8: "01234567"}[base]
            digits = self._read_digits(alphabet)
            if not digits:
                raise self.error("missing digits after base prefix", line, col)
            return Token("int", int(digits, base), line, col, self.text[start : self.pos])

        int_part = self._read_digits("0123456789")

        if self._peek() == "#" and int_part in _INT_BASES:
            base = _INT_BASES[int_part]
            self._advance()
            alphabet = {
                16: "0123456789abcdefABCDEF",
                2: "01",
                8: "01234567",
                10: "0123456789",
            }[base]
            digits = self._read_digits(alphabet)
            if not digits:
                raise self.error(f"missing digits after base prefix '{int_part}#'", line, col)
            return Token("int", int(digits, base), line, col, self.text[start : self.pos])

        if not int_part:
            raise self.error("malformed number", line, col)

        # A '.' only starts a fraction when a digit follows; otherwise it is
        # either the start of the range operator '..' or a struct/field access.
        is_real = False
        if self._peek() == "." and self._peek(1).isdigit():
            is_real = True
            self._advance()
            self._read_digits("0123456789")

        if self._peek() in "eE":
            nxt = self._peek(1)
            offset = 2 if nxt in "+-" else 1
            if self._peek(offset).isdigit():
                is_real = True
                self._advance(offset)
                self._read_digits("0123456789")

        raw = self.text[start : self.pos]
        if is_real:
            return Token("real", float(raw), line, col, raw)
        return Token("int", int(raw, 10), line, col, raw)

    def _read_time_literal(self, line: int, col: int, start: int) -> Token:
        """Read the body of a ``T#`` / ``TIME#`` literal into nanoseconds."""
        sign = 1
        if self._peek() in "+-":
            if self._peek() == "-":
                sign = -1
            self._advance()

        total = 0.0
        pieces = 0
        while self._peek().isdigit():
            digits = self._read_digits("0123456789")
            frac = 0.0
            if self._peek() == "." and self._peek(1).isdigit():
                self._advance()
                frac_digits = self._read_digits("0123456789")
                frac = float(f"0.{frac_digits}")
            unit = None
            for suffix, scale in _TIME_UNITS:
                if self._starts(suffix):
                    unit = scale
                    self._advance(len(suffix))
                    break
            if unit is None:
                raise self.error("missing time unit (expecting d, h, m, s, ms, us or ns)", line, col)
            total += (float(digits) + frac) * unit
            pieces += 1

        if pieces == 0:
            raise self.error("malformed time literal", line, col)
        return Token("time", sign * round(total), line, col, self.text[start : self.pos])

    def _read_date_literal(self, line: int, col: int, start: int) -> Token:
        raw = self._read_date_part(line, col)
        y, m, d = raw
        try:
            ordinal = datetime.date(y, m, d).toordinal()
        except ValueError as exc:
            raise self.error(f"invalid date literal: {exc}", line, col) from exc
        return Token("date", ordinal, line, col, self.text[start : self.pos])

    def _read_date_part(self, line: int, col: int) -> tuple[int, int, int]:
        def field(width: int) -> int:
            digits = self._read_digits("0123456789")
            if not digits or len(digits) > width:
                raise self.error("malformed date literal", line, col)
            return int(digits)

        year = field(4)
        if self._peek() != "-":
            raise self.error("malformed date literal (expecting '-')", line, col)
        self._advance()
        month = field(2)
        if self._peek() != "-":
            raise self.error("malformed date literal (expecting '-')", line, col)
        self._advance()
        day = field(2)
        return year, month, day

    def _read_tod_part(self, line: int, col: int) -> int:
        """Read ``HH:MM[:SS[.frac]]`` into nanoseconds since midnight."""

        def field(width: int) -> int:
            digits = self._read_digits("0123456789")
            if not digits or len(digits) > width:
                raise self.error("malformed time-of-day literal", line, col)
            return int(digits)

        hours = field(2)
        if self._peek() != ":":
            raise self.error("malformed time-of-day literal (expecting ':')", line, col)
        self._advance()
        minutes = field(2)
        seconds = 0.0
        if self._peek() == ":":
            self._advance()
            seconds = float(field(2))
            if self._peek() == "." and self._peek(1).isdigit():
                self._advance()
                seconds += float(f"0.{self._read_digits('0123456789')}")
        if hours > 23 or minutes > 59 or seconds >= 60:
            raise self.error("time-of-day literal out of range", line, col)
        return round((hours * 3600 + minutes * 60 + seconds) * 1_000_000_000)

    def _read_tod_literal(self, line: int, col: int, start: int) -> Token:
        return Token("tod", self._read_tod_part(line, col), line, col, self.text[start : self.pos])

    def _read_dt_literal(self, line: int, col: int, start: int) -> Token:
        y, m, d = self._read_date_part(line, col)
        if self._peek() != "-":
            raise self.error("malformed date-and-time literal (expecting '-' between date and time)", line, col)
        self._advance()
        ns = self._read_tod_part(line, col)
        try:
            ordinal = datetime.date(y, m, d).toordinal()
        except ValueError as exc:
            raise self.error(f"invalid date literal: {exc}", line, col) from exc
        value = ordinal * 86_400_000_000_000 + ns
        return Token("dt", value, line, col, self.text[start : self.pos])

    def _read_string(self) -> Token:
        quote = self.text[self.pos]
        line, col = self.line, self.pos - self.line_start + 1
        start = self.pos
        self._advance()

        out: list[str] = []
        while True:
            if self.pos >= len(self.text):
                raise self.error("unterminated string literal", line, col)
            ch = self.text[self.pos]
            if ch == quote:
                if self._peek(1) == quote:  # doubled quote is an escaped quote
                    out.append(quote)
                    self._advance(2)
                    continue
                self._advance()
                break
            if ch == "$":  # IEC escape character
                self._advance()
                esc = self._peek()
                mapping = {"n": "\n", "r": "\r", "t": "\t", "'": "'", '"': '"', "$": "$", "l": "\n", "p": "\f"}
                if esc not in mapping:
                    raise self.error(f"unknown escape sequence '${esc}'", self.line, self.pos - self.line_start)
                out.append(mapping[esc])
                self._advance()
                continue
            out.append(ch)
            self._advance()

        kind = "wstr" if quote == '"' else "str"
        return Token(kind, "".join(out), line, col, self.text[start : self.pos])

    def _read_address(self) -> Token:
        """Read a direct variable address such as ``%IX0.0`` or ``%QW12``."""
        line, col = self.line, self.pos - self.line_start + 1
        start = self.pos
        self._advance()  # consume '%'
        while self.pos < len(self.text) and (self.text[self.pos].isalnum() or self.text[self.pos] in ".*"):
            self._advance()
        return Token("address", self.text[start : self.pos], line, col, self.text[start : self.pos])

    def _read_typed_literal(self, type_name: str, line: int, col: int, start: int) -> Token:
        """Read the body of ``TYPE#value``; the ``#`` is already consumed."""
        sign = 1
        if self._peek() in "+-" and self._peek(1).isdigit():
            if self._peek() == "-":
                sign = -1
            self._advance()

        ch = self._peek()
        if ch in "'\"":
            body = self._read_string()
        elif ch.isdigit():
            body = self._read_number()
            if sign < 0:
                body = Token(body.kind, -body.value, body.line, body.col, body.raw)
        else:
            word = self._read_identifier_text()
            upper = word.upper()
            if upper == "TRUE":
                body = Token("bool", True, line, col, word)
            elif upper == "FALSE":
                body = Token("bool", False, line, col, word)
            else:
                raise self.error(f"malformed typed literal '{type_name}#{word}'", line, col)

        # A float-typed literal may be written with an integer body: REAL#2.
        if type_name in ("REAL", "LREAL") and body.kind == "int":
            body = Token("real", float(body.value), body.line, body.col, body.raw)

        raw = self.text[start : self.pos]
        return Token(body.kind, body.value, line, col, raw, type_name=type_name)

    # -- main loop ---------------------------------------------------------

    def _read_identifier_text(self) -> str:
        start = self.pos
        while self.pos < len(self.text) and (self.text[self.pos].isalnum() or self.text[self.pos] == "_"):
            self._advance()
        return self.text[start : self.pos]

    def run(self) -> list[Token]:
        text = self.text
        while True:
            self._skip_trivia()
            if self.pos >= len(text):
                break
            self._next_token()
        self.tokens.append(Token("eof", None, self.line, self.pos - self.line_start + 1, ""))
        return self.tokens

    def _next_token(self) -> None:
        text = self.text
        line, col = self.line, self.pos - self.line_start + 1
        start = self.pos
        ch = text[self.pos]

        if ch.isalpha() or ch == "_":
            word = self._read_identifier_text()
            upper = word.upper()

            if self._peek() == "#" and (upper in TYPE_NAMES or upper in TIME_PREFIXES
                                        or upper in DATE_PREFIXES or upper in TOD_PREFIXES
                                        or upper in DT_PREFIXES):
                self._advance()  # consume '#'
                if upper in TIME_PREFIXES:
                    self.tokens.append(self._read_time_literal(line, col, start))
                elif upper in DATE_PREFIXES:
                    self.tokens.append(self._read_date_literal(line, col, start))
                elif upper in TOD_PREFIXES:
                    self.tokens.append(self._read_tod_literal(line, col, start))
                elif upper in DT_PREFIXES:
                    self.tokens.append(self._read_dt_literal(line, col, start))
                else:
                    self.tokens.append(self._read_typed_literal(upper, line, col, start))
                return

            if upper in KEYWORDS:
                self.tokens.append(Token("kw", upper, line, col, word))
            else:
                # IEC identifiers are case-insensitive; normalising here keeps
                # every downstream symbol lookup trivially consistent.
                self.tokens.append(Token("ident", upper, line, col, word))
            return

        if ch.isdigit():
            self.tokens.append(self._read_number())
            return

        if ch in "'\"":
            self.tokens.append(self._read_string())
            return

        if ch == "%":
            self.tokens.append(self._read_address())
            return

        for op in _OPERATORS:
            if self._starts(op):
                self._advance(len(op))
                self.tokens.append(Token("op", op, line, col, op))
                return

        raise self.error(f"unexpected character {ch!r}")


def tokenize(text: str, filename: str = "<st>") -> list[Token]:
    """Tokenize *text*, returning the token stream terminated by an ``eof`` token."""
    return Lexer(text, filename).run()
