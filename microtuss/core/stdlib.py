"""The standard function and function-block library.

Functions are plain Python callables with declared parameters; function blocks
are subclasses of :class:`~microtuss.values.FbInstance` so that their state survives
from one scan to the next, exactly like a user-written FB.

Timers read the *virtual* clock exposed by the environment (``env.now`` in
nanoseconds) rather than wall-clock time.  That is what keeps a test that steps
ten scans of 10 ms deterministic instead of flaky.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable

from microtuss.core.errors import IecRuntimeError
from microtuss.core.values import (
    BUILTIN_TYPES,
    FB,
    INT,
    REAL,
    TIME,
    Cell,
    FbInstance,
    IecType,
    default_value,
    round_half_away,
)

# --- formatting helpers ---------------------------------------------------


def format_time(ns: int) -> str:
    """Render a TIME value the way a controller displays it: ``T#1m30s500ms``."""
    sign = "-" if ns < 0 else ""
    ns = abs(int(ns))
    days, ns = divmod(ns, 86_400_000_000_000)
    hours, ns = divmod(ns, 3_600_000_000_000)
    minutes, ns = divmod(ns, 60_000_000_000)
    seconds, ns = divmod(ns, 1_000_000_000)
    millis, ns = divmod(ns, 1_000_000)
    micros, nanos = divmod(ns, 1_000)

    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if seconds:
        parts.append(f"{seconds}s")
    if millis:
        parts.append(f"{millis}ms")
    if micros:
        parts.append(f"{micros}us")
    if nanos:
        parts.append(f"{nanos}ns")
    if not parts:
        return "T#0s"
    return sign + "T#" + "".join(parts)


def format_tod(ns: int) -> str:
    hours, rest = divmod(int(ns), 3_600_000_000_000)
    minutes, rest = divmod(rest, 60_000_000_000)
    seconds, rest = divmod(rest, 1_000_000_000)
    base = f"TOD#{hours:02d}:{minutes:02d}:{seconds:02d}"
    if rest:
        return base + f".{rest // 1_000_000:03d}"
    return base


# --- integer helpers ------------------------------------------------------


def _div(a: int, b: int) -> int:
    """Integer division truncating toward zero, as IEC 61131-3 requires."""
    if b == 0:
        raise IecRuntimeError("division by zero")
    q = abs(a) // abs(b)
    return q if (a >= 0) == (b >= 0) else -q


def _mod(a: int, b: int) -> int:
    if b == 0:
        raise IecRuntimeError("division by zero (MOD)")
    return a - _div(a, b) * b


def _mask(t: IecType) -> int:
    return (1 << (t.bits or 64)) - 1


# --- function table -------------------------------------------------------


@dataclass
class StdFunction:
    impl: Callable[..., Any] | None
    params: tuple[str, ...]
    param_types: tuple[str, ...] = ()
    ret: str = "SAME"  # a type name, or "SAME" meaning "the first argument's type"
    variadic: bool = False
    optional: tuple[str, ...] = ()
    # Functions that need the runtime rather than only their arguments -- the
    # clock readers, above all.  Called as env_impl(runtime, values_by_name).
    env_impl: Callable[..., Any] | None = None


def _f(
    impl: Callable[..., Any] | None,
    *params: str,
    types: tuple[str, ...] = (),
    ret: str = "SAME",
    variadic: bool = False,
    optional: tuple[str, ...] = (),
    env_impl: Callable[..., Any] | None = None,
) -> StdFunction:
    return StdFunction(
        impl=impl,
        params=params,
        param_types=types,
        ret=ret,
        variadic=variadic,
        optional=optional,
        env_impl=env_impl,
    )


STANDARD_FUNCTIONS: dict[str, StdFunction] = {}


def _register(name: str, fn: StdFunction) -> None:
    STANDARD_FUNCTIONS[name] = fn


# -- math ------------------------------------------------------------------

