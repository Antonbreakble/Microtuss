"""What the GUI does, with no widgets in it.

The window is a thin shell: every decision -- which POU can be run, how a typed
value becomes a value, what one scan means -- lives here, where it can be tested
without a display.

The one interesting difference from the command line is how a value is read.
``--set Level=50`` has to *guess*, because at the moment the argument is parsed
nothing knows what ``Level`` is declared as.  A form does know: it draws a field
per variable, so it decodes against the declared type and never has to guess
whether ``50`` is the number 50 or the string ``"50"``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from iecst.core.errors import IecError, IecTypeError
from iecst.core.session import Session, Variable, load
from iecst.core.stdlib import format_time
from iecst.core.values import ARRAY, BOOL, ENUM, STRING, STRUCT, TIME

__all__ = ["Panel", "Row", "decode_text", "render", "runnable_pous"]


def runnable_pous(folder: "str | Path") -> list[str]:
    """The POUs in *folder* that can be driven from the panel, sorted.

    A project folder also carries library blocks and plain ``FUNCTION``s; only
    the ones with an instance make sense as "the block under test" (see
    ``Session.from_folder``).
    """
    model = load(folder)
    return sorted(
        entry.name
        for entry in model.pous.values()
        if entry.kind in ("FUNCTION_BLOCK", "PROGRAM")
    )


def decode_text(variable: Variable, text: str) -> Any:
    """Turn what the user typed into a value for *variable*, by its declaration.

    :class:`~iecst.core.session.Variable` describes the target, so the
    conversion is decided rather than guessed:

    * ``BOOL`` never gets here -- the form draws a checkbox, and its state is
      the value.  Anything else arriving for a ``BOOL`` is read as a word.
    * a ``STRUCT`` or ``ARRAY`` is written as JSON, and the fields inside it
      still accept the PLC spellings (``"200ms"``, an enumeration member).
    * a number is parsed as one; ``TIME`` and enumerations are left as text for
      the engine to decode, since ``"200ms"`` and ``"Full"`` are not Python
      literals.
    """
    kind = variable.type.kind
    text = text.strip()

    if kind in (STRUCT, ARRAY):
        try:
            return json.loads(text)
        except ValueError as exc:
            raise IecTypeError(f"'{variable.name}': {exc}") from exc

    if kind == BOOL:
        return _as_bool(text)

    # ENUM, TIME and STRING travel as text and are decoded by the engine, which
    # owns spellings such as "Auto" and "200ms".  This has to come before the
    # numeric branches: an enumeration is integer-backed, so ``is_integer`` is
    # true for it too, and "Auto" is not the number its member stands for.
    if kind in (ENUM, TIME, STRING):
        return text

    if variable.type.is_float:
        return _parse(variable, text, float)
    if variable.type.is_integer:
        return _parse(variable, text, int)
    return text


def _as_bool(text: str) -> bool:
    lowered = text.strip().lower()
    if lowered in ("true", "1", "yes"):
        return True
    if lowered in ("false", "0", "no", ""):
        return False
    raise IecTypeError(f"'{text}' is not TRUE or FALSE")


def _parse(variable: Variable, text: str, caster: Any) -> Any:
    try:
        return caster(text)
    except ValueError as exc:
        raise IecTypeError(
            f"'{variable.name}': '{text}' is not a valid {variable.type_name}"
        ) from exc


def _plural(count: int) -> str:
    """Russian agreement for "проход": 1 проход, 2 прохода, 5 проходов."""
    if count % 10 == 1 and count % 100 != 11:
        return "проход"
    if count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14):
        return "прохода"
    return "проходов"


def render(value: Any) -> str:
    """A value as the panel shows it: readable, and editable back.

    Containers go to JSON with the engine's own spelling (enums by member name,
    TIME as ``T#...``) because that same text is what the entry for a DUT input
    hands back when the user edits it.
    """
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return "" if value is None else str(value)


@dataclass
class Row:
    """One variable of the interface, and what the user wants done with it."""

    variable: Variable
    text: str = ""
    active: bool = False


class Panel:
    """A loaded POU, its interface as rows, and the scan control around it."""

    def __init__(self, cycle_time: str = "10ms") -> None:
        self.cycle_time = cycle_time
        self.folder: str | None = None
        self.pou: str | None = None
        self.session: Session | None = None
        self.inputs: list[Row] = []
        self.outputs: list[Row] = []

    # -- loading -----------------------------------------------------------

    def open(
        self, folder: "str | Path", pou: str | None = None, cycle_time: str | None = None
    ) -> None:
        """Load a project folder and lay its interface out as rows.

        Raises :class:`IecError` if the folder does not hold a usable POU, so
        the caller can show the message and keep the previous project loaded.
        """
        cycle_time = cycle_time or self.cycle_time
        session = Session.from_folder(folder, pou, cycle_time=cycle_time)

        self.folder = str(folder)
        self.pou = session.pou_name
        self.cycle_time = cycle_time
        self.session = session
        # Each entry starts at the value the block declares, so a DUT can be
        # edited in place instead of written from scratch.
        state = session.snapshot()
        self.inputs = [
            Row(v, text=render(state.get(v.name))) for v in session.inputs()
        ]
        self.outputs = [Row(v) for v in session.outputs()]

    def reset(self) -> None:
        """Start the same block over: fresh instance, scan 0.

        The setup survives -- which inputs are driven, what they hold, which
        outputs are watched.  Resetting is for running the same scenario again,
        not for throwing the scenario away.
        """
        if not self.folder:
            return
        setup = {
            row.variable.name: (row.text, row.active)
            for row in self.inputs + self.outputs
        }
        self.open(self.folder, self.pou, self.cycle_time)
        for row in self.inputs + self.outputs:
            remembered = setup.get(row.variable.name)
            if remembered:
                row.text, row.active = remembered

    @property
    def loaded(self) -> bool:
        return self.session is not None

    def _require(self) -> Session:
        if self.session is None:
            raise IecError("no project loaded")
        return self.session

    # -- what the user asked for -------------------------------------------

    def row(self, name: str) -> Row:
        wanted = name.upper()
        for row in self.inputs + self.outputs:
            if row.variable.name.upper() == wanted:
                return row
        raise IecError(f"no variable named '{name}'")

    def set_text(self, name: str, text: str) -> None:
        """Remember what an entry shows, without writing it to the block."""
        self.row(name).text = text

    def set_active(self, name: str, active: bool) -> None:
        """Mark a variable as driven (an input) or watched (an output)."""
        self.row(name).active = active

    # -- running -----------------------------------------------------------

    def write_inputs(self) -> None:
        """Push every driven input into the block, and nothing else.

        A variable the user did not tick keeps whatever the last scan left in
        it -- including outputs, which are readable and writable but are not
        part of what this panel drives.
        """
        session = self._require()
        for row in self.inputs:
            if row.active:
                session.set(row.variable.name, decode_text(row.variable, row.text))

    def step(self) -> None:
        """Write the driven inputs, then run exactly one scan."""
        self.write_inputs()
        self._require().step()

    def run(self, scans: int) -> None:
        """Write the driven inputs once, then hold them for *scans* scans."""
        self.write_inputs()
        self._require().run(scans)

    def read(self) -> dict[str, str]:
        """The watched outputs as display strings, keyed by name."""
        session = self._require()
        state = session.snapshot()
        return {
            row.variable.name: render(state.get(row.variable.name))
            for row in self.outputs
            if row.active
        }

    # -- the status line ---------------------------------------------------

    @property
    def scans(self) -> int:
        return self.session.scans if self.session else 0

    @property
    def now(self) -> int:
        return self.session.now if self.session else 0

    def describe(self) -> str:
        """``FB_Tank · 42 прохода · T#4s200ms`` -- what the status line shows."""
        if not self.loaded:
            return "проект не загружен"
        return f"{self.pou} · {self.scans} {_plural(self.scans)} · {format_time(self.now)}"
