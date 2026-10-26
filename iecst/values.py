"""The IEC 61131-3 type system and runtime value containers.

Two decisions shape this module:

* **Wrapping, not growing.**  ``INT#32767 + INT#1`` must yield ``-32768``, not
  32768.  Every integer result is re-wrapped to the width of its type, and
  ``UINT`` values are reduced modulo their width.  This is where hand-written
  interpreters usually diverge from a real controller.

* **Containers hold cells.**  Structs and arrays are made of :class:`Cell`
  objects rather than plain Python values.  That makes ``VAR_IN_OUT`` of an
  array element and ``p^ := x`` fall out for free, since both simply need a
  reference to the same cell that the variable lives in.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

from .errors import IecTypeError

# --- type kinds -----------------------------------------------------------

BOOL = "bool"
INT = "int"
UINT = "uint"
BITSTR = "bitstr"
REAL = "real"
TIME = "time"
DATE = "date"
TOD = "tod"
DT = "dt"
STRING = "string"
CHAR = "char"
ARRAY = "array"
STRUCT = "struct"
ENUM = "enum"
FB = "fb"
POINTER = "pointer"
REFERENCE = "reference"
ANY = "any"

NANOSECONDS_PER_DAY = 86_400_000_000_000

_NS_PER_MS = 1_000_000
_NS_PER_S = 1_000_000_000
_TIME_PART = re.compile(r"([+-]?\d+(?:\.\d+)?)\s*([a-zA-Z]+)")


def _unit_ns(unit: str) -> int:
    table = {
        "D": 86_400 * _NS_PER_S, "H": 3_600 * _NS_PER_S, "M": 60 * _NS_PER_S,
        "S": _NS_PER_S, "MS": _NS_PER_MS, "US": 1_000, "NS": 1,
    }
    try:
        return table[unit.upper()]
    except KeyError:
        raise IecTypeError(f"unknown time unit '{unit}'") from None


def parse_time(text: "str | int | float") -> int:
    """Accept ``"10ms"``, ``"1m30s"``, ``"T#500ms"`` or a number of nanoseconds."""
    if isinstance(text, (int, float)):
        return int(text)
    s = text.strip()
    if s[:2].upper() == "T#":
        s = s[2:]
    parts = _TIME_PART.findall(s)
    if not parts or _TIME_PART.sub("", s).strip():
        raise IecTypeError(f"malformed time literal {text!r}")
    return round(sum(float(value) * _unit_ns(unit) for value, unit in parts))


@dataclass
class IecType:
    name: str
    kind: str
    bits: int = 0
    signed: bool = False
    elem: "IecType | None" = None
    bounds: tuple[tuple[int, int], ...] = ()
    fields: "dict[str, IecType] | None" = None
    field_defaults: "dict[str, Any] | None" = None
    members: "dict[str, int] | None" = None
    target: "IecType | None" = None
    pou: Any = None
    base: "IecType | None" = None
    length: int | None = None
    impl: Any = None

    # -- helpers -----------------------------------------------------------

    @property
    def is_numeric(self) -> bool:
        return self.kind in (INT, UINT, BITSTR, REAL, TIME, DATE, TOD, DT, ENUM, BOOL)

    @property
    def is_integer(self) -> bool:
        return self.kind in (INT, UINT, BITSTR, ENUM, BOOL)

    @property
    def is_float(self) -> bool:
        return self.kind == REAL

    @property
    def is_time_like(self) -> bool:
        return self.kind in (TIME, DATE, TOD, DT)

    @property
    def is_aggregate(self) -> bool:
        return self.kind in (ARRAY, STRUCT)

    @property
    def lo(self) -> int:
        """Inclusive lower bound for a single-dimension ARRAY."""
        return self.bounds[0][0]

    @property
    def hi(self) -> int:
        """Inclusive upper bound for a single-dimension ARRAY."""
        return self.bounds[0][1]

    def element_type(self) -> "IecType":
        t = self
        while t.kind == ARRAY and t.elem is not None:
            t = t.elem
        return t

    def describe(self) -> str:
        if self.kind == ARRAY:
            dims = ", ".join(f"{lo}..{hi}" for lo, hi in self.bounds)
            return f"ARRAY[{dims}] OF {self.elem.describe() if self.elem else '?'}"
        if self.kind == STRUCT:
            return self.name
        if self.kind == POINTER:
            return f"POINTER TO {self.target.describe() if self.target else '?'}"
        return self.name


# --- the built-in type table ---------------------------------------------

def _int_type(name: str, bits: int, signed: bool) -> IecType:
    return IecType(name=name, kind=INT if signed else UINT, bits=bits, signed=signed)


def _bitstr_type(name: str, bits: int) -> IecType:
    return IecType(name=name, kind=BITSTR, bits=bits, signed=False)


BUILTIN_TYPES: dict[str, IecType] = {
    "BOOL": IecType("BOOL", BOOL, bits=1),
    "BYTE": _bitstr_type("BYTE", 8),
    "WORD": _bitstr_type("WORD", 16),
    "DWORD": _bitstr_type("DWORD", 32),
    "LWORD": _bitstr_type("LWORD", 64),
    "SINT": _int_type("SINT", 8, True),
    "INT": _int_type("INT", 16, True),
    "DINT": _int_type("DINT", 32, True),
    "LINT": _int_type("LINT", 64, True),
    "USINT": _int_type("USINT", 8, False),
    "UINT": _int_type("UINT", 16, False),
    "UDINT": _int_type("UDINT", 32, False),
    "ULINT": _int_type("ULINT", 64, False),
    "REAL": IecType("REAL", REAL, bits=32),
    "LREAL": IecType("LREAL", REAL, bits=64),
    "TIME": IecType("TIME", TIME, bits=64),
    "DATE": IecType("DATE", DATE, bits=64),
    "TOD": IecType("TOD", TOD, bits=64),
    "TIME_OF_DAY": IecType("TOD", TOD, bits=64),
    "DT": IecType("DT", DT, bits=64),
    "DATE_AND_TIME": IecType("DT", DT, bits=64),
    "STRING": IecType("STRING", STRING),
    "WSTRING": IecType("WSTRING", STRING),
    "CHAR": IecType("CHAR", CHAR, bits=8),
    "WCHAR": IecType("WCHAR", CHAR, bits=16),
    "ANY": IecType("ANY", ANY),
}

# Conversion aliases accepted as ``X_TO_Y`` function names.
_INT_RANK = {"SINT": 0, "INT": 1, "DINT": 2, "LINT": 3,
             "USINT": 0, "UINT": 1, "UDINT": 2, "ULINT": 3,
             "BYTE": 0, "WORD": 1, "DWORD": 2, "LWORD": 3}


def wrap_int(value: int, t: IecType) -> int:
    """Reduce *value* to the two's-complement range of *t*."""
    bits = t.bits or 64
    if t.kind == BOOL:
        return 1 if value else 0
    modulus = 1 << bits
    value &= modulus - 1
    if t.kind in (INT,) and t.signed:
        sign_bit = 1 << (bits - 1)
        if value & sign_bit:
            value -= modulus
    elif t.kind == ENUM and bits < 64:
        sign_bit = 1 << (bits - 1)
        if value & sign_bit:
            value -= modulus
    return value