_register("ABS", _f(lambda IN: abs(IN), "IN"))
_register("SQRT", _f(lambda IN: math.sqrt(IN) if IN >= 0 else _nan(), "IN", types=("REAL",), ret="REAL"))
_register("EXP", _f(math.exp, "IN", types=("REAL",), ret="REAL"))
_register("LN", _f(lambda IN: math.log(IN) if IN > 0 else _neg_domain("LN"), "IN", types=("REAL",), ret="REAL"))
_register("LOG", _f(lambda IN: math.log10(IN) if IN > 0 else _neg_domain("LOG"), "IN", types=("REAL",), ret="REAL"))
_register("SIN", _f(math.sin, "IN", types=("REAL",), ret="REAL"))
_register("COS", _f(math.cos, "IN", types=("REAL",), ret="REAL"))
_register("TAN", _f(math.tan, "IN", types=("REAL",), ret="REAL"))
_register("ASIN", _f(math.asin, "IN", types=("REAL",), ret="REAL"))
_register("ACOS", _f(math.acos, "IN", types=("REAL",), ret="REAL"))
_register("ATAN", _f(math.atan, "IN", types=("REAL",), ret="REAL"))
_register("ATAN2", _f(math.atan2, "X", "Y", types=("REAL", "REAL"), ret="REAL"))
_register("EXPT", _f(lambda IN1, IN2: float(IN1) ** float(IN2), "IN1", "IN2", types=("REAL", "REAL"), ret="REAL"))
_register("TRUNC", _f(lambda IN: float(int(IN)), "IN", types=("REAL",), ret="REAL"))
_register("ROUND", _f(lambda IN: float(round_half_away(IN)), "IN", types=("REAL",), ret="REAL"))


# -- wall-clock readers ----------------------------------------------------
#
# These are the only functions that observe the virtual clock.  A timer in a
# library file typically does ``start := TIME(); ... ET := TIME() - start;``,
# so leaving them out makes any library TON unusable.  They read ``env.now``,
# which only advances between scans, so results stay reproducible.


def _clock_now(env: Any, values: dict[str, Any]) -> Any:
    """``TIME()`` reads the clock; ``TIME(x)`` converts x."""
    if values:
        return values.get("IN")
    return env.now


def _clock_date(env: Any, values: dict[str, Any]) -> Any:
    """"``DATE()`` reads the clock; ``DATE(x)`` converts x.

    The interpreter has no calendar date, so the clock read yields a fixed
    epoch and only time-of-day values carry meaning.
    """
    del env
    if values:
        return values.get("IN")
    return 1


for _name in ("TIME", "T"):
    _register(_name, _f(None, "IN", ret="TIME", optional=("IN",), env_impl=_clock_now))
for _name in ("TOD", "TIME_OF_DAY"):
    _register(_name, _f(None, "IN", ret="TOD", optional=("IN",), env_impl=_clock_now))
_register("DATE", _f(None, "IN", ret="DATE", optional=("IN",), env_impl=_clock_date))
_register("DT", _f(None, "IN", ret="DT", optional=("IN",), env_impl=_clock_now))
_register("DATE_AND_TIME", _f(None, "IN", ret="DT", optional=("IN",), env_impl=_clock_now))


def _nan() -> float:
    return float("nan")


def _neg_domain(name: str) -> float:
    del name
    return float("-inf")


# -- selection and arithmetic ---------------------------------------------

def _min(*args: Any) -> Any:
    return min(args)


def _max(*args: Any) -> Any:
    return max(args)


_register("MIN", _f(_min, "IN1", "IN2", variadic=True))
_register("MAX", _f(_max, "IN1", "IN2", variadic=True))
_register("LIMIT", _f(lambda MN, IN, MX: MN if IN < MN else (MX if IN > MX else IN), "MN", "IN", "MX"))
_register("SEL", _f(lambda G, IN0, IN1: IN1 if G else IN0, "G", "IN0", "IN1", types=("BOOL",)))
_register("MOVE", _f(lambda IN: IN, "IN"))


def _mux(K: int, *args: Any) -> Any:
    if K < 0 or K >= len(args):
        raise IecRuntimeError(f"MUX selector {K} is outside 0..{len(args) - 1}")
    return args[K]


