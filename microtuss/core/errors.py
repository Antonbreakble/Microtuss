"""Error types for the IEC 61131-3 Structured Text toolchain.

Every error carries an optional source location so that failures coming out of a
test run can be traced back to a line in the original ``.st`` file.
"""

from __future__ import annotations


class IecError(Exception):
    """Base class for every error raised by this package."""

    def __init__(
        self,
        message: str,
        *,
        source: str | None = None,
        line: int | None = None,
        col: int | None = None,
        pos: object | None = None,
    ) -> None:
        # ``pos`` is an ast_nodes.Pos; accepting it directly keeps the location
        # structured (source, line, column) rather than pre-formatted, so that
        # callers and editors can use the fields individually.
        if pos is not None:
            source = source if source is not None else getattr(pos, "source", None)
            line = line if line is not None else getattr(pos, "line", None)
            col = col if col is not None else getattr(pos, "col", None)
        self.message = message
        self.source = source
        self.line = line
        self.col = col
        super().__init__(self.format())

    def format(self) -> str:
        where = ""
        if self.source:
            where = self.source
            if self.line is not None:
                where += f":{self.line}"
                if self.col is not None:
                    where += f":{self.col}"
            where += ": "
        return f"{where}{self.message}"


class IecSyntaxError(IecError):
    """Raised by the lexer and the parser."""


class IecNameError(IecError):
    """Raised when an identifier cannot be resolved."""


class IecTypeError(IecError):
    """Raised when an operation is applied to incompatible types."""


class IecRuntimeError(IecError):
    """Raised when execution cannot continue (division by zero, bad index, ...)."""
