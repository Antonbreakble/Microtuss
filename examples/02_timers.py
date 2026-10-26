"""Timers run on a virtual clock, so a test never depends on wall-clock speed.

    python3 examples/02_timers.py

``cycle_time`` says how much time one scan represents.  ``T#2s`` at 100 ms per
scan is therefore 20 scans -- on a loaded machine or an idle one, the result is
the same.  The scan that *starts* the timer reports ET = 0, so the output
changes one scan after the nominal travel time.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from iecst import Testbench  # noqa: E402

HERE = Path(__file__).resolve().parent

SCANS_TO_TRAVEL = 21  # T#2s at 100ms per scan, plus the scan that starts the timer


def show(tb: Testbench) -> None:
    print(
        f"scan {tb.scans:>2}  t={tb.now / 1_000_000:>4.0f}ms  "
        f"ET={tb.get('travel.ET') / 1_000_000:>4.0f}ms  "
        f"Open={tb.get('Open')}"
    )


tb = Testbench.from_file(HERE / "valve.st", "FB_Valve", cycle_time="100ms")

tb.scan(Cmd=True)
show(tb)

tb.run(SCANS_TO_TRAVEL - 2)
show(tb)
tb.expect(Open=False, Busy=True)  # one scan short of the travel time

tb.step()
show(tb)
tb.expect(Open=True, Busy=False)  # the timer has elapsed

print(tb.summary())
if tb.failures:
    raise SystemExit(1)
