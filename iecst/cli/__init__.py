"""The command-line front end.

Kept as a package rather than a module so that the shell stays a *peer* of the
other front ends: ``iecst.core`` is the engine, ``iecst.testing`` is the
assertion layer, ``iecst.cli`` is one way in, and a GUI would be another.
Nothing outside this package should import :mod:`iecst.cli.commands`.
"""

from .commands import build_parser, main

__all__ = ["build_parser", "main"]
