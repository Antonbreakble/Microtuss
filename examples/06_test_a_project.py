"""A finished test suite for the mini-project: the shape to copy.

    python3 examples/06_test_a_project.py     # or: python3 -m pytest examples/

The conventions worth keeping:

* one bench per test, built by a helper -- tests never share state;
* one behaviour per test, and its name says which;
* assert on the *outputs* of the block, not on its internals;
* when a behaviour takes several scans, say so in a comment with the arithmetic
  instead of leaving a bare number.
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
from microtuss.core.model import load_sources  # noqa: E402

HERE = Path(__file__).resolve().parent
PROJECT = HERE / "project"

# Parsing a project is the slow part, so do it once and share the model.
# Each test still gets its own instance, so nothing leaks between them.
MODEL = load_sources(
    {str(p): p.read_text(encoding="utf-8-sig") for p in sorted(PROJECT.glob("*.st"))}
)


def tank(cycle_time: str = "100ms") -> Testbench:
    return Testbench(MODEL, "FB_Tank", cycle_time=cycle_time)


class TestTankDefaults(unittest.TestCase):
    def test_percent_follows_the_level(self):
        for level, expected in ((10.0, 0.0), (50.0, 50.0), (90.0, 100.0)):
            with self.subTest(level=level):
                tb = tank()
                tb.scan(Fill=True, Level=level)
                self.assertEqual(expected, tb.get("Percent"))

    def test_limits_come_from_the_dut(self):
        tb = tank()
        tb.scan(Fill=True, Level=95.0)
        # T_Limits declares Hi := 90, so 95 is above it without any assignment.
        self.assertTrue(tb.get("Overflowing"))


class TestTankStates(unittest.TestCase):
    def test_idle_when_not_filling(self):
        tb = tank()
        tb.scan(Fill=False)
        self.assertEqual("IDLE", tb.state()["STATE"])

    def test_filling_below_the_low_limit(self):
        tb = tank()
        tb.scan(Fill=True, Level=5.0)  # below Lo = 10
        self.assertEqual("FILLING", tb.state()["STATE"])

    def test_full_needs_the_level_to_settle(self):
        tb = tank()
        tb.scan(Fill=True, Level=95.0)
        # SettleTime is T#300ms, i.e. 3 scans at 100ms; this is the first.
        self.assertEqual("FILLING", tb.state()["STATE"])

        tb.run(2)
        self.assertEqual("FILLING", tb.state()["STATE"], "2 scans is not enough")

        tb.step()
        self.assertEqual("FULL", tb.state()["STATE"], "300 ms have passed")

    def test_a_brief_spike_does_not_latch_full(self):
        tb = tank()
        tb.scan(Fill=True, Level=95.0)  # high...
        tb.scan(Fill=True, Level=50.0)  # ...but not for long enough
        tb.run(5)
        self.assertNotEqual("FULL", tb.state()["STATE"])


class TestTankExpectations(unittest.TestCase):
    def test_expect_reports_every_mismatch_at_once(self):
        tb = tank()
        tb.scan(Fill=True, Level=95.0)
        tb.expect(State="FULL", Percent=100.0)
        # State is legitimately FILLING and Percent is 106.25, so two fail.
        self.assertEqual(2, len(tb.failures))
        for failure in tb.failures:
            self.assertIn("at scan 1", failure.describe())


if __name__ == "__main__":
    unittest.main(verbosity=2)