def round_half_away(value: float) -> int:
    """Round like a PLC does: halves away from zero, not banker's rounding."""
    if math.isnan(value) or math.isinf(value):
        raise IecTypeError(f"cannot convert {value} to an integer type")
    return int(math.floor(value + 0.5)) if value >= 0 else int(math.ceil(value - 0.5))


def _show(value: Any) -> str:
    """How a rejected value reads in an error message.

    ``3.5`` or ``'high'`` says what the test actually passed; ``float`` and
    ``str`` do not.
    """
    text = repr(value)
    return text if len(text) <= 30 else text[:27] + "..."


def enum_member(t: IecType, value: str) -> int | None:
    """The ordinal that *value* names in enumeration *t*, or None.

    Accepts the member bare or qualified -- ``"Red"`` or ``"Color#Red"`` -- in
    any case, since CODESYS writes both spellings.
    """
    name = (value.partition("#")[2] or value).upper()
    return (t.members or {}).get(name)


def coerce(value: Any, target: IecType, *, source: str = "<st>") -> Any:
    """Convert *value* to *target*, applying IEC 61131-3 conversion rules."""
    kind = target.kind

    if kind == ANY:
        return value

    if kind == BOOL:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value != 0
        raise IecTypeError(f"cannot convert {type(value).__name__} to BOOL", source=source)

    if kind in (INT, UINT, BITSTR, ENUM):
        if kind == ENUM and isinstance(value, str):
            # An enumeration accepts its member spelled out, either bare or
            # qualified: "Automatic" or "E_OpMode#Automatic".  Doing this here
            # rather than only at the top level means enum fields nested inside
            # a struct initialiser work too.
            ordinal = enum_member(target, value)
            if ordinal is None:
                members = target.members or {}
                raise IecTypeError(
                    f"'{value}' is not a member of {target.name}"
                    f" (expected one of: {', '.join(members)})",
                    source=source,
                )
            value = ordinal
        if isinstance(value, bool):
            value = 1 if value else 0
        elif isinstance(value, float):
            value = round_half_away(value)
        elif not isinstance(value, int):
            raise IecTypeError(f"cannot convert {_show(value)} to {target.name}", source=source)
        return wrap_int(value, target)

    if kind == REAL:
        if isinstance(value, bool):
            value = 1.0 if value else 0.0
        elif isinstance(value, int):
            value = float(value)
        if isinstance(value, float):
            # REAL is single precision; without this, a REAL accumulator drifts
            # away from what the controller computes.
            if target.bits == 32:
                import struct

                value = struct.unpack("<f", struct.pack("<f", value))[0]
            return value
        raise IecTypeError(
            f"cannot convert {_show(value)} to {target.name}", source=source
        )

    if kind in (TIME, DATE, TOD, DT):
        if kind == TIME and isinstance(value, str):
            # "200ms" / "T#5s", so a TIME field nested in a struct initialiser
            # reads the same as a literal in the ST source.
            try:
                value = parse_time(value)
            except IecTypeError as exc:
                # Re-raise with the position the caller gave us: parse_time has
                # no idea where in the source this value came from.
                raise IecTypeError(
                    f"cannot convert {value!r} to {target.name}: {exc.message}", source=source
                ) from None
        if isinstance(value, float):
            value = round_half_away(value)
        if isinstance(value, bool) or not isinstance(value, int):
            raise IecTypeError(f"cannot convert {_show(value)} to {target.name}", source=source)
        return wrap_int(value, IecType("X", INT, bits=64, signed=True))

    if kind == STRING:
        if isinstance(value, str):
            return value
        if isinstance(value, bool):
            return "TRUE" if value else "FALSE"
        if isinstance(value, int):
            return str(value)
        if isinstance(value, float):
            return repr(value)
        raise IecTypeError(f"cannot convert {_show(value)} to {target.name}", source=source)

    if kind == CHAR:
        if isinstance(value, str) and len(value) == 1:
            return value
        if isinstance(value, int):
            return chr(value)
        raise IecTypeError(f"cannot convert {_show(value)} to {target.name}", source=source)

    if kind == ARRAY:
        return _coerce_array(value, target, source=source)
    if kind == STRUCT:
        return _coerce_struct(value, target, source=source)

    if kind in (POINTER, REFERENCE):
        if value is None or isinstance(value, (PointerValue, Cell)):
            return value
        raise IecTypeError(f"cannot convert {_show(value)} to {target.name}", source=source)

    if kind == FB:
        if isinstance(value, FbInstance) and value.type is target:
            return value
        raise IecTypeError(f"cannot convert {_show(value)} to {target.name}", source=source)

    return value


