"""Recursive-descent parser for IEC 61131-3 Structured Text.

Expression parsing uses precedence climbing with the operator table of the
standard (OR < XOR < AND < equality < comparison < additive < multiplicative <
power < unary) and ``**`` is right-associative.

The parser is deliberately permissive about statement separators and about
CODESYS extensions that do not change semantics (pragmas are dropped by the
lexer, ``AT %I...`` addresses are recorded but unused).  Anything that would
silently change behaviour instead raises a located syntax error.
"""

from __future__ import annotations

from . import ast_nodes as A
from .errors import IecSyntaxError
from .lexer import TYPE_NAMES, Token, tokenize

_SECTION_BY_KEYWORD = {
    "VAR": "local",
    "VAR_INPUT": "input",
    "VAR_OUTPUT": "output",
    "VAR_IN_OUT": "inout",
    "VAR_TEMP": "temp",
    "VAR_STAT": "static",
    "VAR_EXTERNAL": "external",
    "VAR_GLOBAL": "global",
}

# Access and inheritance modifiers CODESYS puts on a POU declaration.  They
# carry no meaning for a single-instance test, but rejecting them would make a
# verbatim CODESYS export unparseable.
_MODIFIERS = frozenset({
    "FINAL", "ABSTRACT", "PRIVATE", "PUBLIC", "PROTECTED", "INTERNAL",
})


# Loosest binding first.  Each entry maps token text to the AST node to build.
_BINARY_LEVELS: tuple[dict[str, str], ...] = (
    {"OR": "OR"},
    {"XOR": "XOR"},
    {"AND": "AND", "&": "AND"},
    {"=": "=", "<>": "<>"},
    {"<": "<", ">": ">", "<=": "<=", ">=": ">="},
    {"+": "+", "-": "-"},
    {"*": "*", "/": "/", "MOD": "MOD"},
)

_UNARY_OPS = {"NOT", "-", "+"}

# Operators that also exist as extensible standard functions.
_FUNCTION_LIKE_OPERATORS = frozenset({"AND", "OR", "XOR", "NOT", "MOD"})

# Keywords that can never begin a statement.  A statement list stops at these
# even when they are not one of its own terminators, so that a construct left
# unterminated reports "expected END_IF" instead of a confusing cascade of
# "expected an expression" errors.
_BLOCK_END_KEYWORDS = frozenset(
    """
    END_IF ELSIF ELSE END_CASE END_FOR END_WHILE END_REPEAT UNTIL
    END_FUNCTION_BLOCK END_FUNCTION END_PROGRAM END_VAR END_TYPE
    END_STRUCT END_UNION END_INTERFACE END_METHOD END_PROPERTY
    """.split()
)


