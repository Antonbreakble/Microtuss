"""The same bench inside a normal test framework.

    python3 examples/04_unittest.py     # or: python3 -m pytest examples/

Nothing in microtuss depends on a test runner -- ``Testbench`` is an ordinary
object, so ``unittest``, ``pytest`` and a bare ``assert`` all work.  The
``failures`` list is what lets a test report *several* mismatches per run
instead of stopping at the first one.
"""

import sys
import unittest
from pathlib import Path

# This file sits in examples/, so running it directly puts examples/ -- not
# the repository root -- on sys.path, and `microtuss` would not be importable.
# Add the root.  After `pip install -e .` the package is on the path anyway,
# and this line changes nothing.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from microtuss import Testbench  # noqa: E402

HERE = Path(__file__).resolve().parent
VALVE = HERE / "valve.st"


def valve(cycle_time: str = "100ms") -> Testbench:
    return Testbench.from_file(VALVE, "FB_Valve", cycle_time=cycle_time)


class TestValve(unittest.TestCase):
    def test_starts_closed(self):
        tb = valve()
        tb.run(1)
        self.assertFalse(tb.get("Open"))
        self.assertFalse(tb.get("Busy"))

    def test_opens_after_the_travel_time(self):
        tb = valve()
        tb.scan(Cmd=True)
        tb.run(20)
        self.assertTrue(tb.get("Open"))
        self.assertFalse(tb.get("Busy"))

    def test_reports_while_travelling(self):
        tb = valve()
        tb.scan(Cmd=True)
        self.assertTrue(tb.get("Busy"), "the valve is still moving")
        self.assertFalse(tb.get("Open"), "and is not open yet")

    def test_expectation_failures_are_collected_not_raised(self):
        tb = valve()
        tb.scan(Cmd=True)
        tb.expect(Open=True, Busy=False)  # both are wrong this early
        self.assertEqual(2, len(tb.failures))
        # A failure message names the variable, both values, and where it was:
        self.assertIn("OPEN: expected TRUE, got FALSE", tb.failures[0].describe())
        self.assertIn("at scan 1", tb.failures[0].describe())

    def test_a_longer_travel_time_delays_the_output(self):
        tb = valve()
        tb.set(TravelTime="T#5s")
        tb.scan(Cmd=True)
        tb.run(20)
        self.assertFalse(tb.get("Open"), "T#5s is not over after 2s")
        tb.run(31)
        self.assertTrue(tb.get("Open"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
