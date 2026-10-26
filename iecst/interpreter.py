"""The Structured Text interpreter.

Execution is scan-based.  A :class:`Runtime` holds the persistent state (FB
instances and the virtual clock); :meth:`Runtime.run` executes the body of the
POU under test once per scan with a fixed cycle time.  Time only advances
between scans, so timers are reproducible.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import ast_nodes as A
from .errors import IecError, IecNameError, IecRuntimeError, IecTypeError
from .model import Model, PouEntry
from .stdlib import STANDARD_FUNCTIONS, NativeFb, StdFunction, _div, _mod, format_time
from .values import (
    ANY,
    ARRAY,
    BITSTR,
    BOOL,
    BUILTIN_TYPES,
    DT,
    ENUM,
    FB,
    INT,
    POINTER,
    REAL,
    REFERENCE,
    STRING,
    TIME,
    UINT,
    ArrayValue,
    BitCell,
    Cell,
    FbInstance,
    IecType,
    PointerValue,
    StructValue,
    coerce,
    default_value,
    enum_member,
    parse_time as _parse_time,
    wrap_int,
)

NANOSECOND = 1

# Numeric rank, used to pick the result type of a mixed-type arithmetic op.
_RANK = {
    "BOOL": 0, "SINT": 1, "USINT": 1, "BYTE": 1,
    "INT": 2, "UINT": 2, "WORD": 2,
    "DINT": 3, "UDINT": 3, "DWORD": 3,
    "LINT": 4, "ULINT": 4, "LWORD": 4,
    "REAL": 5, "LREAL": 6,
}


#: Time literals are parsed by the value layer, so that ``"200ms"`` means the
#: same thing at the top level and nested inside a struct initialiser.
parse_time = _parse_time


# --- control-flow signals -------------------------------------------------


class _Break(Exception):
    pass


class _Continue(Exception):
    pass


class _Return(Exception):
    pass


# --- scope ----------------------------------------------------------------


class Frame:
    """One activation record: a POU invocation or a function call."""

    __slots__ = ("names", "entry", "instance", "parent", "constants")

    def __init__(
        self,
        names: dict[str, Cell],
        entry: PouEntry | None = None,
        instance: FbInstance | None = None,
        parent: "Frame | None" = None,
    ) -> None:
        self.names = names
        self.entry = entry
        self.instance = instance
        self.parent = parent
        # Named constants visible in this scope that are not variables: the
        # members of a POU-private enumeration, for instance.  They are kept
        # apart from ``names`` so nothing expects a Cell where there is an int.
        self.constants: dict[str, Any] = {}


@dataclass
class TraceEvent:
    scan: int
    time_ns: int
    kind: str
    name: str
    value: Any


# --- the runtime ----------------------------------------------------------


class Runtime:
    """Owns the persistent state and drives execution scan by scan.

    ``pou`` is the name of the POU under test.  For a FUNCTION_BLOCK it is
    instantiated once and its body runs each scan; for a PROGRAM the same
    applies; for a FUNCTION the arguments come from :meth:`call`.
    """

    def __init__(
        self,
        model: Model,
        pou: str,
        *,
        cycle_time: str | int = "10ms",
        trace: bool = False,
    ) -> None:
        self.model = model
        self.cycle_ns = parse_time(cycle_time)
        self.now = 0
        self.scan = 0
        self.trace_enabled = trace
        self.events: list[TraceEvent] = []

        entry = model.pou(pou)
        if entry is None:
            known = ", ".join(sorted(model.pous)) or "none"
            raise IecNameError(f"no POU named '{pou}' (known: {known})")
        self.entry = entry
        self.instance = self.instantiate(entry.type)
        self._materialise_inouts()

    def _materialise_inouts(self) -> None:
        """Give the POU under test a backing cell for each VAR_IN_OUT.

        A VAR_IN_OUT must be bound by its caller; for the POU under test the
        caller is the test itself, so the runtime supplies storage that
        :meth:`bind` and :meth:`set` can reach.  Nested function blocks are
        deliberately left alone -- an unbound VAR_IN_OUT there is a real bug
        and still raises when the block runs.
        """
        for name, cell in self.instance.vars.items():
            if cell.type.kind == REFERENCE and cell.value is None:
                target = cell.type.target or self.model.types["ANY"]
                cell.value = Cell(default_value(target), target, name)

    def bind(self, name: str, value: Any = None) -> Cell:
        """Return the storage cell behind *name*, so two POUs can share it.

        Passing the returned cell as an argument to another instance is how a
        test wires one block's output into another block's VAR_IN_OUT.
        """
        cell = self._find_cell(name)
        if value is not None:
            self.write_cell(cell, value)
        return cell

    # -- instantiation -----------------------------------------------------

    def instantiate(self, t: IecType) -> FbInstance:
        """Create a fresh instance of function-block type *t*."""
        impl = t.impl
        if isinstance(impl, type) and issubclass(impl, NativeFb):
            return impl(t)

        entry = self.model.pou(t.name)
        if entry is None:
            raise IecTypeError(f"'{t.name}' is not a function block or program")

        inst = FbInstance(t)
        frame = Frame(inst.vars, entry=entry, instance=inst)
        for decl in entry.defn.vars:
            self._declare_cells(decl, inst.vars)
        self._add_inline_enum_members(entry, frame)
        # Initialisers run after every cell exists, so one may reference
        # another variable declared later in the same POU.
        for decl in entry.defn.vars:
            self._run_initialisers(decl, inst.vars, frame)
        inst.ctx = frame
        return inst

    def _add_inline_enum_members(self, entry: PouEntry, frame: Frame) -> None:
        """Expose the members of a POU's anonymous enumerations by bare name.

        A block that declares ``Command : (NO_CMD := 0, SET_SHIFT := 1)``
        writes ``CASE stCmd OF SET_SHIFT:`` -- there is no type name to qualify
        them with, so the members belong to the POU's own scope.
        """
        for decl in entry.defn.vars:
            if decl.type.members is None:
                continue
            t = self.model.resolve_type(decl.type)
            for name, ordinal in (t.members or {}).items():
                frame.constants.setdefault(name, ordinal)

    def _declare_cells(self, decl: A.VarDecl, into: dict[str, Cell]) -> None:
        t = self.model.resolve_type(decl.type)
        if decl.section == "inout":
            t = IecType(t.describe(), REFERENCE, target=t)
        for name in decl.names:
            if t.kind == FB and name not in into:
                child = self.instantiate(t)
                into[name] = Cell(child, t, name)
            else:
                into[name] = Cell(default_value(t), t, name)

    def _run_initialisers(self, decl: A.VarDecl, into: dict[str, Cell], frame: Frame) -> None:
        if decl.init is None:
            return
        for name in decl.names:
            if name not in into:
                continue
            if isinstance(decl.init, A.ArrayLiteral):
                into[name].value = self._build_array(decl.init, into[name].type, frame)
            else:
                into[name].set(self.eval(decl.init, frame))

    def _build_array(self, node: A.ArrayLiteral, t: IecType, frame: Frame) -> ArrayValue:
        if t.kind != ARRAY:
            raise IecTypeError("array initialiser used for a non-array type")
        value = ArrayValue(t)
        value.build([self.eval(item, frame) for item in node.items])
        return value

    # -- symbol lookup -----------------------------------------------------

    def lookup(self, name: str, frame: Frame | None) -> Any:
        f = frame
        while f is not None:
            if name in f.names:
                return f.names[name]
            if name in f.constants:
                return f.constants[name]
            f = f.parent
        cell = self.model.globals.get(name)
        if cell is not None:
            return cell
        entry = self.model.pou(name)
        if entry is not None:
            return entry
        fn = STANDARD_FUNCTIONS.get(name) or self.model.stdlib.get(name)
        if fn is not None:
            return fn
        t = self.model.type_of(name)
        if t is not None:
            return t
        raise IecNameError(f"undeclared identifier '{name}'")

    def read_cell(self, cell: Cell, pos: A.Pos | None = None) -> Any:
        """Read through VAR_IN_OUT / REFERENCE indirection.

        Only REFERENCE dereferences implicitly: reading a POINTER variable
        yields the address itself, and reaching the pointee takes an explicit
        ``^``.
        """
        if cell.type.kind == REFERENCE:
            target = cell.value
            if target is None:
                raise IecRuntimeError(
                    f"VAR_IN_OUT parameter '{cell.name}' was not bound by the caller",
                    pos=pos,
                )
            if isinstance(target, Cell):
                return target.get()
        return cell.get()

    def write_cell(self, cell: Cell, value: Any, pos: A.Pos | None = None) -> None:
        """Write through VAR_IN_OUT / REFERENCE indirection."""
        source = describe_pos(pos)
        if cell.type.kind == REFERENCE:
            target = cell.value
            if target is None:
                raise IecRuntimeError(
                    f"VAR_IN_OUT parameter '{cell.name}' was not bound by the caller",
                    pos=pos,
                )
            if isinstance(target, Cell):
                cell = target
        if cell.type.kind in (BITSTR, INT, UINT):
            # Writing an integer into a bit string, or a bit string into an
            # integer, means reinterpreting the same bits on a controller:
            # ``Status := 2#10`` leaves the status word equal to 2.  Going
            # through BOOL would make it 1 and silently lose the payload.
            cell.value = wrap_int(int(value), cell.type)
            return
        cell.set(value, source=source)

    # -- lvalues -----------------------------------------------------------

    def _bit_view(self, node: A.BitAccess, frame: Frame) -> Cell:
        """A cell standing for one bit of a bit string or integer.

        ``OUT.Status.3`` has to be both readable and assignable, so this hands
        back a view onto the parent word rather than a copy of the bit.
        """
        base = self.resolve_cell(node.base, frame)
        if base is None:
            raise IecTypeError("cannot access a bit of this expression", pos=node.pos)
        t = base.type
        if t.kind == REFERENCE and isinstance(base.value, Cell):
            t = base.value.type
        if t.kind not in (BITSTR, INT, UINT, BOOL):
            raise IecTypeError(
                f"cannot access bit {self.eval(node.index, frame)} of {t.describe()}",
                pos=node.pos,
            )
        index = int(self.eval(node.index, frame))
        bits = t.bits or 1
        if not 0 <= index < bits:
            raise IecTypeError(
                f"bit {index} is outside {t.describe()}", pos=node.pos
            )
        name = f"{base.name or '?'}.{index}"
        return BitCell(base, index, BUILTIN_TYPES["BOOL"], name)

    def resolve_cell(self, node: A.Expr, frame: Frame) -> Cell | None:
        """Return the storage cell *node* denotes, or None if it is not an lvalue."""
        if isinstance(node, A.VarRef):
            sym = self.lookup(node.name, frame)
            return sym if isinstance(sym, Cell) else None

        if isinstance(node, A.FieldAccess):
            base = self.resolve_cell(node.base, frame)
            if base is None:
                return None
            holder = self.read_cell(base, node.pos)
            if isinstance(holder, StructValue):
                return holder.cell(node.name, describe_pos(node.pos))
            if isinstance(holder, FbInstance):
                if node.name in holder.vars:
                    return holder.vars[node.name]
                raise IecNameError(
                    f"'{holder.type.name}' has no member '{node.name}'", pos=node.pos
                )
            raise IecTypeError(
                f"cannot access member '{node.name}' of a non-struct value", pos=node.pos
            )

        if isinstance(node, A.IndexAccess):
            base = self.resolve_cell(node.base, frame)
            if base is None:
                raise IecTypeError("cannot index this expression", pos=node.pos)
            holder = self.read_cell(base, node.pos)
            if not isinstance(holder, ArrayValue):
                raise IecTypeError("indexing a non-array value", pos=node.pos)
            indices = [int(self.eval(i, frame)) for i in node.indices]
            return holder.cell_at(indices, describe_pos(node.pos))

        if isinstance(node, A.BitAccess):
            return self._bit_view(node, frame)

        if isinstance(node, A.Deref):
            base = self.resolve_cell(node.base, frame)
            if base is None:
                return None
            ptr = self.read_cell(base, node.pos)
            # An unassigned pointer holds None rather than a PointerValue, so
            # both spellings of "no address" report the same way.
            if ptr is None or (isinstance(ptr, PointerValue) and ptr.cell is None):
                raise IecRuntimeError("dereference of a null pointer", pos=node.pos)
            if not isinstance(ptr, PointerValue):
                raise IecTypeError("dereference of a non-pointer value", pos=node.pos)
            return ptr.cell

        return None

    # -- expressions -------------------------------------------------------

    def eval(self, node: A.Expr, frame: Frame) -> Any:
        if isinstance(node, A.Literal):
            return self._literal_value(node)
        if isinstance(node, A.EnumValue):
            return self._enum_value(node)
        if isinstance(node, A.VarRef):
            sym = self.lookup(node.name, frame)
            if isinstance(sym, Cell):
                return self.read_cell(sym, node.pos)
            if isinstance(sym, FbInstance):
                return sym
            if isinstance(sym, PouEntry):
                raise IecNameError(
                    f"'{node.name}' is a {sym.defn.kind.lower()}, not a value", pos=node.pos
                )
            if isinstance(sym, StdFunction):
                raise IecNameError(
                    f"'{node.name}' is a function, not a value", pos=node.pos
                )
            return sym
        if isinstance(node, A.FieldAccess):
            cell = self.resolve_cell(node, frame)
            if cell is None:
                raise IecTypeError(
                    f"cannot read member '{node.name}'", pos=node.pos
                )
            return self.read_cell(cell, node.pos)
        if isinstance(node, A.IndexAccess):
            return self.read_cell(self.resolve_cell(node, frame), node.pos)  # type: ignore[arg-type]
        if isinstance(node, A.BitAccess):
            return self.read_cell(self._bit_view(node, frame), node.pos)
        if isinstance(node, A.Deref):
            cell = self.resolve_cell(node, frame)
            if cell is None:
                raise IecTypeError("cannot dereference this expression", pos=node.pos)
            return cell.value
        if isinstance(node, A.ArrayLiteral):
            return [self.eval(item, frame) for item in node.items]
        if isinstance(node, A.Unary):
            return self._eval_unary(node, frame)
        if isinstance(node, A.Binary):
            return self._eval_binary(node, frame)
        if isinstance(node, A.Call):
            return self.eval_call(node, frame)
        raise IecRuntimeError(f"cannot evaluate {type(node).__name__}", pos=node.pos)

    def _literal_value(self, node: A.Literal) -> Any:
        if node.kind == "int" and node.type_name:
            t = self.model.type_of(node.type_name)
            if t is not None and t.kind in (INT, UINT, ENUM):
                return wrap_int(int(node.value), t)
            if t is not None and t.kind == TIME:
                return int(node.value)
        if node.kind == "bool":
            return bool(node.value)
        return node.value

    def _enum_value(self, node: A.EnumValue) -> int:
        t = self.model.type_of(node.type_name)
        if t is None or t.kind != ENUM:
            raise IecNameError(f"'{node.type_name}' is not an enumeration", pos=node.pos)
        if node.member not in (t.members or {}):
            raise IecNameError(
                f"'{node.type_name}' has no member '{node.member}'", pos=node.pos
            )
        return (t.members or {})[node.member]

    def _eval_unary(self, node: A.Unary, frame: Frame) -> Any:
        operand = self.eval(node.operand, frame)
        if node.op == "NOT":
            t = self.static_type(node.operand, frame)
            if t is not None and t.kind == BOOL:
                return not operand
            return wrap_int(~int(operand), t or self.model.types["INT"])
        if node.op == "-":
            t = self.static_type(node.operand, frame)
            if isinstance(operand, float):
                return -operand
            return wrap_int(-int(operand), t or self.model.types["INT"])
        return operand

    def _eval_binary(self, node: A.Binary, frame: Frame) -> Any:
        op = node.op

        # Short-circuit the logical operators, as the standard requires.
        if op == "AND":
            left = self.eval(node.left, frame)
            if self._is_logical(node.left, frame, left):
                return bool(left) and bool(self.eval(node.right, frame))
        elif op == "OR":
            left = self.eval(node.left, frame)
            if self._is_logical(node.left, frame, left):
                return bool(left) or bool(self.eval(node.right, frame))

        left = self.eval(node.left, frame)
        right = self.eval(node.right, frame)

        lt = self.static_type(node.left, frame)
        rt = self.static_type(node.right, frame)

        if op in ("=", "<>", "<", ">", "<=", ">="):
            return self._compare(op, left, right)

        if op in ("AND", "OR", "XOR"):
            rt_ = self._arith_type(lt, rt)
            if rt_ is not None and rt_.kind == BOOL:
                return {"AND": left and right, "OR": left or right, "XOR": bool(left) ^ bool(right)}[op]
            a, b = int(left), int(right)
            result = {"AND": a & b, "OR": a | b, "XOR": a ^ b}[op]
            return wrap_int(result, rt_ or self.model.types["DINT"])

        if op in ("+", "-", "*", "/", "MOD", "**"):
            return self._arithmetic(op, left, right, lt, rt, node.pos)

        raise IecRuntimeError(f"unsupported operator '{op}'", pos=node.pos)

    def _is_logical(self, node: A.Expr, frame: Frame, value: Any) -> bool:
        t = self.static_type(node, frame)
        return (t is not None and t.kind == BOOL) or isinstance(value, bool)

    def _compare(self, op: str, left: Any, right: Any) -> bool:
        if isinstance(left, str) or isinstance(right, str):
            left, right = str(left), str(right)
        try:
            return {
                "=": left == right, "<>": left != right,
                "<": left < right, ">": left > right,
                "<=": left <= right, ">=": left >= right,
            }[op]
        except TypeError as exc:
            raise IecTypeError(f"cannot compare {type(left).__name__} with {type(right).__name__}") from exc

    def _arith_type(self, lt: IecType | None, rt: IecType | None) -> IecType | None:
        if lt is None:
            return rt
        if rt is None:
            return lt
        if lt.kind == ANY:
            return rt
        if rt.kind == ANY:
            return lt
        lr, rr = _RANK.get(lt.name, 3), _RANK.get(rt.name, 3)
        if lt.kind == REAL or rt.kind == REAL:
            # LREAL wins over REAL; otherwise the wider float.
            if lt.name == "LREAL" or rt.name == "LREAL":
                return self.model.types["LREAL"]
            return self.model.types["REAL"]
        if lt.kind in (TIME, DT) or rt.kind in (TIME, DT):
            return lt if lt.kind in (TIME, DT) else rt
        return lt if lr >= rr else rt

    def _arithmetic(
        self,
        op: str,
        left: Any,
        right: Any,
        lt: IecType | None,
        rt: IecType | None,
        pos: A.Pos | None,
    ) -> Any:
        result_type = self._arith_type(lt, rt)
        is_float = isinstance(left, float) or isinstance(right, float)

        if op == "MOD":
            if is_float:
                raise IecTypeError("MOD requires integer operands", pos=pos)
            divisor = int(right)
            if divisor == 0:
                raise IecRuntimeError("division by zero (MOD)", pos=pos)
            return wrap_int(_mod(int(left), divisor), result_type or self.model.types["INT"])

        if op == "/" and not is_float:
            divisor = int(right)
            # Checked here rather than inside _div so that the error carries the
            # position of the offending expression.
            if divisor == 0:
                raise IecRuntimeError("division by zero", pos=pos)
            return wrap_int(_div(int(left), divisor), result_type or self.model.types["INT"])

        if op == "**":
            exponent = float(right)
            try:
                if is_float:
                    return float(left) ** exponent
                value = int(round(float(left) ** exponent))
            except (OverflowError, ValueError) as exc:
                raise IecRuntimeError(f"power operation failed: {exc}", pos=pos) from exc
            return wrap_int(value, result_type or self.model.types["INT"])

        if is_float:
            if op == "+":
                return float(left) + float(right)
            if op == "-":
                return float(left) - float(right)
            if op == "*":
                return float(left) * float(right)
            if op == "/":
                if float(right) == 0.0:
                    raise IecRuntimeError("division by zero", pos=pos)
                return float(left) / float(right)

        if op == "+":
            return wrap_int(int(left) + int(right), result_type or self.model.types["INT"])
        if op == "-":
            return wrap_int(int(left) - int(right), result_type or self.model.types["INT"])
        if op == "*":
            return wrap_int(int(left) * int(right), result_type or self.model.types["INT"])
        raise IecRuntimeError(f"unsupported arithmetic operator '{op}'", pos=pos)

    # -- static typing -----------------------------------------------------

    def static_type(self, node: A.Expr, frame: Frame) -> IecType | None:
        """Best-effort static type of an expression.

        Only the result width matters here: it decides how integer arithmetic
        wraps.  Where the type cannot be determined, None is returned and the
        caller falls back to a DINT-wide result.
        """
        if isinstance(node, A.VarRef):
            sym = self.lookup(node.name, frame)
            if isinstance(sym, Cell):
                if sym.type.kind == REFERENCE and isinstance(sym.value, Cell):
                    return sym.value.type
                if sym.type.kind == POINTER:
                    return sym.type
                return sym.type
            if isinstance(sym, FbInstance):
                return sym.type
            return None
        if isinstance(node, A.FieldAccess):
            base = self.resolve_cell(node.base, frame)
            if base is not None:
                holder = self.read_cell(base, node.pos)
                if isinstance(holder, StructValue):
                    return (holder.type.fields or {}).get(node.name)
                if isinstance(holder, FbInstance) and node.name in holder.vars:
                    return holder.vars[node.name].type
            return None
        if isinstance(node, A.IndexAccess):
            base = self.resolve_cell(node.base, frame)
            if base is not None:
                t = base.type
                if t.kind == REFERENCE and base.value is not None:
                    t = base.value.type
                return t.element_type()
            return None
        if isinstance(node, A.Deref):
            base = self.resolve_cell(node.base, frame)
            if base is not None:
                t = base.type
                if t.kind in (POINTER, REFERENCE):
                    return t.target
            return None
        if isinstance(node, A.Literal):
            return self._literal_type(node)
        if isinstance(node, A.EnumValue):
            return self.model.type_of(node.type_name)
        if isinstance(node, A.Unary):
            return self.static_type(node.operand, frame)
        if isinstance(node, A.Binary):
            if node.op in ("=", "<>", "<", ">", "<=", ">=", "AND", "OR", "XOR"):
                lt = self.static_type(node.left, frame)
                if lt is not None and lt.kind == BOOL:
                    return self.model.types["BOOL"]
                return self._arith_type(lt, self.static_type(node.right, frame))
            return self._arith_type(
                self.static_type(node.left, frame), self.static_type(node.right, frame)
            )
        if isinstance(node, A.Call):
            return self._call_return_type(node, frame)
        return None

    def _literal_type(self, node: A.Literal) -> IecType | None:
        if node.type_name:
            return self.model.type_of(node.type_name)
        if node.kind == "bool":
            return self.model.types["BOOL"]
        if node.kind == "real":
            return self.model.types["REAL"]
        if node.kind == "str":
            return self.model.types["STRING"]
        if node.kind in ("time", "date", "tod", "dt"):
            return self.model.types[node.kind.upper()]
        if node.kind == "int":
            value = int(node.value)
            # An unsuffixed integer literal takes the narrowest of INT, DINT
            # and LINT that holds it.  SINT is deliberately not considered: a
            # literal is at least an INT, so ``100 + 100`` must not wrap.
            for name in ("INT", "DINT"):
                t = self.model.types[name]
                lo, hi = -(1 << (t.bits - 1)), (1 << (t.bits - 1)) - 1
                if lo <= value <= hi:
                    return t
            return self.model.types["LINT"]
        return None

    def _call_return_type(self, node: A.Call, frame: Frame) -> IecType | None:
        name = self._callee_name(node)
        if name is None:
            return None
        fn = STANDARD_FUNCTIONS.get(name) or self.model.stdlib.get(name)
        if isinstance(fn, StdFunction):
            if fn.ret == "SAME":
                return self.static_type(node.args[0].value, frame) if node.args else None
            return self.model.type_of(fn.ret)
        entry = self.model.pou(name)
        if entry is not None and entry.defn.kind == "FUNCTION":
            return entry.type.target
        try:
            sym = self.lookup(name, frame)
        except IecError:
            return None
        if isinstance(sym, FbInstance):
            return sym.type
        return None

    def _callee_name(self, node: A.Call) -> str | None:
        if isinstance(node.callee, A.VarRef):
            return node.callee.name
        return None

    # -- calls -------------------------------------------------------------

    def eval_call(self, node: A.Call, frame: Frame) -> Any:
        name = self._callee_name(node)

        # ADR/REF need the storage cell of their argument, not its value.
        if name in ("ADR", "REF"):
            if len(node.args) != 1:
                raise IecRuntimeError(f"{name} expects exactly one argument", pos=node.pos)
            cell = self.resolve_cell(node.args[0].value, frame)
            if cell is None:
                raise IecTypeError(f"{name} requires a variable", pos=node.pos)
            target = cell.value if cell.type.kind == REFERENCE and isinstance(cell.value, Cell) else cell
            return PointerValue(target, IecType(f"POINTER TO {cell.type.describe()}", POINTER, target=cell.type))

        if name == "SIZEOF":
            arg = node.args[0].value if node.args else None
            if isinstance(arg, A.VarRef):
                t = self.model.type_of(arg.name)
                if t is not None:
                    return max(1, (t.bits or 8) // 8) if t.kind != STRING else (t.length or 80)
            t = self.static_type(arg, frame) if arg is not None else None
            return max(1, ((t.bits or 8) // 8) if t else 1)

        symbol = None
        if name is not None:
            try:
                symbol = self.lookup(name, frame)
            except IecNameError:
                symbol = None

        if isinstance(symbol, FbInstance):
            return self._call_fb_instance(symbol, node, frame)

        if isinstance(symbol, Cell):
            holder = symbol.value
            if isinstance(holder, FbInstance):
                return self._call_fb_instance(holder, node, frame)
            # Inside a function, its own name refers to the result variable,
            # which shadows the FUNCTION declaration; recursion must still work.
            if name is not None:
                recurse = self.model.pou(name)
                if recurse is not None and recurse.defn.kind == "FUNCTION":
                    return self._call_function(recurse, node, frame)

        if isinstance(symbol, PouEntry):
            if symbol.defn.kind == "FUNCTION":
                return self._call_function(symbol, node, frame)
            # An FB type used as a value: build an implicit temporary instance.
            return self._call_fb_instance(self.instantiate(symbol.type), node, frame)

        if isinstance(symbol, StdFunction):
            return self._call_std(symbol, node, frame)

        if isinstance(symbol, IecType) and symbol.name in STANDARD_FUNCTIONS:
            return self._call_std(STANDARD_FUNCTIONS[symbol.name], node, frame)

        # Method-style access on an FB instance: ``inst.child(...)``
        if isinstance(node.callee, A.FieldAccess):
            base = self.resolve_cell(node.callee.base, frame)
            if base is not None:
                holder = self.read_cell(base, node.callee.pos)
                if isinstance(holder, FbInstance) and node.callee.name in holder.vars:
                    child = holder.vars[node.callee.name].value
                    if isinstance(child, FbInstance):
                        return self._call_fb_instance(child, node, frame)

        raise IecNameError(
            f"'{name}' is not a function or function block", pos=node.pos
        )

    # -- function-block invocation ----------------------------------------

    def _fb_interface(self, t: IecType) -> dict[str, tuple[str, IecType]]:
        """Map parameter name to (kind, type) in declaration order."""
        out: dict[str, tuple[str, IecType]] = {}
        impl = t.impl
        if isinstance(impl, type) and issubclass(impl, NativeFb):
            for pname, tname, kind in impl.decls:
                out[pname] = (kind, BUILTIN_TYPES[tname])
            return out
        entry = self.model.pou(t.name)
        if entry is None:
            return out
        for decl in entry.defn.vars:
            if decl.section not in ("input", "output", "inout"):
                continue
            for vname in decl.names:
                out[vname] = (decl.section, self.model.resolve_type(decl.type))
        return out

    def _call_fb_instance(self, inst: FbInstance, node: A.Call, frame: Frame) -> Any:
        interface = self._fb_interface(inst.type)
        bindings = self._bind_args(inst.vars, inst.type.name, interface, node, frame)
        self.execute_instance(inst)
        # ``Q => done`` copies the output back only after the body has run.
        for pname, target in bindings:
            cell = self.resolve_cell(target, frame)
            if cell is None:
                raise IecTypeError("output binding requires a variable", pos=target.pos)
            self.write_cell(cell, inst.vars[pname].value, target.pos)
        return inst

    def _bind_args(
        self,
        vars_map: dict[str, Cell],
        owner: str,
        interface: dict[str, tuple[str, IecType]],
        node: A.Call,
        caller: Frame,
    ) -> list[tuple[str, A.Expr]]:
        """Bind call arguments into *vars_map*, returning the output bindings.

        Named arguments are bound first so that positional ones skip whatever
        is already taken, which is how CODESYS resolves a mixed call.
        """
        named = {a.name: a for a in node.args if a.name is not None and not a.is_output}
        for pname, arg in named.items():
            if pname not in interface:
                raise IecNameError(f"'{owner}' has no parameter '{pname}'", pos=arg.pos)
            self._bind_one(vars_map, owner, pname, interface[pname], arg.value, caller)

        pending = iter(
            n for n, v in interface.items() if v[0] in ("input", "inout") and n not in named
        )
        for arg in node.args:
            if arg.name is not None or arg.is_output:
                continue
            pname = next(pending, None)
            if pname is None:
                raise IecTypeError(f"too many arguments for '{owner}'", pos=arg.pos)
            self._bind_one(vars_map, owner, pname, interface[pname], arg.value, caller)

        bindings: list[tuple[str, A.Expr]] = []
        for arg in node.args:
            if not arg.is_output:
                continue
            assert arg.name is not None  # guaranteed by the parser
            if arg.name not in interface:
                raise IecNameError(
                    f"'{owner}' has no output parameter '{arg.name}'", pos=arg.pos
                )
            bindings.append((arg.name, arg.value))
        return bindings

    def _bind_one(
        self,
        vars_map: dict[str, Cell],
        owner: str,
        pname: str,
        decl: tuple[str, IecType],
        value_expr: A.Expr,
        caller: Frame,
    ) -> None:
        kind, _ptype = decl
        cell = vars_map.get(pname)
        if cell is None:
            raise IecNameError(f"'{owner}' has no parameter '{pname}'")
        if kind == "inout" or cell.type.kind == REFERENCE:
            target = self.resolve_cell(value_expr, caller)
            if target is None:
                raise IecTypeError(
                    f"VAR_IN_OUT '{pname}' requires a variable, not an expression",
                    pos=value_expr.pos,
                )
            while target.type.kind == REFERENCE and isinstance(target.value, Cell):
                target = target.value
            cell.value = target
        else:
            cell.set(self.eval(value_expr, caller), source=describe_pos(value_expr.pos))

    def execute_instance(self, inst: FbInstance) -> None:
        """Run one scan of *inst*, be it a native or a user function block."""
        if isinstance(inst, NativeFb):
            inst.run(self)
            return
        entry = self.model.pou(inst.type.name)
        if entry is None:
            raise IecRuntimeError(f"no implementation for '{inst.type.name}'")
        frame = Frame(inst.vars, entry=entry, instance=inst, parent=None)
        if inst.ctx is not None:
            # Constants live on the frame built at instantiation; this frame is
            # rebuilt every scan, so carry them across.
            frame.constants = inst.ctx.constants
        try:
            self.execute_block(entry.defn.body, frame)
        except _Return:
            # RETURN leaves the current POU only.  Letting it escape here would
            # abort the body of whichever block invoked this instance.
            pass

    # -- function invocation ----------------------------------------------

    def _call_function(self, entry: PouEntry, node: A.Call, frame: Frame) -> Any:
        defn = entry.defn
        locals_: dict[str, Cell] = {}
        ret_type = entry.type.target or self.model.types["ANY"]
        locals_[defn.name] = Cell(default_value(ret_type), ret_type, defn.name)

        call_frame = Frame(locals_, entry=entry, parent=frame)
        for decl in defn.vars:
            self._declare_cells(decl, locals_)
        for decl in defn.vars:
            self._run_initialisers(decl, locals_, call_frame)

        interface = {
            vname: (decl.section, self.model.resolve_type(decl.type))
            for decl in defn.vars
            if decl.section in ("input", "inout")
            for vname in decl.names
        }
        self._bind_args(locals_, defn.name, interface, node, frame)

        try:
            self.execute_block(defn.body, call_frame)
        except _Return:
            pass
        return locals_[defn.name].value

    # -- standard functions -----------------------------------------------

    def _call_std(self, fn: StdFunction, node: A.Call, frame: Frame) -> Any:
        names = fn.params
        values: dict[str, Any] = {}

        positional = [a for a in node.args if a.name is None]
        named = {a.name: a for a in node.args if a.name is not None}

        if fn.variadic:
            if named:
                raise IecTypeError(f"{names[0]}... cannot be called with named arguments")
            args = [self.eval(a.value, frame) for a in positional]
            return fn.impl(*args)

        for pname, arg in named.items():
            if pname not in names:
                raise IecNameError(f"function has no parameter '{pname}'", pos=arg.pos)
            values[pname] = self.eval(arg.value, frame)

        pos_iter = iter(names)
        for arg in positional:
            for pname in pos_iter:
                if pname in values:
                    continue
                values[pname] = self.eval(arg.value, frame)
                break
            else:
                raise IecTypeError("too many arguments", pos=arg.pos)

        missing = [n for n in names if n not in values and n not in fn.optional]
        if missing:
            raise IecTypeError(
                f"missing argument(s): {', '.join(missing)}", pos=node.pos
            )

        if fn.env_impl is not None:
            result = fn.env_impl(self, values)
        else:
            result = fn.impl(*(values[n] for n in names if n in values))
        if fn.ret != "SAME":
            t = self.model.type_of(fn.ret)
            return coerce(result, t) if t is not None else result
        return result

    # -- statements --------------------------------------------------------

    def execute_block(self, body: list[A.Stmt], frame: Frame) -> None:
        for stmt in body:
            self.execute(stmt, frame)

    def execute(self, stmt: A.Stmt, frame: Frame) -> None:
        if isinstance(stmt, A.Assignment):
            cell = self.resolve_cell(stmt.target, frame)
            if cell is None:
                raise IecTypeError("cannot assign to this expression", pos=stmt.pos)
            value = self.eval(stmt.value, frame)
            self.write_cell(cell, value, stmt.pos)
            if self.trace_enabled:
                self._trace("assign", cell.name or "?", self.read_cell(cell, stmt.pos))
            return

        if isinstance(stmt, A.RefAssignment):
            cell = self.resolve_cell(stmt.target, frame)
            if cell is None:
                raise IecTypeError("cannot assign a reference here", pos=stmt.pos)
            target = self.resolve_cell(stmt.value, frame)
            if target is None:
                raise IecTypeError("REF requires a variable", pos=stmt.value.pos)
            cell.value = PointerValue(target, cell.type)
            return

        if isinstance(stmt, A.CallStatement):
            self.eval_call(stmt.call, frame)
            return

        if isinstance(stmt, A.If):
            for cond, body in stmt.branches:
                if self.eval(cond, frame):
                    self.execute_block(body, frame)
                    return
            if stmt.orelse:
                self.execute_block(stmt.orelse, frame)
            return

        if isinstance(stmt, A.Case):
            self._execute_case(stmt, frame)
            return

        if isinstance(stmt, A.For):
            self._execute_for(stmt, frame)
            return

        if isinstance(stmt, A.While):
            while self.eval(stmt.cond, frame):
                try:
                    self.execute_block(stmt.body, frame)
                except _Break:
                    break
                except _Continue:
                    continue
            return

        if isinstance(stmt, A.Repeat):
            while True:
                try:
                    self.execute_block(stmt.body, frame)
                except _Break:
                    break
                except _Continue:
                    pass
                if self.eval(stmt.cond, frame):
                    break
            return

        if isinstance(stmt, A.Exit):
            raise _Break
        if isinstance(stmt, A.Continue):
            raise _Continue
        if isinstance(stmt, A.Return):
            raise _Return
        if isinstance(stmt, A.Empty):
            return

        raise IecRuntimeError(f"cannot execute {type(stmt).__name__}", pos=stmt.pos)

    def _execute_case(self, stmt: A.Case, frame: Frame) -> None:
        selector = self.eval(stmt.selector, frame)
        for arm in stmt.arms:
            for label in arm.labels:
                lo = self.eval(label.lo, frame)
                hi = self.eval(label.hi, frame) if label.hi is not None else lo
                if lo <= selector <= hi:
                    self.execute_block(arm.body, frame)
                    return
        if stmt.orelse:
            self.execute_block(stmt.orelse, frame)

    def _execute_for(self, stmt: A.For, frame: Frame) -> None:
        cell = self.resolve_cell(stmt.var, frame)
        if cell is None:
            raise IecTypeError("FOR loop variable must be a variable", pos=stmt.pos)
        start = int(self.eval(stmt.start, frame))
        stop = int(self.eval(stmt.stop, frame))
        step = int(self.eval(stmt.step, frame)) if stmt.step is not None else 1
        if step == 0:
            raise IecRuntimeError("FOR loop step must not be zero", pos=stmt.pos)

        self.write_cell(cell, start, stmt.pos)
        while True:
            current = int(self.read_cell(cell, stmt.pos))
            if (step > 0 and current > stop) or (step < 0 and current < stop):
                break
            try:
                self.execute_block(stmt.body, frame)
            except _Break:
                break
            except _Continue:
                pass
            current = int(self.read_cell(cell, stmt.pos))
            self.write_cell(cell, current + step, stmt.pos)

    # -- tracing -----------------------------------------------------------

    def _trace(self, kind: str, name: str, value: Any) -> None:
        self.events.append(TraceEvent(self.scan, self.now, kind, name, value))

    # -- driving execution -------------------------------------------------

    def step(self) -> None:
        """Run exactly one scan, advancing the virtual clock afterwards."""
        self.scan += 1
        # execute_instance contains RETURN, so the POU under test cannot leak a
        # _Return out of its own scan.
        self.execute_instance(self.instance)
        self.now += self.cycle_ns

    def run(self, scans: int = 1) -> None:
        for _ in range(scans):
            self.step()

    # -- variable access from tests ---------------------------------------

    def get(self, name: str) -> Any:
        cell = self._find_cell(name)
        return self.read_cell(cell)

    def encode_for(self, cell: Cell, value: Any) -> Any:
        """Translate a Python literal into the domain value *cell* expects.

        This is the one place that understands the convenient test-side
        spellings: ``"1m30s"`` for a TIME, ``"Color#Red"`` or ``"Red"`` for an
        enumeration member.
        """
        t = cell.type
        if t.kind == REFERENCE and isinstance(cell.value, Cell):
            t = cell.value.type
        if isinstance(value, str):
            if t.kind == TIME:
                return parse_time(value)
            if t.kind == ENUM:
                return self._enum_member(value, t)
        return value

    def render_for(self, cell: Cell, value: Any) -> Any:
        """Inverse of :meth:`encode_for`, used to word assertion messages."""
        t = cell.type
        if t.kind == REFERENCE and isinstance(cell.value, Cell):
            t = cell.value.type
        if t.kind == ENUM and not isinstance(value, str):
            name = _enum_name(t, value)
            if name is not None:
                return name
        return value

    def _enum_member(self, value: str, t: IecType) -> int:
        return _enum_member(value, t)

    def call(self, name: str, **kwargs: Any) -> Any:
        """Invoke a FUNCTION by name and return its result.

        Arguments are coerced to the declared parameter types, so a TIME
        parameter accepts ``"1s"`` and an enumeration parameter accepts
        ``"Color#Red"``.
        """
        entry = self.model.pou(name)
        if entry is None or entry.defn.kind != "FUNCTION":
            raise IecNameError(f"'{name}' is not a FUNCTION")
        interface = {
            vname: self.model.resolve_type(decl.type)
            for decl in entry.defn.vars
            if decl.section in ("input", "inout")
            for vname in decl.names
        }
        args: list[A.Arg] = []
        for pname, value in kwargs.items():
            key = pname.upper()
            ptype = interface.get(key)
            if ptype is None:
                raise IecNameError(f"'{name}' has no parameter '{pname}'")
            args.append(A.Arg(name=key, value=self._to_literal(value, ptype), pos=None))
        call = A.Call(callee=A.VarRef(name=name.upper(), pos=None), args=args, pos=None)
        caller = self.instance.ctx
        return self._call_function(entry, call, caller)

    @staticmethod
    def _to_literal(value: Any, target: IecType | None) -> A.Literal:
        """Wrap a Python value in a Literal of the type the parameter expects."""
        if target is not None and isinstance(value, str):
            if target.kind == TIME:
                return A.Literal(kind="time", value=parse_time(value))
            if target.kind == ENUM:
                return A.Literal(
                    kind="int", value=_enum_member(value, target), type_name=target.name
                )
        if isinstance(value, bool):
            return A.Literal(kind="bool", value=value)
        if isinstance(value, int):
            return A.Literal(kind="int", value=value)
        if isinstance(value, float):
            return A.Literal(kind="real", value=value)
        if isinstance(value, str):
            return A.Literal(kind="str", value=value)
        raise IecTypeError(f"cannot pass {type(value).__name__} as a function argument")

    def set(self, name: str | None = None, value: Any = None, **kwargs: Any) -> None:
        """Set one variable positionally, or several by keyword."""
        pairs: list[tuple[str, Any]] = []
        if name is not None:
            pairs.append((str(name), value))
        pairs.extend(kwargs.items())
        for var_name, var_value in pairs:
            self._set_one(var_name, var_value)

    def _set_one(self, name: str, value: Any) -> None:
        cell = self._find_cell(name)
        cell.set(self.encode_for(cell, value))

    def _find_cell(self, name: str) -> Cell:
        """Resolve ``"Timer.ET"``, ``"Configs[2].Mode"`` or ``"M[1,2]"`` to a cell.

        Indices are written the ST way, so the number in the brackets is a real
        index and is range-checked against the array's declared bounds: for
        ``ARRAY[1..8]`` the first element is ``Arr[1]``.
        """
        parts = name.split(".")
        head, indices = _split_part(parts[0], name)
        root = head.upper()
        if root in self.instance.vars:
            current: Any = self.instance.vars[root]
        elif root in self.model.globals:
            current = self.model.globals[root]
        else:
            raise IecNameError(f"no variable named '{name}'")

        steps: list[tuple[str | None, list[int]]] = [(None, indices)]
        steps += [_split_part(part, name) for part in parts[1:]]
        for member, idx in steps:
            holder = self.read_cell(current) if isinstance(current, Cell) else current
            if member:
                label = member.upper()
                if isinstance(holder, FbInstance):
                    if label not in holder.vars:
                        raise IecNameError(f"'{holder.type.name}' has no member '{member}'")
                    current = holder.vars[label]
                elif isinstance(holder, StructValue):
                    current = holder.cell(label, source=name)
                else:
                    raise IecNameError(f"cannot descend into '{member}' of '{name}'")
                holder = self.read_cell(current) if isinstance(current, Cell) else current
            if idx:
                if not isinstance(holder, ArrayValue):
                    raise IecNameError(f"cannot index '{member or root}': it is not an array")
                current = holder.cell_at(idx, source=name)

        if not isinstance(current, Cell):
            raise IecNameError(f"'{name}' is not a variable")
        return current

    def snapshot(self) -> dict[str, Any]:
        """A flat, JSON-friendly view of the instance's variables."""
        out: dict[str, Any] = {}
        for name, cell in self.instance.vars.items():
            if name.startswith("_"):
                continue
            value = self.read_cell(cell)
            out[name] = _render(value, cell.type)
        return out

    def report(self) -> str:
        lines = [f"scan={self.scan} time={format_time(self.now)}"]
        for name, value in sorted(self.snapshot().items()):
            lines.append(f"  {name} = {value}")
        return "\n".join(lines)


