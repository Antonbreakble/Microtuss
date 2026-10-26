"""A Structured Text interpreter for testing CODESYS function blocks.

The package is layered, and each layer only knows the ones below it::

    microtuss.core     the engine -- lexer, parser, type system, interpreter,
                   and `Session`, the one door into all of it
    microtuss.testing  `Testbench` -- expecting values, for test suites
    microtuss.cli      the command line

A GUI would be a fourth peer of ``cli``, built on ``core``.

For most work two names are enough::

    >>> from microtuss import Session, Testbench
    >>> session = Session.from_folder("FB_AI", "AnalogInput", cycle_time="10ms")
    >>> bench = Testbench.from_folder("FB_AI", "AnalogInput")

The re-exports below are resolved on first use rather than at import time.  A
front end that only wants the engine -- a GUI, say -- must be able to do
``import microtuss.core.session`` without dragging in the assertion layer or the
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
    # -- microtuss.core --------------------------------------------------------
    "SourceUnit": "microtuss.core.ast_nodes",
    "IecError": "microtuss.core.errors",
    "IecNameError": "microtuss.core.errors",
    "IecRuntimeError": "microtuss.core.errors",
    "IecSyntaxError": "microtuss.core.errors",
    "IecTypeError": "microtuss.core.errors",
    "Frame": "microtuss.core.interpreter",
    "Runtime": "microtuss.core.interpreter",
    "parse_time": "microtuss.core.interpreter",
    "Token": "microtuss.core.lexer",
    "tokenize": "microtuss.core.lexer",
    "Model": "microtuss.core.model",
    "load_sources": "microtuss.core.model",
    "Parser": "microtuss.core.parser",
    "parse_text": "microtuss.core.parser",
    "Session": "microtuss.core.session",
    "Variable": "microtuss.core.session",
    "check": "microtuss.core.session",
    "load": "microtuss.core.session",
    "sources_from_folder": "microtuss.core.session",
    "format_time": "microtuss.core.stdlib",
    # -- microtuss.testing -----------------------------------------------------
    "Expectation": "microtuss.testing.bench",
    "Testbench": "microtuss.testing.bench",
    "run_scenario": "microtuss.testing.bench",
}


def __getattr__(name: str) -> Any:
    """Import a re-exported name the first time it is asked for."""
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module 'microtuss' has no attribute '{name}'")
    from importlib import import_module

    return getattr(import_module(module_name), name)


def __dir__() -> list[str]:
    return sorted(__all__)