_register("MUX", _f(_mux, "K", variadic=True, types=("UINT",)))


def _add(*args: Any) -> Any:
    total = args[0]
    for v in args[1:]:
        total = total + v
    return total


def _mul(*args: Any) -> Any:
    total = args[0]
    for v in args[1:]:
        total = total * v
    return total


def _sub(*args: Any) -> Any:
    total = args[0]
    for v in args[1:]:
        total = total - v
    return total


def _divf(*args: Any) -> Any:
    total = args[0]
    for v in args[1:]:
        total = total / v if isinstance(total, float) else _div(total, v)
    return total


_register("ADD", _f(_add, "IN1", "IN2", variadic=True))
_register("MUL", _f(_mul, "IN1", "IN2", variadic=True))
_register("SUB", _f(_sub, "IN1", "IN2", variadic=True))
_register("DIV", _f(_divf, "IN1", "IN2", variadic=True))


# -- bit strings -----------------------------------------------------------

_register("SHL", _f(lambda IN, N: IN << int(N), "IN", "N", types=("ANY", "UINT")))
_register("SHR", _f(lambda IN, N: IN >> int(N), "IN", "N", types=("ANY", "UINT")))
_register("AND", _f(lambda *a: _fold(a, lambda x, y: x & y), "IN1", "IN2", variadic=True))
_register("OR", _f(lambda *a: _fold(a, lambda x, y: x | y), "IN1", "IN2", variadic=True))
_register("XOR", _f(lambda *a: _fold(a, lambda x, y: x ^ y), "IN1", "IN2", variadic=True))
_register("NOT", _f(lambda IN: ~int(IN), "IN"))


def _fold(args: tuple[Any, ...], op: Callable[[int, int], int]) -> int:
    acc = int(args[0])
    for v in args[1:]:
        acc = op(acc, int(v))
    return acc


def _rol(IN: Any, N: Any, bits: int) -> int:
    n = int(N) % bits
    m = (1 << bits) - 1
    v = int(IN) & m
    return ((v << n) | (v >> (bits - n))) & m if n else v


def _ror(IN: Any, N: Any, bits: int) -> int:
    n = int(N) % bits
    m = (1 << bits) - 1
    v = int(IN) & m
    return ((v >> n) | (v << (bits - n))) & m if n else v


_register("ROL", _f(lambda IN, N: _rol(IN, N, 16), "IN", "N", types=("ANY", "UINT")))
_register("ROR", _f(lambda IN, N: _ror(IN, N, 16), "IN", "N", types=("ANY", "UINT")))


# -- strings ---------------------------------------------------------------

_register("LEN", _f(lambda IN: len(IN), "IN", types=("STRING",), ret="INT"))
_register("LEFT", _f(lambda IN, L: IN[: int(L)], "IN", "L", types=("STRING", "INT"), ret="STRING"))
_register("RIGHT", _f(lambda IN, L: IN[len(IN) - int(L):] if L > 0 else "", "IN", "L",
                      types=("STRING", "INT"), ret="STRING"))
_register("MID", _f(lambda IN, L, P: IN[int(P) - 1: int(P) - 1 + int(L)], "IN", "L", "P",
                    types=("STRING", "INT", "INT"), ret="STRING"))
_register("CONCAT", _f(lambda *a: "".join(str(x) for x in a), "IN1", "IN2", variadic=True, ret="STRING"))
_register("INSERT", _f(lambda IN1, IN2, P: IN1[: int(P) - 1] + IN2 + IN1[int(P) - 1:], "IN1", "IN2", "P",
                       types=("STRING", "STRING", "INT"), ret="STRING"))
_register("DELETE", _f(lambda IN, L, P: IN[: int(P) - 1] + IN[int(P) - 1 + int(L):], "IN", "L", "P",
                       types=("STRING", "INT", "INT"), ret="STRING"))
_register("REPLACE", _f(lambda IN1, IN2, L, P: IN1[: int(P) - 1] + IN2 + IN1[int(P) - 1 + int(L):],
                        "IN1", "IN2", "L", "P", types=("STRING", "STRING", "INT", "INT"), ret="STRING"))
