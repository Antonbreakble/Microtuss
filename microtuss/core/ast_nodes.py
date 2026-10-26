"""AST node definitions for IEC 61131-3 Structured Text.

The tree is intentionally plain: dataclasses with no behaviour, so that the
parser, the type checker and the interpreter can each walk it without hidden
coupling.  Every node carries a :class:`Pos` so runtime errors can point at the
line of the original ``.st`` file.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Pos:
    line: int
    col: int
    source: str = "<st>"

    def __str__(self) -> str:
        return f"{self.source}:{self.line}:{self.col}"


# --- base markers ---------------------------------------------------------


class Node:
    """Marker base class for every AST node."""

    pos: Pos | None


class Expr(Node):
    """Marker base class for expressions."""


class Stmt(Node):
    """Marker base class for statements."""


# --- types ----------------------------------------------------------------


@dataclass
class TypeSpec(Node):
    """A type reference appearing in a declaration.

    ``name`` is the canonical upper-case type name.  ``ARRAY``, ``STRUCT`` and
    ``REFERENCE TO`` carry their payload in the remaining fields.
    """

    name: str
    ranges: list[tuple[Expr, Expr]] = field(default_factory=list)
    elem: TypeSpec | None = None
    fields: list[VarDecl] | None = None
    members: list[EnumMember] | None = None
    base: TypeSpec | None = None
    length: int | None = None
    ref: bool = False
    ptr: int = 0
    pos: Pos | None = None

    def __str__(self) -> str:
        if self.ptr:
            return f"POINTER TO {self.elem}"
        if self.ref:
            return f"REFERENCE TO {self.elem}"
        if self.name == "ARRAY":
            dims = ", ".join(f"{lo}..{hi}" for lo, hi in [(a, b) for a, b in self.ranges])
            return f"ARRAY[{dims}] OF {self.elem}"
        if self.name == "STRUCT":
            return "STRUCT"
        if self.length is not None:
            return f"{self.name}[{self.length}]"
        return self.name


@dataclass
class VarDecl(Node):
    names: list[str]
    type: TypeSpec
    section: str = "local"  # input | output | inout | local | temp | static | external | global
    init: Expr | None = None
    constant: bool = False
    retain: bool = False
    at: str | None = None
    pos: Pos | None = None


# --- expressions ----------------------------------------------------------


@dataclass
class Literal(Expr):
    kind: str  # int | real | bool | str | wstr | time | date | tod | dt | null
    value: object
    type_name: str | None = None
    pos: Pos | None = None


@dataclass
class VarRef(Expr):
    """A bare identifier: local, parameter or global variable."""

    name: str
    pos: Pos | None = None


@dataclass
class EnumValue(Expr):
    """A qualified enumeration value such as ``Color#Red``."""

    type_name: str
    member: str
    pos: Pos | None = None


@dataclass
class FieldAccess(Expr):
    base: Expr
    name: str
    pos: Pos | None = None


@dataclass
class IndexAccess(Expr):
    base: Expr
    indices: list[Expr]
    pos: Pos | None = None


@dataclass
class BitAccess(Expr):
    """Bit access on a bit-string or integer: ``Status.3``, ``wFlags.15``."""

    base: Expr
    index: Expr
    pos: Pos | None = None


@dataclass
class Deref(Expr):
    """Pointer dereference ``p^``.  Also produced for ``p^.field``."""

    base: Expr
    pos: Pos | None = None


@dataclass
class Unary(Expr):
    op: str  # '-' | 'NOT' | '+' | 'ADR'
    operand: Expr
    pos: Pos | None = None


@dataclass
class Binary(Expr):
    op: str
    left: Expr
    right: Expr
    pos: Pos | None = None


@dataclass
class Arg(Node):
    """One argument of a call.

    ``name`` is set for ``f(x := 1)``; ``is_output`` marks ``f(x => y)``, which
    is how an FB output parameter is bound to a variable.
    """

    value: Expr
    name: str | None = None
    is_output: bool = False
    pos: Pos | None = None


@dataclass
class ArrayLiteral(Expr):
    """Structured array initialiser: ``[1, 2, 3]``."""

    items: list[Expr]
    pos: Pos | None = None


@dataclass
class Call(Expr):
    """A function or function-block invocation.

    ``callee`` is the expression in front of the parentheses, so that both
    ``TON(...)`` and ``inst.Method(...)`` are represented uniformly.
    """

    callee: Expr
    args: list[Arg]
    type_name: str | None = None
    pos: Pos | None = None


# --- statements -----------------------------------------------------------


@dataclass
class Assignment(Stmt):
    target: Expr
    value: Expr
    pos: Pos | None = None


@dataclass
class RefAssignment(Stmt):
    """``p REF= q`` or ``p := REF(q)``."""

    target: Expr
    value: Expr
    pos: Pos | None = None


@dataclass
class CallStatement(Stmt):
    call: Call
    pos: Pos | None = None


@dataclass
class If(Stmt):
    branches: list[tuple[Expr, list[Stmt]]]
    orelse: list[Stmt] | None = None
    pos: Pos | None = None


@dataclass
class CaseLabel(Node):
    lo: Expr
    hi: Expr | None = None
    pos: Pos | None = None


@dataclass
class CaseArm(Node):
    labels: list[CaseLabel]
    body: list[Stmt]
    pos: Pos | None = None


@dataclass
class Case(Stmt):
    selector: Expr
    arms: list[CaseArm]
    orelse: list[Stmt] | None = None
    pos: Pos | None = None


@dataclass
class For(Stmt):
    var: Expr
    start: Expr
    stop: Expr
    step: Expr | None
    body: list[Stmt]
    pos: Pos | None = None


@dataclass
class While(Stmt):
    cond: Expr
    body: list[Stmt]
    pos: Pos | None = None


@dataclass
class Repeat(Stmt):
    body: list[Stmt]
    cond: Expr
    pos: Pos | None = None


@dataclass
class Exit(Stmt):
    pos: Pos | None = None


@dataclass
class Continue(Stmt):
    pos: Pos | None = None


@dataclass
class Return(Stmt):
    pos: Pos | None = None


@dataclass
class Empty(Stmt):
    pos: Pos | None = None


# --- top level ------------------------------------------------------------


@dataclass
class PouDef(Node):
    kind: str  # FUNCTION | FUNCTION_BLOCK | PROGRAM
    name: str
    return_type: TypeSpec | None = None
    vars: list[VarDecl] = field(default_factory=list)
    body: list[Stmt] = field(default_factory=list)
    extends: str | None = None
    implements: list[str] = field(default_factory=list)
    members: list[PouDef] = field(default_factory=list)
    pos: Pos | None = None

    def vars_in(self, *sections: str) -> list[VarDecl]:
        return [v for v in self.vars if v.section in sections]


@dataclass
class TypeAlias(Node):
    name: str
    type: TypeSpec
    pos: Pos | None = None


@dataclass
class StructDef(Node):
    name: str
    fields: list[VarDecl]
    pos: Pos | None = None


@dataclass
class EnumMember(Node):
    name: str
    value: Expr | None = None
    pos: Pos | None = None


@dataclass
class EnumDef(Node):
    name: str
    members: list[EnumMember]
    base: TypeSpec | None = None
    pos: Pos | None = None


@dataclass
class SourceUnit(Node):
    """One parsed ``.st`` file."""

    name: str
    pous: list[PouDef] = field(default_factory=list)
    types: list[TypeAlias | StructDef | EnumDef] = field(default_factory=list)
    globals: list[VarDecl] = field(default_factory=list)
    pos: Pos | None = None
