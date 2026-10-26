"""A Structured Text interpreter for testing CODESYS function blocks.

Front end:

    >>> from iecst import parse_text
    >>> unit = parse_text(open("FB_Motor.st").read())
    >>> unit.pous[0].name
    'FB_MOTOR'
"""

from .api import Expectation, Testbench, check, load, run_scenario
from .ast_nodes import SourceUnit
from .errors import IecError, IecNameError, IecRuntimeError, IecSyntaxError, IecTypeError
from .interpreter import Runtime, parse_time
from .lexer import Token, tokenize
from .model import Model, load_sources
from .parser import Parser, parse_text
from .stdlib import format_time

__all__ = [
    "Expectation",
    "IecError",
    "IecNameError",
    "IecRuntimeError",
    "IecSyntaxError",
    "IecTypeError",
    "Model",
    "Parser",
    "Runtime",
    "SourceUnit",
    "Testbench",
    "Token",
    "check",
    "format_time",
    "load",
    "load_sources",
    "parse_text",
    "parse_time",
    "run_scenario",
    "tokenize",
]
