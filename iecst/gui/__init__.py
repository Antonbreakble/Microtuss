"""A window onto a block, built on :mod:`iecst.core` and nothing else.

This is the fourth layer, a peer of :mod:`iecst.cli`: it imports the core, never
the testing layer and never the CLI, so a change to the command line cannot
break the window and neither can drag the other's dependencies in.  The rule is
checked in ``tests/test_layering.py`` rather than trusted.

Importing this package does **not** import tkinter -- only :func:`main` needs
it, and it is reached through ``__getattr__`` so that ``from iecst.gui.panel
import Panel`` stays usable on a machine with no display (in a test run, for
instance).
"""

from __future__ import annotations

from typing import Any

from iecst.gui.panel import Panel, Row, decode_text, render, runnable_pous

__all__ = ["Gui", "Panel", "Row", "decode_text", "main", "render", "runnable_pous"]


def __getattr__(name: str) -> Any:
    # The window lives in a module that imports tkinter, which is present but
    # heavy; keep it out of the way until something actually opens a window.
    if name in ("Gui", "main"):
        from iecst.gui import app

        return getattr(app, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
