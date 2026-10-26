"""The assertion layer: what a test needs and a UI does not.

``Testbench`` wraps a :class:`~microtuss.core.session.Session` and records
expectations instead of raising them, so one run reports every mismatch.  It
lives outside the core so that a GUI can drive the engine without pulling in
the notion of a failing assertion.
"""

from .bench import Expectation, Testbench, run_scenario

__all__ = ["Expectation", "Testbench", "run_scenario"]