def _coerce_array(value: Any, target: IecType, *, source: str) -> "ArrayValue":
    if isinstance(value, ArrayValue):
        if value.type is target:
            return value
        value = [c.value for c in value.items]
    if isinstance(value, (list, tuple)) and len(target.bounds) > 1 and value and isinstance(value[0], (list, tuple)):
        value = _flatten(value)
    if not isinstance(value, (list, tuple)):
        raise IecTypeError(f"cannot convert {_show(value)} to {target.name}", source=source)
    out = ArrayValue(type=target)
    out.build([coerce(v, target.elem, source=source) for v in value])
    return out


def _flatten(value: Any) -> list[Any]:
    out: list[Any] = []
    for item in value:
        if isinstance(item, (list, tuple)):
            out.extend(_flatten(item))
        else:
            out.append(item)
    return out


def _coerce_struct(value: Any, target: IecType, *, source: str) -> "StructValue":
    if isinstance(value, StructValue):
        return value
    if not isinstance(value, dict):
        raise IecTypeError(f"cannot convert {_show(value)} to {target.name}", source=source)
    # Field names are matched case-insensitively, so a test may write
    # {"lo": 0, "hi": 100} for a STRUCT whose members are Lo and Hi.
    provided = {str(k).upper(): v for k, v in value.items()}
    unknown = set(provided) - set(target.fields or {})
    if unknown:
        raise IecTypeError(
            f"'{target.name}' has no member(s) {', '.join(sorted(unknown))}"
            f" (expected: {', '.join(sorted(target.fields or {}))})",
            source=source,
        )
    out = StructValue(type=target)
    defaults = target.field_defaults or {}
    for fname, ftype in (target.fields or {}).items():
        # A member left out of the dict falls back to the DUT's own default
        # rather than to zero, so a partial dict still means the same thing as
        # on the controller.
        if fname in provided:
            member = provided[fname]
        elif fname in defaults:
            member = defaults[fname]
        else:
            member = default_value(ftype)
        out.fields[fname] = Cell(coerce(member, ftype, source=source), ftype, fname)
    return out