_register("FIND", _f(lambda IN1, IN2: IN1.upper().find(IN2.upper()) + 1, "IN1", "IN2",
                     types=("STRING", "STRING"), ret="INT"))


# -- type conversions ------------------------------------------------------

_CONVERSIONS = (
    "BOOL", "SINT", "INT", "DINT", "LINT", "USINT", "UINT", "UDINT", "ULINT",
    "BYTE", "WORD", "DWORD", "LWORD", "REAL", "LREAL",
    "TIME", "DATE", "TOD", "DT", "STRING", "CHAR",
)

_CONV_FUNCS = {
    name: _f(lambda v, _n=name: v, "IN", ret=name, types=("ANY",)) for name in _CONVERSIONS
}


def _register_conversions() -> None:
    # ``INT(x)`` style conversion: the type name used as a function.  The
    # clock readers registered above are left alone, so ``TIME()`` still means
    # "read the clock" while ``TIME(x)`` converts.
    for name, fn in _CONV_FUNCS.items():
        STANDARD_FUNCTIONS.setdefault(name, fn)
        STANDARD_FUNCTIONS.setdefault(f"{name}_TO_{name}", fn)

    # Every source/target spelling, including the aliases of the time types,
    # so that TIME_TO_DINT, TOD_TO_TIME and friends all exist.
    aliases = {
        "TIME": ("T",),
        "TOD": ("TIME_OF_DAY",),
        "DT": ("DATE_AND_TIME",),
    }
    spellings: list[tuple[str, str]] = []
    for canonical in _CONVERSIONS:
        spellings.append((canonical, canonical))
        spellings.extend((alias, canonical) for alias in aliases.get(canonical, ()))

    for src_spelling, _src_type in spellings:
        for dst_spelling, dst_type in spellings:
            if src_spelling == dst_spelling:
                continue
            # The function returns its argument; the caller applies the target
            # type from StdFunction.ret.
            _register(f"{src_spelling}_TO_{dst_spelling}", _f(lambda v: v, "IN", ret=dst_type, types=("ANY",)))


_register_conversions()


# --- standard function blocks --------------------------------------------


class NativeFb(FbInstance):
    """Base class for standard function blocks implemented in Python.

    Subclasses declare their interface in :attr:`decls` as ``(name, type, kind)``
    triples with ``kind`` one of ``input``/``output``/``local``.  The cell
    layout is therefore identical to a user-written FB, which lets the
    interpreter bind arguments and read outputs through one code path.
    """

    decls: tuple[tuple[str, str, str], ...] = ()

    def __init__(self, type_: IecType) -> None:
        super().__init__(type_)
        for name, tname, _kind in self.decls:
            t = BUILTIN_TYPES[tname]
            self.vars[name] = Cell(default_value(t), t, name)

    # -- convenience accessors --------------------------------------------

    def get(self, name: str) -> Any:
        return self.vars[name].value

    def put(self, name: str, value: Any) -> None:
        self.vars[name].set(value)

    def declare(self, name: str, tname: str, value: Any = None) -> None:
        """Register an internal state cell not exposed in :attr:`decls`."""
        t = BUILTIN_TYPES[tname]
        self.vars[name] = Cell(default_value(t) if value is None else value, t, name)

    def run(self, env: Any) -> None:  # pragma: no cover - overridden
        raise NotImplementedError


class Ton(NativeFb):
    """TON: on-delay timer.  ``Q`` goes TRUE once ``IN`` has been TRUE for ``PT``."""

    decls = (("IN", "BOOL", "input"), ("PT", "TIME", "input"),
             ("Q", "BOOL", "output"), ("ET", "TIME", "output"))

    def __init__(self, type_: IecType) -> None:
        super().__init__(type_)
        self.declare("_start", "TIME", 0)
        self.declare("_running", "BOOL", False)

    def run(self, env: Any) -> None:
        if not self.get("IN"):
            self.put("Q", False)
            self.put("ET", 0)
            self.vars["_running"].value = False
            return
        if not self.vars["_running"].value:
            self.vars["_start"].value = env.now
            self.vars["_running"].value = True
        elapsed = env.now - self.vars["_start"].value
        pt = self.get("PT")
        if elapsed >= pt:
            self.put("Q", True)
            self.put("ET", pt)
        else:
            self.put("Q", False)
            self.put("ET", elapsed)


