"""A project of several files, including a library that overrides a built-in.

    python3 examples/05_a_project.py

Everything under ``project/`` is loaded at once -- DUTs, the utility library
and the block under test -- exactly as the files arrive from CODESYS.  Two
things are worth noticing:

* ``Limits`` is a user DUT whose members carry defaults, so the block starts
  out with ``Lo = 10`` and ``Hi = 90`` without anyone assigning them.
* ``ton1`` in ``Utils.st`` shadows the interpreter's built-in ``TON``, so the
  block is tested against the timer it will actually run with.
"""

import sys
from pathlib import Path

# This file sits in examples/, so running it directly puts examples/ -- not
# the repository root -- on sys.path, and `microtuss` would not be importable.
# Add the root.  After `pip install -e .` the package is on the path anyway,
# and this line changes nothing.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from microtuss import Testbench  # noqa: E402

HERE = Path(__file__).resolve().parent
PROJECT = HERE / "project"

# The whole folder: each file is one CODESYS object.
SOURCES = {str(p): p.read_text(encoding="utf-8-sig") for p in sorted(PROJECT.glob("*.st"))}

from microtuss.core.model import load_sources  # noqa: E402


def tank(**kwargs) -> Testbench:
    return Testbench(load_sources(SOURCES), "FB_Tank", cycle_time="100ms", **kwargs)


# -- the DUT supplies its own defaults --------------------------------------

tb = tank()
tb.run(1)
print("defaults from the T_Limits DUT")
print(f"  Limits.Lo    -> {tb.get('Limits.Lo')!r}")
print(f"  Limits.Hi    -> {tb.get('Limits.Hi')!r}")
print(f"  Percent      -> {tb.get('Percent')!r}   (50 is halfway from 10 to 90)")

# -- the level decides the state --------------------------------------------

tb = tank()
tb.scan(Fill=True, Level=20.0)
print("\nbelow the low limit")
print(f"  State        -> {tb.state()['STATE']!r}")

tb = tank()
tb.scan(Fill=True, Level=95.0)
print("\nat the high limit, on the first scan")
print(f"  State        -> {tb.state()['STATE']!r}   (the settle timer has not run yet)")
print(f"  Overflowing  -> {tb.get('Overflowing')!r}")
tb.run(3)
print(f"  State        -> {tb.state()['STATE']!r}   (after SettleTime)")

# -- a DUT can be written as a whole value ----------------------------------

tb = tank()
tb.set(Limits={"lo": 0.0, "hi": 50.0})
tb.scan(Fill=True, Level=25.0)
print("\nlimits replaced with one dict")
print(f"  Percent      -> {tb.get('Percent')!r}")

# -- library functions are callable in their own right ---------------------

tb = tank()
call = tb.runtime.call
print("\nlibrary functions called directly")
print(f"  Pct(25, 0, 50) -> {call('Pct', v=25.0, lo=0.0, hi=50.0)!r}")

# -- a typo is reported, not ignored ---------------------------------------

tb = tank()
try:
    tb.set(Limits={"lol": 0.0})
except Exception as exc:
    print("\na misspelled member:")
    print(f"  {exc}")

print("\nall of the above ran without an assertion; the checks live in")
print("06_pytest_style.py")
