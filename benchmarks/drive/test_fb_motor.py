"""A worked example: testing an IEC 61131-3 function block from Python.

Run it with::

    python3 benchmarks/drive/test_fb_motor.py

The point of the example is the *shape* of a test: set the inputs, advance
scans by a defined amount of virtual time, then assert on the outputs.  Nothing
here talks to CODESYS, and no wall-clock time is involved, so the run is
reproducible.
"""

import sys
from pathlib import Path

# Allow running this file directly from a checkout, without installing.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from iecst import Testbench  # noqa: E402  (import follows the path bootstrap)

FB = Path(__file__).resolve().parent / "src" / "fb_motor.st"


def test_motor_reaches_setpoint() -> None:
    tb = Testbench.from_file(FB, "FB_Motor", cycle_time="10ms")

    # One scan with the start command, then let the timer elapse.
    tb.scan(START=True, STOP=False, SETPOINT=1000)
    tb.run(59)  # 500 ms of virtual time at 10 ms per scan

    tb.expect(RUNNING=True, STATE=10)  # 10 == E_State#Running
    assert tb.expect(RUNNING=True), tb.summary()

    # Speed is derived from the elapsed timer, not from Setpoint: the FB's
    # last statement is Speed := INT(ton1.ET / T#1ms), so after 500 ms of
    # elapsed time it reports 500 regardless of the setpoint.
    assert tb.get("SPEED") == 500, tb.summary()

    # Members of a nested FB instance are reached with a dotted path; the raw
    # value of a TIME is nanoseconds, and state() renders it for humans.
    assert tb.get("TON1.ET") == 500_000_000
    assert tb.state()["TON1"]["ET"] == "T#500ms"


def test_position_advances_while_running() -> None:
    tb = Testbench.from_file(FB, "FB_Motor")
    tb.scan(START=True)
    start = tb.get("POSITION")
    tb.run(4)
    # POSITION is a VAR_IN_OUT of the POU under test, so it advances even
    # though no external variable was wired to it.
    assert tb.get("POSITION") == start + 4


def test_stop_resets_the_outputs() -> None:
    tb = Testbench.from_file(FB, "FB_Motor")
    tb.set(START=True)
    tb.run(5)
    assert tb.get("STATE") == 10

    tb.scan(START=False, STOP=True)
    assert tb.get("STATE") == 0  # E_State#Idle
    assert tb.get("RUNNING") is False
    assert tb.get("SPEED") == 0


def test_stopping_on_the_same_scan_as_starting_wins() -> None:
    # IF Start AND NOT Stop -- so Start together with Stop must not run.
    tb = Testbench.from_file(FB, "FB_Motor")
    tb.scan(START=True, STOP=True)
    assert tb.get("STATE") == 0


def test_call_a_plain_function_directly() -> None:
    tb = Testbench.from_file(FB, "FB_Motor")
    assert tb.runtime.call("F_Clamp", v=1500, lo=0, hi=1000) == 1000
    assert tb.runtime.call("F_Clamp", v=-5, lo=0, hi=1000) == 0
    assert tb.runtime.call("F_Clamp", v=42, lo=0, hi=1000) == 42


def test_assertion_failure_reports_the_scan() -> None:
    tb = Testbench.from_file(FB, "FB_Motor")
    tb.set(START=True)
    tb.run(3)
    ok = tb.expect(SPEED=12345)
    assert not ok
    message = tb.failures[0].describe()
    assert "expected 12345" in message
    assert "at scan 3" in message


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in tests:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(tests)} example tests passed")