class Tof(NativeFb):
    """TOF: off-delay timer.  ``Q`` stays TRUE for ``PT`` after ``IN`` drops."""

    decls = (("IN", "BOOL", "input"), ("PT", "TIME", "input"),
             ("Q", "BOOL", "output"), ("ET", "TIME", "output"))

    def __init__(self, type_: IecType) -> None:
        super().__init__(type_)
        self.declare("_start", "TIME", 0)
        self.declare("_timing", "BOOL", False)
        self.declare("_prev", "BOOL", False)

    def run(self, env: Any) -> None:
        in_ = self.get("IN")
        if in_:
            self.put("Q", True)
            self.put("ET", 0)
            self.vars["_timing"].value = False
        else:
            if self.vars["_prev"].value and not self.vars["_timing"].value:
                self.vars["_start"].value = env.now
                self.vars["_timing"].value = True
            if self.vars["_timing"].value:
                elapsed = env.now - self.vars["_start"].value
                if elapsed >= self.get("PT"):
                    self.put("Q", False)
                    self.put("ET", self.get("PT"))
                else:
                    self.put("Q", True)
                    self.put("ET", elapsed)
            else:
                self.put("Q", False)
                self.put("ET", 0)
        self.vars["_prev"].value = in_


class Tp(NativeFb):
    """TP: pulse timer.  A rising edge on ``IN`` holds ``Q`` TRUE for ``PT``."""

    decls = (("IN", "BOOL", "input"), ("PT", "TIME", "input"),
             ("Q", "BOOL", "output"), ("ET", "TIME", "output"))

    def __init__(self, type_: IecType) -> None:
        super().__init__(type_)
        self.declare("_start", "TIME", 0)
        self.declare("_active", "BOOL", False)
        self.declare("_prev", "BOOL", False)

    def run(self, env: Any) -> None:
        in_ = self.get("IN")
        rising = in_ and not self.vars["_prev"].value
        self.vars["_prev"].value = in_
        if rising and not self.vars["_active"].value:
            self.vars["_start"].value = env.now
            self.vars["_active"].value = True
        if self.vars["_active"].value:
            elapsed = env.now - self.vars["_start"].value
            if elapsed >= self.get("PT"):
                self.put("Q", False)
                self.put("ET", self.get("PT"))
                self.vars["_active"].value = False
            else:
                self.put("Q", True)
                self.put("ET", elapsed)
        else:
            self.put("Q", False)
            self.put("ET", 0)


class RTrig(NativeFb):
    """R_TRIG: detects a rising edge on ``CLK``."""

    decls = (("CLK", "BOOL", "input"), ("Q", "BOOL", "output"))

    def __init__(self, type_: IecType) -> None:
        super().__init__(type_)
        self.declare("_M", "BOOL", False)

    def run(self, env: Any) -> None:
        del env
        clk = self.get("CLK")
        self.put("Q", bool(clk) and not self.vars["_M"].value)
        self.vars["_M"].value = clk


class FTrig(NativeFb):
    """F_TRIG: detects a falling edge on ``CLK``."""

    decls = (("CLK", "BOOL", "input"), ("Q", "BOOL", "output"))

    def __init__(self, type_: IecType) -> None:
        super().__init__(type_)
        self.declare("_M", "BOOL", True)

    def run(self, env: Any) -> None:
        del env
        clk = self.get("CLK")
        self.put("Q", (not clk) and self.vars["_M"].value)
        self.vars["_M"].value = clk