def _split_part(part: str, whole: str) -> tuple[str, list[int]]:
    """Split ``"Configs[2]"`` into ``("Configs", [2])``.

    Accepts ``[i]``, ``[i,j]`` and ``[i][j]`` spellings for the same subscript.
    """
    name, _, rest = part.partition("[")
    if not rest:
        return name, []
    if not rest.endswith("]"):
        raise IecNameError(f"unbalanced '[' in '{whole}'")
    indices: list[int] = []
    for chunk in rest[:-1].replace("[", ",").replace("]", ",").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            indices.append(int(chunk, 0))
        except ValueError:
            raise IecNameError(f"bad array index '{chunk}' in '{whole}'") from None
    return name, indices


def _render(value: Any, t: IecType) -> Any:
    if isinstance(value, FbInstance):
        return {k: _render(v.value, v.type) for k, v in value.vars.items() if not k.startswith("_")}
    if isinstance(value, StructValue):
        return {k: _render(c.value, c.type) for k, c in value.fields.items()}
    if isinstance(value, ArrayValue):
        return [_render(c.value, c.type) for c in value.items]
    if isinstance(value, PointerValue):
        return "<pointer>"
    if t.kind == TIME:
        return format_time(int(value))
    if t.kind == ENUM:
        # Showing the member name is far more useful to a PLC engineer than
        # the raw ordinal; set() accepts the name back, so state() round-trips.
        name = _enum_name(t, value)
        if name is not None:
            return name
    return value


def _enum_name(t: IecType, value: Any) -> str | None:
    """The member of *t* holding *value*, or None if there is no such member."""
    # Two members can share an ordinal in ST; the first name declared wins.
    for name, ordinal in (t.members or {}).items():
        if ordinal == value:
            return name
    return None


def describe_pos(pos: A.Pos | None) -> str:
    """Render an AST position for the value layer, which takes plain strings."""
    return str(pos) if pos is not None else "<st>"


def _enum_member(value: str, t: IecType) -> int:
    """Resolve ``"Color#Red"`` or ``"Red"`` against enumeration type *t*."""
    ordinal = enum_member(t, value)
    if ordinal is None:
        members = t.members or {}
        raise IecNameError(
            f"'{value}' is not a member of {t.name}"
            f" (expected one of: {', '.join(members)})"
        )
    return ordinal
