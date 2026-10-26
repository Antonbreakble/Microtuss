"""The one entry point into the engine, with no user interface attached.

Everything a front end needs is here, and nothing a front end needs to know
about is elsewhere.  The CLI calls this module; a GUI would call the same
module; a test suite calls it too.  The engine itself -- lexer, parser, type
system, interpreter -- is reachable through :class:`Session`, never by a front
end importing internals directly.

The shape of a session::

    session = Session.from_sources({...}, "FB_Valve", cycle_time="10ms")

    session.inputs()          # what can be driven, and of what type
    session.outputs()         # what it produces
    session.set("Cmd", True)  # write
    session.step()            # one scan, advancing the virtual clock
    session.get("Open")       # read
    session.snapshot()        # everything at once, JSON-friendly

Two properties matter for a UI:

* **Nothing here prints or exits.**  Errors are raised, values are returned.
* **``inputs()`` / ``outputs()`` describe the POU's interface**, so a form can
  be built without the caller having to parse anything itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from iecst.core.errors import IecError, IecNameError
from iecst.core.interpreter import Runtime
from iecst.core.model import Model, load_sources, validate
from iecst.core.values import (
    BOOL,
    ENUM,
    REFERENCE,
    STRUCT,
    TIME,
    IecType,
    parse_time,
)

__all__ = [
    "Session",
    "Variable",
    "check",
    "load",
    "load_files",
    "load_sources",
    "sources_from_folder",
    "validate",
]


# -- loading ---------------------------------------------------------------


def load(
    files: "str | Path | Iterable[str | Path] | Mapping[str, str]",
) -> Model:
    """Load ``.st`` sources into a linked :class:`Model`.

    Accepts a path, several paths, or a name-to-text mapping -- the last is how
    a GUI holds unsaved editor buffers, where there is no file to read.
    """
    if isinstance(files, Mapping):
        return load_sources(dict(files))
    if isinstance(files, (str, Path)):
        files = [files]
    return load_sources(_read_files(files))


def load_files(files: "str | Path | Iterable[str | Path]") -> Model:
    """Load one or more ``.st`` files into a linked :class:`Model`."""
    return load(files)


def check(
    files: "str | Path | Iterable[str | Path] | Mapping[str, str]",
) -> list[IecError]:
    """Parse and link without executing; return the problems found.

    An empty list means the sources are well formed.  Errors inside a POU's
    variable declarations surface here rather than at the first scan, which is
    what a "did my export load?" command wants.
    """
    return validate(load(files))


def sources_from_folder(
    folder: "str | Path", *, pattern: str = "*.st", recursive: bool = True
) -> dict[str, str]:
    """Read a folder the way CODESYS delivers a project.

    One file per object means the block under test, its DUTs and its library
    all sit in the same tree, so loading is "everything under here".  The
    files are sorted, which keeps loading deterministic: a name clash resolves
    the same way on every machine.
    """
    root = Path(folder)
    paths = root.rglob(pattern) if recursive else root.glob(pattern)
    return _read_files(sorted(paths))


def _read_files(paths: Iterable[str | Path]) -> dict[str, str]:
    # utf-8-sig because a CODESYS export routinely carries a BOM.
    return {
        str(path): Path(path).read_text(encoding="utf-8-sig", errors="replace")
        for path in paths
    }


# -- describing the POU's interface ----------------------------------------


@dataclass(frozen=True)
class Variable:
    """One variable of the POU under test, as a UI would present it."""

    name: str
    type: IecType
    direction: str          # "input" | "output" | "inout" | "local"
    writable: bool

    @property
    def type_name(self) -> str:
        return self.type.describe()

    @property
    def enum_members(self) -> dict[str, int]:
        """Member names of an ENUM, or ``{}`` -- what a dropdown would show."""
        t = self.type
        while t.kind in (REFERENCE,):
            t = t.target or t
        if t.kind == ENUM:
            return dict(t.members or {})
        return {}

    @property
    def is_time(self) -> bool:
        return self.type.kind == TIME

    @property
    def is_bool(self) -> bool:
        return self.type.kind == BOOL

    @property
    def is_struct(self) -> bool:
        return self.type.kind == STRUCT


# Which VAR section reads as which direction.  VAR_OUTPUT and VAR are readable
# but driving them is a test technique, not the block's interface, so only the
# declared inputs are advertised as such.
_DIRECTIONS = {
    "input": "input",
    "output": "output",
    "inout": "inout",
    "local": "local",
}


class Session:
    """A loaded POU, ready to be driven by any front end."""

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
        self.pou_name = pou
        self.cycle_ns = parse_time(cycle_time)

    # -- construction ------------------------------------------------------

    @classmethod
    def from_sources(
        cls, sources: Mapping[str, str], pou: str, **kwargs: Any
    ) -> "Session":
        return cls(load_sources(dict(sources)), pou, **kwargs)

    @classmethod
    def from_folder(cls, folder: "str | Path", pou: str | None = None, **kwargs: Any) -> "Session":
        """Load a project folder, inferring the POU when there is only one."""
        return cls.from_sources(sources_from_folder(folder), pou or "", **kwargs)

    @classmethod
    def from_files(
        cls,
        files: "str | Path | Iterable[str | Path]",
        pou: str | None = None,
        **kwargs: Any,
    ) -> "Session":
        model = load_files(files)
        return cls(model, pou or _sole_pou(model), **kwargs)

    # -- describing --------------------------------------------------------

    @property
    def pou_kind(self) -> str:
        """``FUNCTION_BLOCK``, ``FUNCTION`` or ``PROGRAM``."""
        return self.runtime.entry.kind

    def variables(self) -> list[Variable]:
        """Every declared variable of the POU, in declaration order.

        This is the whole interface a front end needs: a form can be laid out
        from it, and a CLI can list what ``--set`` accepts.
        """
        out: list[Variable] = []
        seen: set[str] = set()
        for decl in self.runtime.entry.defn.vars:
            direction = _DIRECTIONS.get(decl.section, "local")
            for name in decl.names:
                if name in seen:
                    continue
                seen.add(name)
                cell = self.runtime.instance.vars.get(name)
                t = cell.type if cell is not None else decl.type
                out.append(
                    Variable(
                        name=name,
                        type=t,
                        direction=direction,
                        writable=direction in ("input", "inout", "local"),
                    )
                )
        return out

    def inputs(self) -> list[Variable]:
        return [v for v in self.variables() if v.direction == "input"]

    def outputs(self) -> list[Variable]:
        return [v for v in self.variables() if v.direction == "output"]

    def variable(self, name: str) -> Variable:
        """Look up one variable, ignoring case, as the ST source would."""
        wanted = name.upper()
        for v in self.variables():
            if v.name.upper() == wanted:
                return v
        raise IecNameError(f"'{name}' is not declared by {self.pou_name}")

    # -- driving -----------------------------------------------------------

    def set(self, name: str, value: Any) -> None:
        self.runtime.set(name.upper(), value)

    def set_many(self, values: Mapping[str, Any]) -> None:
        for name, value in values.items():
            self.set(name, value)

    def get(self, name: str) -> Any:
        return self.runtime.get(name.upper())

    def step(self) -> None:
        """Run one scan and advance the virtual clock."""
        self.runtime.step()

    def run(self, scans: int = 1) -> None:
        self.runtime.run(scans)

    def call(self, name: str, **kwargs: Any) -> Any:
        """Invoke a ``FUNCTION`` by name -- no instance, no scan."""
        return self.runtime.call(name, **kwargs)

    def bind(self, name: str, value: Any = None):
        """The storage behind a ``VAR_IN_OUT``, for wiring two blocks together."""
        return self.runtime.bind(name.upper(), value)

    # -- reading state -----------------------------------------------------

    @property
    def now(self) -> int:
        """Virtual time in nanoseconds."""
        return self.runtime.now

    @property
    def scans(self) -> int:
        return self.runtime.scan

    def snapshot(self) -> dict[str, Any]:
        """Every top-level variable, TIME as ``T#...``, enums by member name."""
        return self.runtime.snapshot()

    def encode_for(self, name: str, value: Any) -> Any:
        """Translate a UI value the way :meth:`set` would, without setting it."""
        return self.runtime.encode_for(self.runtime._find_cell(name.upper()), value)

    def display(self, name: str, value: Any) -> Any:
        """Render a value for display: enum ordinals become member names."""
        return self.runtime.render_for(self.runtime._find_cell(name.upper()), value)


def _sole_pou(model: Model) -> str:
    """The only FUNCTION_BLOCK or PROGRAM in *model*, or a clear complaint."""
    candidates = sorted(
        e.name for e in model.pous.values() if e.kind in ("FUNCTION_BLOCK", "PROGRAM")
    )
    if len(candidates) != 1:
        raise IecNameError(
            "cannot infer the POU to run; pass pou= "
            f"(available: {', '.join(candidates) or 'none'})"
        )
    return candidates[0]