class Parser:
    def __init__(self, tokens: list[Token], filename: str = "<st>") -> None:
        self.tokens = tokens
        self.filename = filename
        self.i = 0

    # -- token helpers -----------------------------------------------------

    @property
    def tok(self) -> Token:
        return self.tokens[self.i]

    def peek(self, offset: int = 0) -> Token:
        j = min(self.i + offset, len(self.tokens) - 1)
        return self.tokens[j]

    def _pos(self, tok: Token | None = None) -> A.Pos:
        tok = tok or self.tok
        return A.Pos(tok.line, tok.col, self.filename)

    def error(self, message: str, tok: Token | None = None) -> IecSyntaxError:
        tok = tok or self.tok
        found = "end of file" if tok.kind == "eof" else repr(tok.raw or tok.value)
        return IecSyntaxError(
            f"{message}, found {found}", source=self.filename, line=tok.line, col=tok.col
        )

    def at_kw(self, *words: str) -> bool:
        t = self.tok
        return t.kind == "kw" and t.value in words

    def at_op(self, *ops: str) -> bool:
        t = self.tok
        return t.kind == "op" and t.value in ops

    def at_op_peek(self, op: str, offset: int) -> bool:
        t = self.peek(offset)
        return t.kind == "op" and t.value == op

    def accept_kw(self, *words: str) -> bool:
        if self.at_kw(*words):
            self.i += 1
            return True
        return False

    def accept_op(self, *ops: str) -> bool:
        if self.at_op(*ops):
            self.i += 1
            return True
        return False

    def expect_kw(self, word: str) -> Token:
        if not self.at_kw(word):
            raise self.error(f"expected '{word}'")
        tok = self.tok
        self.i += 1
        return tok

    def expect_op(self, op: str) -> Token:
        if not self.at_op(op):
            raise self.error(f"expected '{op}'")
        tok = self.tok
        self.i += 1
        return tok

    def expect_ident(self) -> tuple[str, Token]:
        t = self.tok
        if t.kind != "ident":
            raise self.error("expected an identifier")
        self.i += 1
        return str(t.value), t

    # -- entry point -------------------------------------------------------

    def parse_unit(self, name: str = "<st>") -> A.SourceUnit:
        unit = A.SourceUnit(name=name, pos=A.Pos(1, 1, self.filename))
        while self.tok.kind != "eof":
            if self.at_kw(*_MODIFIERS):
                self.i += 1
                continue
            if self.at_kw("FUNCTION_BLOCK", "FUNCTION", "PROGRAM"):
                unit.pous.append(self.parse_pou())
            elif self.at_kw("TYPE"):
                unit.types.extend(self.parse_type_section())
            elif self.at_kw("VAR_GLOBAL"):
                unit.globals.extend(self.parse_var_section())
            elif self.at_kw("CLASS", "INTERFACE", "METHOD", "PROPERTY", "NAMESPACE", "ACTION"):
                # CODESYS' object-oriented extensions.  Failing loudly with a
                # clear message beats a confusing cascade of syntax errors, and
                # the feature set is documented as out of scope.
                raise self.error(
                    f"'{self.tok.value}' (CODESYS object-oriented extension) is not supported"
                )
            else:
                raise self.error("expected FUNCTION, FUNCTION_BLOCK, PROGRAM, TYPE or VAR_GLOBAL")
        return unit

    # -- declarations ------------------------------------------------------

    def parse_pou(self) -> A.PouDef:
        start = self.tok
        kind = str(self.tok.value)
        self.i += 1
        # CODESYS writes "FUNCTION_BLOCK FINAL AnalogInput" for a block that
        # cannot be derived from.  It changes nothing about how one instance
        # behaves, so skip it rather than fail on a verbatim export.  A modifier
        # is only a modifier when a name follows it, so a block genuinely
        # called Final still parses.
        j = self.i
        while self.tokens[j].kind == "kw" and self.tokens[j].value in _MODIFIERS:
            j += 1
        if j > self.i and self.tokens[j].kind == "ident":
            self.i = j
        name, _ = self.expect_ident()

        return_type = None
        if self.accept_op(":"):
            return_type = self.parse_type_spec()

        extends = None
        if self.accept_kw("EXTENDS"):
            extends, _ = self.expect_ident()

        implements: list[str] = []
        if self.accept_kw("IMPLEMENTS"):
            while True:
                base, _ = self.expect_ident()
                implements.append(base)
                if not self.accept_op(","):
                    break

        vars_: list[A.VarDecl] = []
        while self.at_kw(*_SECTION_BY_KEYWORD):
            vars_.extend(self.parse_var_section())

        body: list[A.Stmt] = []
        end_kw = {"FUNCTION_BLOCK": "END_FUNCTION_BLOCK", "FUNCTION": "END_FUNCTION", "PROGRAM": "END_PROGRAM"}[kind]
        while not self.at_kw(end_kw):
            if self.tok.kind == "eof":
                # CODESYS splits a project into one file per object and does not
                # write the closing keyword into the export.  A file that simply
                # stops has told us all it is going to, so end the POU there.
                # Nothing is lost: a body that stopped mid-statement fails to
                # parse on its own account, and the section order is fixed.
                return self._finish_pou(kind, name, return_type, extends, implements, vars_, body, start)
            stmt = self.parse_statement()
            if not isinstance(stmt, A.Empty):
                body.append(stmt)
        self.expect_kw(end_kw)
        self.accept_op(";")

        return self._finish_pou(kind, name, return_type, extends, implements, vars_, body, start)

    def _finish_pou(
        self,
        kind: str,
        name: str,
        return_type: A.TypeSpec | None,
        extends: str | None,
        implements: list[str],
        vars_: list[A.VarDecl],
        body: list[A.Stmt],
        start: Token,
    ) -> A.PouDef:
        return A.PouDef(
            kind=kind,
            name=name,
            return_type=return_type,
            vars=vars_,
            body=body,
            extends=extends,
            implements=implements,
            pos=self._pos(start),
        )

    def parse_var_section(self) -> list[A.VarDecl]:
        section_kw = str(self.tok.value)
        section = _SECTION_BY_KEYWORD[section_kw]
        self.i += 1

        constant = retain = False
        while True:
            if self.accept_kw("CONSTANT"):
                constant = True
            elif self.accept_kw("RETAIN", "PERSISTENT"):
                retain = True
            elif self.accept_kw("NON_RETAIN"):
                pass
            else:
                break

        decls: list[A.VarDecl] = []
        while not self.at_kw("END_VAR"):
            if self.tok.kind == "eof":
                raise self.error("expected 'END_VAR'")
            decls.append(self.parse_var_decl(section, constant=constant, retain=retain))
        self.expect_kw("END_VAR")
        self.accept_op(";")
        return decls

    def parse_var_decl(self, section: str, *, constant: bool = False, retain: bool = False) -> A.VarDecl:
        start = self.tok
        names: list[str] = []
        while True:
            name, _ = self.expect_ident()
            names.append(name)
            if not self.accept_op(","):
                break

        at = None
        if self.accept_kw("AT"):
            t = self.tok
            if t.kind not in ("address", "ident"):
                raise self.error("expected a direct address such as %IX0.0 after 'AT'")
            at = str(t.value)
            self.i += 1

        self.expect_op(":")
        type_spec = self.parse_type_spec()

        init = None
        if self.accept_op(":="):
            init = self.parse_expression()

        self.expect_op(";")
        return A.VarDecl(
            names=names,
            type=type_spec,
            section=section,
            init=init,
            constant=constant,
            retain=retain,
            at=at,
            pos=self._pos(start),
        )

    def parse_type_spec(self) -> A.TypeSpec:
        pos = self._pos()

        if self.accept_kw("ARRAY"):
            self.expect_op("[")
            ranges: list[tuple[A.Expr, A.Expr]] = []
            while True:
                lo = self.parse_expression()
                self.expect_op("..")
                hi = self.parse_expression()
                ranges.append((lo, hi))
                if not self.accept_op(","):
                    break
            self.expect_op("]")
            self.expect_kw("OF")
            elem = self.parse_type_spec()
            return A.TypeSpec(name="ARRAY", ranges=ranges, elem=elem, pos=pos)

        if self.accept_kw("REFERENCE"):
            self.expect_kw("TO")
            return A.TypeSpec(name="REFERENCE", elem=self.parse_type_spec(), ref=True, pos=pos)

        if self.accept_kw("POINTER"):
            self.expect_kw("TO")
            return A.TypeSpec(name="POINTER", elem=self.parse_type_spec(), ptr=1, pos=pos)

        if self.at_kw("STRING", "WSTRING"):
            name = str(self.tok.value)
            self.i += 1
            length = None
            if self.accept_op("["):
                length = self._parse_const_int("string length")
                self.expect_op("]")
            return A.TypeSpec(name=name, length=length, pos=pos)

        if self.at_kw("STRUCT", "UNION"):
            name = str(self.tok.value)
            end_kw = "END_STRUCT" if name == "STRUCT" else "END_UNION"
            self.i += 1
            fields: list[A.VarDecl] = []
            while not self.at_kw(end_kw):
                if self.tok.kind == "eof":
                    raise self.error(f"expected '{end_kw}'")
                fields.append(self.parse_var_decl("local"))
            self.expect_kw(end_kw)
            return A.TypeSpec(name=name, fields=fields, pos=pos)

        if self.at_enum_spec():
            # An enumeration given inline rather than as a named DUT:
            # ``Command : (NO_CMD := 0, DROP_SHIFT := 2);``.  CODESYS emits this
            # for block-private state, and its type has no name to refer to.
            return self.parse_enum_spec()

        t = self.tok
        if t.kind == "kw" and t.value in TYPE_NAMES:
            self.i += 1
            return A.TypeSpec(name=str(t.value), pos=pos)
        if t.kind == "ident":
            self.i += 1
            return A.TypeSpec(name=str(t.value), pos=pos)
        raise self.error("expected a type name")

    def _parse_const_int(self, what: str) -> int:
        t = self.tok
        if t.kind != "int":
            raise self.error(f"expected an integer {what}")
        self.i += 1
        return int(t.value)  # type: ignore[arg-type]

    def parse_type_section(self) -> list[A.TypeAlias | A.StructDef | A.EnumDef]:
        self.expect_kw("TYPE")
        out: list[A.TypeAlias | A.StructDef | A.EnumDef] = []
        while not self.at_kw("END_TYPE"):
            if self.tok.kind == "eof":
                raise self.error("expected 'END_TYPE'")
            name, start = self.expect_ident()
            self.expect_op(":")

            if self.at_enum_spec():
                spec = self.parse_enum_spec()
                out.append(
                    A.EnumDef(name=name, members=spec.members or [], base=spec.base,
                              pos=self._pos(start))
                )
            elif self.at_kw("STRUCT"):
                spec = self.parse_type_spec()
                out.append(A.StructDef(name=name, fields=spec.fields or [], pos=self._pos(start)))
            else:
                out.append(A.TypeAlias(name=name, type=self.parse_type_spec(), pos=self._pos(start)))
            # A STRUCT or enum already ends in a closing keyword; CODESYS omits
            # the semicolon there, so accept it either way.  A plain alias has
            # no terminator of its own and does need one.
            if isinstance(out[-1], A.TypeAlias):
                self.expect_op(";")
            else:
                self.accept_op(";")
        self.expect_kw("END_TYPE")
        self.accept_op(";")
        return out

    def at_enum_spec(self) -> bool:
        """Whether an enumeration body starts here.

        Three shapes reach this: ``(A, B)``, ``INT (A, B)`` and, where the
        enumeration has a user-defined base, ``T_Base (A, B)``.
        """
        if self.at_op("("):
            return True
        t = self.tok
        nxt = self.peek(1)
        return nxt.kind == "op" and nxt.value == "(" and (
            t.kind == "ident" or (t.kind == "kw" and t.value in TYPE_NAMES)
        )

    def parse_enum_spec(self) -> A.TypeSpec:
        """An enumeration body, with or without an explicit base type.

        The base is taken as a plain type name: going through
        :meth:`parse_type_spec` would see the same ``NAME (`` again and recurse.
        """
        pos = self._pos()
        base = None
        if not self.at_op("("):
            t = self.tok
            if not (t.kind == "ident" or (t.kind == "kw" and t.value in TYPE_NAMES)):
                raise self.error("expected a base type name before '('")
            base = A.TypeSpec(name=str(t.value), pos=self._pos(t))
            self.i += 1
        self.expect_op("(")
        members: list[A.EnumMember] = []
        while True:
            mname, mtok = self.expect_ident()
            value = None
            if self.accept_op(":="):
                value = self.parse_expression()
            members.append(A.EnumMember(name=mname, value=value, pos=self._pos(mtok)))
            if not self.accept_op(","):
                break
        self.expect_op(")")
        return A.TypeSpec(name="ENUM", members=members, base=base, pos=pos)

    # -- statements --------------------------------------------------------

    def parse_statement_list(self, *terminators: str) -> list[A.Stmt]:
        body: list[A.Stmt] = []
        while not (
            self.tok.kind == "eof"
            or self.at_kw(*terminators)
            or (self.tok.kind == "kw" and self.tok.value in _BLOCK_END_KEYWORDS)
        ):
            stmt = self.parse_statement()
            # Stray ';' carries no semantics, so it is consumed but not kept.
            if not isinstance(stmt, A.Empty):
                body.append(stmt)
        return body

    def parse_statement(self) -> A.Stmt:
        pos = self._pos()

        if self.accept_op(";"):
            return A.Empty(pos=pos)

        if self.at_kw("IF"):
            return self._parse_if()
        if self.at_kw("CASE"):
            return self._parse_case()
        if self.at_kw("FOR"):
            return self._parse_for()
        if self.at_kw("WHILE"):
            return self._parse_while()
        if self.at_kw("REPEAT"):
            return self._parse_repeat()
        if self.accept_kw("EXIT"):
            self.expect_op(";")
            return A.Exit(pos=pos)
        if self.accept_kw("CONTINUE"):
            self.expect_op(";")
            return A.Continue(pos=pos)
        if self.accept_kw("RETURN"):
            self.expect_op(";")
            return A.Return(pos=pos)

        expr = self.parse_expression()

        if self.accept_op(":="):
            value = self.parse_expression()
            self.expect_op(";")
            return A.Assignment(target=expr, value=value, pos=pos)

        if self.tok.kind == "ident" and self.tok.value == "REF" and self.at_op_peek("=", 1):
            self.i += 1
            value = self.parse_expression()
            self.expect_op(";")
            return A.RefAssignment(target=expr, value=value, pos=pos)

        if isinstance(expr, A.Call):
            self.expect_op(";")
            return A.CallStatement(call=expr, pos=pos)

        raise self.error("expected ':=' or a function-block invocation after the expression")

    def _parse_if(self) -> A.If:
        pos = self._pos()
        self.expect_kw("IF")
        branches: list[tuple[A.Expr, list[A.Stmt]]] = []
        cond = self.parse_expression()
        self.expect_kw("THEN")
        branches.append((cond, self.parse_statement_list("ELSIF", "ELSE", "END_IF")))
        while self.accept_kw("ELSIF"):
            cond = self.parse_expression()
            self.expect_kw("THEN")
            branches.append((cond, self.parse_statement_list("ELSIF", "ELSE", "END_IF")))
        orelse = None
        if self.accept_kw("ELSE"):
            orelse = self.parse_statement_list("END_IF")
        self.expect_kw("END_IF")
        self.accept_op(";")
        return A.If(branches=branches, orelse=orelse, pos=pos)

    def _parse_case(self) -> A.Case:
        pos = self._pos()
        self.expect_kw("CASE")
        selector = self.parse_expression()
        self.expect_kw("OF")

        arms: list[A.CaseArm] = []
        orelse = None
        while not self.at_kw("END_CASE"):
            if self.tok.kind == "eof":
                raise self.error("expected 'END_CASE'")
            if self.accept_kw("ELSE"):
                orelse = self.parse_statement_list("END_CASE")
                break

            arm_pos = self._pos()
            labels = self._parse_labels()
            body: list[A.Stmt] = []
            # A CASE arm has no closing keyword: it ends where the next arm's
            # label begins.  Labels are told apart from statements by
            # speculative parsing, since no statement has the shape ``expr :``
            # (assignment uses ``:=`` and invocations use parentheses).
            while not (self.at_kw("ELSE", "END_CASE") or self.tok.kind == "eof"):
                if self._labels_follow():
                    break
                stmt = self.parse_statement()
                if not isinstance(stmt, A.Empty):
                    body.append(stmt)
            arms.append(A.CaseArm(labels=labels, body=body, pos=arm_pos))

        self.expect_kw("END_CASE")
        self.accept_op(";")
        return A.Case(selector=selector, arms=arms, orelse=orelse, pos=pos)

    def _parse_labels(self) -> list[A.CaseLabel]:
        labels: list[A.CaseLabel] = []
        while True:
            lpos = self._pos()
            lo = self.parse_expression()
            hi = None
            if self.accept_op(".."):
                hi = self.parse_expression()
            labels.append(A.CaseLabel(lo=lo, hi=hi, pos=lpos))
            if not self.accept_op(","):
                break
        self.expect_op(":")
        return labels

    def _labels_follow(self) -> bool:
        """Return True if the upcoming tokens are a CASE label, without consuming."""
        save = self.i
        try:
            self._parse_labels()
        except IecSyntaxError:
            self.i = save
            return False
        self.i = save
        return True

    def _parse_for(self) -> A.For:
        pos = self._pos()
        self.expect_kw("FOR")
        var = self.parse_expression()
        self.expect_op(":=")
        start = self.parse_expression()
        self.expect_kw("TO")
        stop = self.parse_expression()
        step = None
        if self.accept_kw("BY"):
            step = self.parse_expression()
        self.expect_kw("DO")
        body = self.parse_statement_list("END_FOR")
        self.expect_kw("END_FOR")
        self.accept_op(";")
        return A.For(var=var, start=start, stop=stop, step=step, body=body, pos=pos)

    def _parse_while(self) -> A.While:
        pos = self._pos()
        self.expect_kw("WHILE")
        cond = self.parse_expression()
        self.expect_kw("DO")
        body = self.parse_statement_list("END_WHILE")
        self.expect_kw("END_WHILE")
        self.accept_op(";")
        return A.While(cond=cond, body=body, pos=pos)

    def _parse_repeat(self) -> A.Repeat:
        pos = self._pos()
        self.expect_kw("REPEAT")
        body = self.parse_statement_list("UNTIL")
        self.expect_kw("UNTIL")
        cond = self.parse_expression()
        self.expect_kw("END_REPEAT")
        self.accept_op(";")
        return A.Repeat(body=body, cond=cond, pos=pos)

    # -- expressions -------------------------------------------------------

    def parse_expression(self) -> A.Expr:
        return self._parse_binary(0)

    def _parse_binary(self, level: int) -> A.Expr:
        if level >= len(_BINARY_LEVELS):
            return self._parse_unary()
        left = self._parse_binary(level + 1)
        table = _BINARY_LEVELS[level]
        while True:
            t = self.tok
            key = str(t.value) if t.kind in ("op", "kw") else None
            if key is None or key not in table:
                return left
            pos = self._pos(t)
            self.i += 1
            right = self._parse_binary(level + 1)
            left = A.Binary(op=table[key], left=left, right=right, pos=pos)

    def _parse_unary(self) -> A.Expr:
        t = self.tok
        key = str(t.value) if t.kind in ("op", "kw") else None
        if key in _UNARY_OPS:
            pos = self._pos(t)
            self.i += 1
            operand = self._parse_unary()
            return A.Unary(op=key, operand=operand, pos=pos)
        return self._parse_power()

    def _parse_power(self) -> A.Expr:
        base = self._parse_postfix()
        if self.at_op("**"):
            pos = self._pos()
            self.i += 1
            # right-associative, and the exponent may carry a unary sign
            exponent = self._parse_unary()
            return A.Binary(op="**", left=base, right=exponent, pos=pos)
        return base

    def _parse_postfix(self) -> A.Expr:
        expr = self._parse_primary()
        while True:
            if self.at_op("."):
                pos = self._pos()
                self.i += 1
                if self.tok.kind == "int":
                    # Bit access: OUT.Status.3 is bit 3 of the DWORD.  It is a
                    # run-time concern which operand the index lands on, so it
                    # is kept distinct from a struct member of the same name.
                    number = self.tok.value
                    self.i += 1
                    expr = A.BitAccess(base=expr, index=A.Literal(
                        kind="int", value=number, pos=pos), pos=pos)
                else:
                    name, _ = self.expect_ident()
                    expr = A.FieldAccess(base=expr, name=name, pos=pos)
            elif self.at_op("["):
                pos = self._pos()
                self.i += 1
                indices: list[A.Expr] = []
                # Supporting a comma-separated list here (rather than only the
                # standard single index) makes multi-dimensional ARRAY access
                # work for libraries that use CODESYS' relaxed form.
                while True:
                    indices.append(self.parse_expression())
                    if not self.accept_op(","):
                        break
                self.expect_op("]")
                expr = A.IndexAccess(base=expr, indices=indices, pos=pos)
            elif self.at_op("^"):
                pos = self._pos()
                self.i += 1
                expr = A.Deref(base=expr, pos=pos)
            elif self.at_op("(") and isinstance(expr, (A.VarRef, A.FieldAccess, A.Deref)):
                expr = self._parse_call_args(expr)
            else:
                return expr

    def _parse_call_args(self, callee: A.Expr) -> A.Call:
        pos = self._pos()
        self.expect_op("(")
        args: list[A.Arg] = []
        while not self.at_op(")"):
            args.append(self._parse_arg())
            if not self.accept_op(","):
                break
        self.expect_op(")")
        return A.Call(callee=callee, args=args, pos=pos)

    def _parse_arg(self) -> A.Arg:
        pos = self._pos()
        t = self.tok
        # Named input argument:  ident := expr
        if t.kind == "ident" and self.peek(1).kind == "op" and self.peek(1).value == ":=":
            name = str(t.value)
            self.i += 2
            return A.Arg(value=self.parse_expression(), name=name, pos=pos)

        value = self.parse_expression()
        # Output binding:  outParam => variable.  The parameter name sits on the
        # left and the assignment target on the right, so the two are split
        # into Arg.name and Arg.value respectively.
        if self.accept_op("=>"):
            target = self.parse_expression()
            param = value.name if isinstance(value, A.VarRef) else None
            if param is None:
                raise self.error("the left side of '=>' must be an output parameter name")
            return A.Arg(value=target, name=param, is_output=True, pos=pos)
        return A.Arg(value=value, pos=pos)

    def _parse_primary(self) -> A.Expr:
        t = self.tok
        pos = self._pos(t)

        if t.kind in ("int", "real", "bool", "str", "wstr", "time", "date", "tod", "dt"):
            self.i += 1
            return A.Literal(
                kind=t.kind, value=t.value, type_name=t.type_name, pos=pos
            )

        if t.kind == "kw" and t.value in ("TRUE", "FALSE"):
            self.i += 1
            return A.Literal(kind="bool", value=t.value == "TRUE", pos=pos)

        if t.kind == "ident":
            self.i += 1
            name = str(t.value)
            if self.at_op("#"):
                self.i += 1
                member, _ = self.expect_ident()
                return A.EnumValue(type_name=name, member=member, pos=pos)
            return A.VarRef(name=name, pos=pos)

        # A type keyword used as an operand: SIZEOF(INT), or a conversion
        # written in the CODESYS function style such as INT(x).
        if t.kind == "kw" and t.value in TYPE_NAMES:
            self.i += 1
            return A.VarRef(name=str(t.value), pos=pos)

        # IEC 61131-3 defines AND, OR, XOR and NOT as extensible standard
        # *functions* as well as operators, so ``AND(a, b)`` must parse.
        if t.kind == "kw" and t.value in _FUNCTION_LIKE_OPERATORS and self.at_op_peek("(", 1):
            self.i += 1
            return A.VarRef(name=str(t.value), pos=pos)

        if self.accept_op("("):
            expr = self.parse_expression()
            self.expect_op(")")
            return expr

        if self.at_op("["):
            self.i += 1
            items: list[A.Expr] = []
            while not self.at_op("]"):
                items.append(self.parse_expression())
                if not self.accept_op(","):
                    break
            self.expect_op("]")
            return A.ArrayLiteral(items=items, pos=pos)

        raise self.error("expected an expression")


def parse_text(text: str, filename: str = "<st>") -> A.SourceUnit:
    """Tokenize and parse *text* into a :class:`~iecst.ast_nodes.SourceUnit`."""
    return Parser(tokenize(text, filename), filename).parse_unit(filename)