def default_value(t: IecType) -> Any:
    """The value a variable of type *t* holds before any assignment."""
    if t.kind == BOOL:
        return False
    if t.kind in (INT, UINT, BITSTR, ENUM):
        return 0
    if t.kind == REAL:
        return 0.0
    if t.kind in (TIME, DATE, TOD, DT):
        return 0
    if t.kind == STRING:
        return ""
    if t.kind == CHAR:
        return "\x00"
    if t.kind == ARRAY:
        v = ArrayValue(type=t)
        v.build_defaults()
        return v
    if t.kind == STRUCT:
        v = StructValue(type=t)
        defaults = t.field_defaults or {}
        for fname, ftype in (t.fields or {}).items():
            # A DUT member may carry a default of its own -- ``InLow : REAL := 4``
            # -- which stands in wherever the caller supplies no struct.
            initial = defaults[fname] if fname in defaults else default_value(ftype)
            v.fields[fname] = Cell(coerce(initial, ftype), ftype, fname)
        return v
    if t.kind in (POINTER, REFERENCE):
        return None
    if t.kind == FB:
        return FbInstance(t)
    return None


# --- value containers -----------------------------------------------------


class Cell:
    """A mutable storage slot: the unit of assignment and of VAR_IN_OUT aliasing."""

    __slots__ = ("value", "type", "name")

    def __init__(self, value: Any, type: IecType, name: str = "") -> None:
        self.value = value
        self.type = type
        self.name = name

    def set(self, value: Any, *, source: str = "<st>") -> None:
        self.value = coerce(value, self.type, source=source)

    def get(self) -> Any:
        return self.value

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Cell({self.name or '?'}={self.value!r})"