class Ctu(NativeFb):
    """CTU: count-up counter.  ``CU`` edges increment ``CV``."""

    decls = (("CU", "BOOL", "input"), ("R", "BOOL", "input"), ("PV", "INT", "input"),
             ("Q", "BOOL", "output"), ("CV", "INT", "output"))

    def __init__(self, type_: IecType) -> None:
        super().__init__(type_)
        self.declare("_prev", "BOOL", False)

    def run(self, env: Any) -> None:
        del env
        cu = self.get("CU")
        if self.get("R"):
            self.put("CV", 0)
        elif cu and not self.vars["_prev"].value:
            self.put("CV", self.get("CV") + 1)
        self.put("Q", self.get("CV") >= self.get("PV"))
        self.vars["_prev"].value = cu


class Ctd(NativeFb):
    """CTD: count-down counter.  ``LD`` loads ``PV``, ``CD`` edges decrement."""

    decls = (("CD", "BOOL", "input"), ("LD", "BOOL", "input"), ("PV", "INT", "input"),
             ("Q", "BOOL", "output"), ("CV", "INT", "output"))

    def __init__(self, type_: IecType) -> None:
        super().__init__(type_)
        self.declare("_prev", "BOOL", False)

    def run(self, env: Any) -> None:
        del env
        cd = self.get("CD")
        if self.get("LD"):
            self.put("CV", self.get("PV"))
        elif cd and not self.vars["_prev"].value:
            self.put("CV", self.get("CV") - 1)
        self.put("Q", self.get("CV") <= 0)
        self.vars["_prev"].value = cd


class Ctud(NativeFb):
    """CTUD: up/down counter."""

    decls = (("CU", "BOOL", "input"), ("CD", "BOOL", "input"),
             ("R", "BOOL", "input"), ("LD", "BOOL", "input"), ("PV", "INT", "input"),
             ("QU", "BOOL", "output"), ("QD", "BOOL", "output"), ("CV", "INT", "output"))

    def __init__(self, type_: IecType) -> None:
        super().__init__(type_)
        self.declare("_prev_up", "BOOL", False)
        self.declare("_prev_dn", "BOOL", False)

    def run(self, env: Any) -> None:
        del env
        cu, cd = self.get("CU"), self.get("CD")
        cv = self.get("CV")
        if self.get("R"):
            cv = 0
        else:
            if self.get("LD"):
                cv = self.get("PV")
            if cu and not self.vars["_prev_up"].value:
                cv += 1
            if cd and not self.vars["_prev_dn"].value:
                cv -= 1
        self.put("CV", cv)
        self.put("QU", cv >= self.get("PV"))
        self.put("QD", cv <= 0)
        self.vars["_prev_up"].value = cu
        self.vars["_prev_dn"].value = cd


class Rs(NativeFb):
    """RS: reset-dominant latch."""

    decls = (("SET", "BOOL", "input"), ("RESET1", "BOOL", "input"), ("Q1", "BOOL", "output"))

    def run(self, env: Any) -> None:
        del env
        if self.get("RESET1"):
            self.put("Q1", False)
        elif self.get("SET"):
            self.put("Q1", True)


class Sr(NativeFb):
    """SR: set-dominant latch."""

    decls = (("SET1", "BOOL", "input"), ("RESET", "BOOL", "input"), ("Q1", "BOOL", "output"))

    def run(self, env: Any) -> None:
        del env
        if self.get("SET1"):
            self.put("Q1", True)
        elif self.get("RESET"):
            self.put("Q1", False)


_STANDARD_FB_CLASSES: dict[str, type[NativeFb]] = {
    "TON": Ton,
    "TOF": Tof,
    "TP": Tp,
    "R_TRIG": RTrig,
    "F_TRIG": FTrig,
    "CTU": Ctu,
    "CTD": Ctd,
    "CTUD": Ctud,
    "RS": Rs,
    "SR": Sr,
}


def standard_fb_types() -> dict[str, IecType]:
    """Type objects for the standard FBs, sharing the class in ``impl``."""
    out: dict[str, IecType] = {}
    for name, cls in _STANDARD_FB_CLASSES.items():
        out[name] = IecType(name, FB, impl=cls)
    return out
