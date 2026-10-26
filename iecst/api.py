"""The public surface of the package, geared towards writing tests.

Typical use::

    from iecst.api import Testbench

    tb = Testbench.from_file("FB_Motor.st", pou="FB_Motor", cycle_time="10ms")
    tb.set(Start=True, Setpoint=1000)
    tb.run(60)
    tb.expect(Running=True, Speed=1000)
    print(tb.summary())

Values are converted with the usual Python spellings: ``True`` for BOOL,
``100`` for INT, ``1.5`` for REAL, ``"1m30s"`` for TIME, ``"Color#Red"`` for an
enumeration member.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .errors import IecError, IecNameError
from .interpreter import Runtime
from .model import Model, load_sources, validate
from .stdlib import format_time
from .values import PointerValue


def load(files: "str | Path | Iterable[str | Path] | Mapping[str, str]") -> Model:
    """Load one or more ``.st`` files (or a name-to-text mapping) into a model."""
    if isinstance(files, Mapping):
        sources = {str(k): v for k, v in files.items()}
    else:
        if isinstance(files, (str, Path)):
            files = [files]
        sources = {}
        for f in files:
            path = Path(f)
            sources[str(path)] = path.read_text(encoding="utf-8", errors="replace")

    # Each file is parsed independently, but types and POUs may be spread
    # across them, so `load_sources` links the whole set before building.
    return load_sources(sources)


@dataclass
class Expectation:
    name: str
    expected: Any
    actual: Any
    ok: bool
    scanned: int
    time_ns: int

    def describe(self) -> str:
        mark = "PASS" if self.ok else "FAIL"
        if self.ok:
            return f"[{mark}] {self.name} == {_fmt(self.actual)}"
        return (
            f"[{mark}] {self.name}: expected {_fmt(self.expected)}, got {_fmt(self.actual)}"
            f"  (at scan {self.scanned}, t={format_time(self.time_ns)})"
        )


def _fmt(value: Any) -> str:
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float):
        return repr(value)
    return str(value)


class Testbench:
    """Drives a POU under test and records the assertions made against it."""

    # The name starts with "Test", which pytest would otherwise read as a test
    # class and warn about on every collected file that imports it.
    __test__ = False

    def __init__(
        self,
        model: Model,
        pou: str,
        *,
        cycle_time: str | int = "10ms",
        trace: bool = False,
    ) -> None:
        self.model = model
        self.runtime = Runtime(model, pou, cycle_time=cycle_time, trace=trace)
        self.expectations: list[Expectation] = []
        self.pou_name = pou

    # -- construction ------------------------------------------------------

    @classmethod
    def from_file(
        cls,
        path: "str | Path",
        pou: str | None = None,
        *,
        includes: "Sequence[str | Path] | None" = None,
        cycle_time: str | int = "10ms",
        trace: bool = False,
    ) -> "Testbench":
        """Load *path*, plus any *includes* (library files it depends on)."""
        files: list[str | Path] = list(includes or []) + [path]
        model = load(files)
        if pou is None:
            candidates = [
                e.name for e in model.pous.values() if e.defn.kind in ("FUNCTION_BLOCK", "PROGRAM")
            ]
            if len(candidates) != 1:
                raise IecNameError(
                    f"cannot infer the POU to test; pass pou= (available: {', '.join(sorted(candidates)) or 'none'})"
                )
            pou = candidates[0]
        return cls(model, pou, cycle_time=cycle_time, trace=trace)

    # -- driving -----------------------------------------------------------

    def set(self, name: str | None = None, value: Any = None, **values: Any) -> "Testbench":
        """Set one or more variables of the POU under test.

        Either ``set("Configs[2].Mode", "Automatic")`` or
        ``set(Enable=True, Feedback=700.0)``.
        """
        if name is None:
            self.runtime.set(**{k.upper(): v for k, v in values.items()})
        else:
            if values:
                raise IecNameError("set() takes either a name and a value, or keyword arguments")
            self.runtime.set(name.upper(), value)
        return self

    def set_many(self, values: Mapping[str, Any]) -> "Testbench":
        for name, value in values.items():
            self.runtime.set(name.upper(), value)
        return self

    def get(self, name: str) -> Any:
        return self.runtime.get(name.upper())

    def run(self, scans: int = 1) -> "Testbench":
        self.runtime.run(scans)
        return self

    def step(self) -> "Testbench":
        self.runtime.step()
        return self

    def scan(self, **values: Any) -> "Testbench":
        """Set inputs and run exactly one scan -- the common test idiom."""
        return self.set(**values).step()

    @property
    def now(self) -> int:
        return self.runtime.now

    @property
    def scans(self) -> int:
        return self.runtime.scan

    def encode(self, name: str, value: Any) -> Any:
        """Translate a Python literal the way :meth:`set` would."""
        try:
            cell = self.runtime._find_cell(name.upper())
        except IecError:
            return value
        return self.runtime.encode_for(cell, value)

    def _display(self, name: str, value: Any) -> Any:
        """Render *value* for an assertion message, naming enum members."""
        try:
            return self.runtime.render_for(self.runtime._find_cell(name.upper()), value)
        except IecError:
            return value

    # -- assertions --------------------------------------------------------

    def expect(self, **expected: Any) -> bool:
        """Assert several variables at once; returns True if all matched."""
        ok = True
        for name, want in expected.items():
            got = self.get(name)
            want = self.encode(name, want)
            matched = self._matches(want, got)
            if not matched:
                # Show an enumeration member by name: "expected STARTING, got
                # IDLE" is worth reading, "expected 2, got 1" is not.
                want = self._display(name, want)
                got = self._display(name, got)
            self.expectations.append(
                Expectation(
                    name=name.upper(),
                    expected=want,
                    actual=got,
                    ok=matched,
                    scanned=self.scans,
                    time_ns=self.now,
                )
            )
            ok = ok and matched
        return ok

    @staticmethod
    def _matches(want: Any, got: Any) -> bool:
        if isinstance(want, float) and isinstance(got, float):
            # REAL is stored as float32, so compare at that precision.
            return abs(want - got) <= max(1e-6, abs(want) * 1e-6)
        return want == got

    @property
    def failures(self) -> list[Expectation]:
        return [e for e in self.expectations if not e.ok]

    def summary(self) -> str:
        passed = len(self.expectations) - len(self.failures)
        lines = [f"{self.pou_name}: {passed}/{len(self.expectations)} expectations passed"]
        lines.extend(f"  {e.describe()}" for e in self.failures)
        return "\n".join(lines)

    def state(self) -> dict[str, Any]:
        """Current variable values, with TIME rendered as ``T#...``."""
        snapshot = self.runtime.snapshot()
        return {k: _jsonable(v) for k, v in snapshot.items()}


def _jsonable(value: Any) -> Any:
    if isinstance(value, PointerValue):
        return "<pointer>"
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    return value


def run_scenario(
    source: "str | Path",
    pou: str | None = None,
    *,
    cycle_time: str | int = "10ms",
    steps: "Sequence[tuple[Mapping[str, Any], int]] | None" = None,
    includes: "Sequence[str | Path] | None" = None,
) -> dict[str, Any]:
    """Run a list of ``(inputs, scans)`` steps and return the final variable state.

    This is the whole program in one call, which is handy for a quick check or
    for driving the tool from a script without building a full test bench.
    """
    tb = Testbench.from_file(source, pou, includes=includes, cycle_time=cycle_time)
    for inputs, scans in steps or []:
        tb.set_many(inputs)
        tb.run(scans)
    return tb.state()


def check(
    files: "str | Path | Iterable[str | Path] | Mapping[str, str]",
) -> list[IecError]:
    """Parse and link *files*, returning any problems found without executing."""
    model = load(files)
    return validate(model)
