"""The smallest useful test: drive an input, count scans, assert an output.

    python3 examples/01_first_test.py

Nothing here knows about CODESYS.  ``valve.st`` is parsed, run for a number of
scans, and the outputs are read back as ordinary Python values.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from iecst import Testbench  # noqa: E402

HERE = Path(__file__).resolve().parent

tb = Testbench.from_file(HERE / "valve.st", "FB_Valve", cycle_time="10ms")

tb.scan(Cmd=True)  # set Cmd, then run exactly one scan
tb.run(200)  # 200 more scans = 2 s of virtual time

# expect() records the assertion instead of raising, so one run reports every
# mismatch rather than stopping at the first.
tb.expect(Open=True, Busy=False)
print(tb.summary())

if tb.failures:
    raise SystemExit(1)