class BitCell(Cell):
    """A view onto one bit of a bit-string or integer cell.

    ``Status.3`` is not a slot of its own: reading it means testing a bit, and
    assigning it means clearing or setting that bit in the parent word.  The
    view is what lets one :class:`Cell` serve both ``X := Status.3`` and
    ``Status.3 := TRUE``.
    """

    __slots__ = ("parent", "index")

    def __init__(self, parent: Cell, index: int, type: IecType, name: str = "") -> None:
        # The initial value is only a placeholder: get() always asks the parent,
        # so the view can never go stale.
        super().__init__(False, type, name)
        self.parent = parent
        self.index = index

    def get(self) -> Any:
        return bool((int(self.parent.value or 0) >> self.index) & 1)

    def set(self, value: Any, *, source: str = "<st>") -> None:
        word = int(self.parent.value or 0)
        if value:
            word |= 1 << self.index
        else:
            word &= ~(1 << self.index)
        self.parent.value = wrap_int(word, self.parent.type)
        self.value = self.get()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"BitCell({self.name}={self.get()})"


class PointerValue:
    """The result of ``ADR(x)`` or of assigning a pointer variable."""

    __slots__ = ("cell", "type")

    def __init__(self, cell: "Cell | None", type: IecType) -> None:
        self.cell = cell
        self.type = type

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"PointerValue({self.cell!r})"


class StructValue:
    """A struct instance: field name to cell."""

    __slots__ = ("fields", "type")

    def __init__(self, type: IecType, fields: "dict[str, Cell] | None" = None) -> None:
        self.type = type
        self.fields = fields if fields is not None else {}

    def cell(self, name: str, source: str = "<st>") -> Cell:
        try:
            return self.fields[name]
        except KeyError:
            raise IecTypeError(
                f"'{self.type.name}' has no member '{name}'", source=source
            ) from None

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"StructValue({self.type.name})"


class ArrayValue:
    """An array, stored as a flat row-major list of cells.

    Flattening multi-dimensional arrays keeps the cell list one level deep,
    which is what makes ``arr[i][j]`` and ``VAR_IN_OUT`` of an element both
    resolve to a plain :class:`Cell`.
    """

    __slots__ = ("items", "type")

    def __init__(self, type: IecType, items: "list[Cell] | None" = None) -> None:
        self.type = type
        self.items: list[Cell] = items if items is not None else []

    @property
    def size(self) -> int:
        n = 1
        for lo, hi in self.type.bounds:
            n *= hi - lo + 1
        return n

    def build_defaults(self) -> None:
        elem = self.type.elem
        self.items = [Cell(default_value(elem), elem) for _ in range(self.size)]

    def build(self, values: list[Any]) -> None:
        size = self.size
        elem = self.type.elem
        if len(values) > size:
            raise IecTypeError(
                f"too many initialisers for {self.type.describe()}: got {len(values)}, expected {size}"
            )
        elems = [Cell(v, elem) for v in values]
        elems.extend(Cell(default_value(elem), elem) for _ in range(size - len(elems)))
        self.items = elems

    def offset(self, indices: list[int], source: str = "<st>") -> int:
        bounds = self.type.bounds
        if len(indices) != len(bounds):
            raise IecTypeError(
                f"{self.type.describe()} expects {len(bounds)} index/indices, got {len(indices)}",
                source=source,
            )
        off = 0
        for index, (lo, hi) in zip(indices, bounds):
            if not lo <= index <= hi:
                raise IecTypeError(
                    f"array index {index} out of range {lo}..{hi} for {self.type.describe()}",
                    source=source,
                )
            off = off * (hi - lo + 1) + (index - lo)
        return off

    def cell_at(self, indices: list[int], source: str = "<st>") -> Cell:
        return self.items[self.offset(indices, source)]

    def __len__(self) -> int:
        return self.size

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"ArrayValue({self.type.describe()})"


class FbInstance:
    """A function-block or program instance, persisting state between scans."""

    __slots__ = ("type", "vars", "ctx")

    def __init__(self, type: IecType) -> None:
        self.type = type
        self.vars: dict[str, Cell] = {}
        self.ctx: Any = None

    def cell(self, name: str, source: str = "<st>") -> Cell:
        return self.vars[name]

    def read(self, name: str) -> Any:
        return self.vars[name].value

    def write(self, name: str, value: Any, *, source: str = "<st>") -> None:
        self.vars[name].set(value, source=source)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"FbInstance({self.type.name})"
