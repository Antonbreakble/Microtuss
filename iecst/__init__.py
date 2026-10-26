"""A Structured Text interpreter for testing CODESYS function blocks.

The package is layered, and each layer only knows the ones below it::

    iecst.core     the engine -- lexer, parser, type system, interpreter,
                   and `Session`, the one door into all of it
    iecst.testing  `Testbench` -- expecting values, for test suites
    iecst.cli      the command line

A GUI would be a fourth peer of ``cli``, built on ``core``.

For most work two names are enough::

    >>> from iecst import Session, Testbench
    >>> session = Session.from_folder("FB_AI", "AnalogInput", cycle_time="10ms")
    >>> bench = Testbench.from_folder("FB_AI", "AnalogInput")

The re-exports below are resolved on first use rather than at import time.  A
front end that only wants the engine -- a GUI, say -- must be able to do
``import iecst.core.session`` without dragging in the assertion layer or the
command line, and an eager import here would defeat that.
"""

from typing import Any

__all__ = [
    "Expectation",
    "Frame",
    "IecError",
    "IecNameError",
    "IecRuntimeError",
    "IecSyntaxError",
    "IecTypeError",
    "Model",
    "Parser",
    "Runtime",
    "Session",
    "SourceUnit",
    "Testbench",
    "Token",
    "Variable",
    "check",
    "format_time",
    "load",
    "load_sources",
    "parse_text",
    "parse_time",
    "run_scenario",
    "sources_from_folder",
    "tokenize",
]

# Public name -> the module that owns it.  The module here is the *original*,
# so the re-export never becomes a second definition to keep in sync.
_EXPORTS: dict[str, str] = {
    # -- iecst.core --------------------------------------------------------
    "SourceUnit": "iecst.core.ast_nodes",
    "IecError": "iecst.core.errors",
    "IecNameError": "iecst.core.errors",
    "IecRuntimeError": "iecst.core.errors",
    "IecSyntaxError": "iecst.core.errors",
    "IecTypeError": "iecst.core.errors",
    "Frame": "iecst.core.interpreter",
    "Runtime": "iecst.core.interpreter",
    "parse_time": "iecst.core.interpreter",
    "Token": "iecst.core.lexer",
    "tokenize": "iecst.core.lexer",
    "Model": "iecst.core.model",
    "load_sources": "iecst.core.model",
    "Parser": "iecst.core.parser",
    "parse_text": "iecst.core.parser",
    "Session": "iecst.core.session",
    "Variable": "iecst.core.session",
    "check": "iecst.core.session",
    "load": "iecst.core.session",
    "sources_from_folder": "iecst.core.session",
    "format_time": "iecst.core.stdlib",
    # -- iecst.testing -----------------------------------------------------
    "Expectation": "iecst.testing.bench",
    "Testbench": "iecst.testing.bench",
    "run_scenario": "iecst.testing.bench",
}


def __getattr__(name: str) -> Any:
    """Import a re-exported name the first time it is asked for."""
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module 'iecst' has no attribute '{name}'")
    from importlib import import_module

    return getattr(import_module(module_name), name)


def __dir__() -> list[str]:
    return sorted(__all__)
