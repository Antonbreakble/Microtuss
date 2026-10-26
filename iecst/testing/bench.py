"""``Testbench`` -- the assertion layer, for people writing tests.

This is the layer that turns a :class:`~iecst.core.session.Session` into
something a test reads well in: ``expect(...)`` records a result instead of
raising, so one run reports every mismatch, and ``summary()`` prints them.

It is deliberately *not* part of the core.  A GUI does not want assertions; a
CLI only wants them for ``--expect``.  Both build on ``Session`` directly, and
import this module only when the concept of an expectation is actually needed.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from iecst.core.errors import IecError, IecNameError
from iecst.core.model import Model
from iecst.core.session import Session
from iecst.core.stdlib import format_time
from iecst.core.values import PointerValue

__all__ = ["Expectation", "Testbench", "run_scenario"]


@dataclass
class Expectation:
    """One recorded assertion and its outcome."""

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
        session: "Session | Model",
        pou: str | None = None,
        *,
        cycle_time: str | int = "10ms",
        trace: bool = False,
    ) -> None:
        # Accepting a bare Model keeps the common test idiom short --
        # ``Testbench(load_sources(...), "FB", cycle_time="10ms")`` -- since a
        # Model alone does not say which POU to run.  A Session already does,
        # so passing one needs no ``pou``.
        if isinstance(session, Session):
            if pou is not None:
                raise IecNameError("pass either a Session or a Model and POU, not both")
            self.session = session
        else:
            if pou is None:
                raise IecNameError("a Model needs the name of the POU to run")
            self.session = Session(session, pou, cycle_time=cycle_time, trace=trace)
        self.expectations: list[Expectation] = []

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
        files = list(includes or []) + [path]
        return cls(Session.from_files(files, pou, cycle_time=cycle_time, trace=trace))

    @classmethod
    def from_folder(
        cls,
        folder: "str | Path",
        pou: str | None = None,
        *,
        cycle_time: str | int = "10ms",
        trace: bool = False,
    ) -> "Testbench":
        """Load every ``.st`` under *folder* -- the CODESYS delivery shape."""
        # Session.from_folder owns the inference, so that the CLI, a GUI and
        # this all agree on which block was picked.
        return cls(Session.from_folder(folder, pou, cycle_time=cycle_time, trace=trace))

    @classmethod
    def from_sources(
        cls,
        sources: Mapping[str, str],
        pou: str,
        *,
        cycle_time: str | int = "10ms",
        trace: bool = False,
    ) -> "Testbench":
        """Load a name-to-text mapping -- what the benchmark suites use."""
        return cls(
            Session.from_sources(sources, pou, cycle_time=cycle_time, trace=trace)
        )

    # -- pass-through to the session ---------------------------------------

    @property
    def pou_name(self) -> str:
        return self.session.pou_name

    @property
    def model(self):
        return self.session.model

    @property
    def runtime(self):
        """The :class:`Runtime` underneath, for driving blocks by hand."""
        return self.session.runtime

    @property
    def now(self) -> int:
        return self.session.now

    @property
    def scans(self) -> int:
        return self.session.scans

    # -- driving -----------------------------------------------------------

    def set(self, name: str | None = None, value: Any = None, **values: Any) -> "Testbench":
        """Set one or more variables of the POU under test.

        Either ``set("Configs[2].Mode", "Automatic")`` or
        ``set(Enable=True, Feedback=700.0)``.
        """
        if name is None:
            self.session.set_many({k.upper(): v for k, v in values.items()})
        else:
            if values:
                raise IecNameError("set() takes either a name and a value, or keyword arguments")
            self.session.set(name, value)
        return self

    def set_many(self, values: Mapping[str, Any]) -> "Testbench":
        self.session.set_many(values)
        return self

    def get(self, name: str) -> Any:
        return self.session.get(name)

    def run(self, scans: int = 1) -> "Testbench":
        self.session.run(scans)
        return self

    def step(self) -> "Testbench":
        self.session.step()
        return self

    def scan(self, **values: Any) -> "Testbench":
        """Set inputs and run exactly one scan -- the common test idiom."""
        return self.set(**values).step()

    def encode(self, name: str, value: Any) -> Any:
        """Translate a Python literal the way :meth:`set` would."""
        try:
            return self.session.encode_for(name, value)
        except IecError:
            return value

    def _display(self, name: str, value: Any) -> Any:
        """Render *value* for an assertion message, naming enum members."""
        try:
            return self.session.display(name, value)
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
        return {k: _jsonable(v) for k, v in self.session.snapshot().items()}


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
