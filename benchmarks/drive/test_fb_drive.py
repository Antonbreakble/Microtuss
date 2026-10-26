"""A worked example: a project made of DUTs, a utils library and an FB.

    python3 benchmarks/drive/test_fb_drive.py

This mirrors how a real CODESYS project is laid out -- the block under test
refers to user-defined types and to a library TON that overrides the
interpreter's built-in one.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from iecst import Testbench  # noqa: E402

HERE = Path(__file__).resolve().parent

# The library files are listed first; a POU declared in them wins over the
# standard library, which is why TON here is the project's own implementation.
INCLUDES = [HERE / "src" / "duts.st", HERE / "src" / "utils.st"]

CONFIG = {
    "mode": "E_OpMode#Automatic",
    "speedrange": {"lo": 0.0, "hi": 1500.0},
    "ramptime": "200ms",
    "name": "Main",
}


def bench() -> Testbench:
    return Testbench.from_file(
        HERE / "src" / "fb_drive.st", "FB_Drive", includes=INCLUDES, cycle_time="10ms"
    )


def test_takes_a_struct_dut_as_an_input() -> None:
    tb = bench()
    tb.set(CFG=CONFIG)
    tb.run(1)
    # The FB copies the config into an array and back out again; members are
    # matched case-insensitively on the way in.
    assert tb.get("ACTIVECONFIG.SPEEDRANGE.HI") == 1500.0
    assert tb.get("ACTIVECONFIG.NAME") == "Main"


def test_startup_takes_the_configured_ramp_time() -> None:
    tb = bench()
    tb.scan(Enable=True, CFG=CONFIG)

    # The library TON needs RampTime = 200 ms = 21 scans (the scan that turns
    # IN on establishes the start instant), so the drive is Starting before it.
    tb.run(19)
    assert tb.state()["STATE"] == "STARTING", tb.summary()

    tb.run(2)
    assert tb.state()["STATE"] == "RUNNING", tb.summary()


def test_speed_is_clamped_to_the_configured_range() -> None:
    tb = bench()
    tb.set(Enable=True, CFG=CONFIG)
    tb.run(30)  # past the ramp, so the drive is running
    assert tb.state()["STATE"] == "RUNNING"

    tb.set(FEEDBACK=9999.0)
    tb.step()
    assert tb.get("SPEED") == 1500.0, "clamped to SpeedRange.Hi"

    tb.set(FEEDBACK=-50.0)
    tb.step()
    assert tb.get("SPEED") == 0.0, "clamped to SpeedRange.Lo"


def test_manual_mode_does_not_follow_feedback() -> None:
    tb = bench()
    manual = dict(CONFIG, mode="E_OpMode#Manual")
    tb.set(Enable=True, CFG=manual, FEEDBACK=800.0)
    tb.run(30)
    assert tb.state()["STATE"] == "RUNNING"
    assert tb.get("SPEED") == 0.0, "manual mode ignores feedback"


def test_missing_feedback_raises_a_fault() -> None:
    tb = bench()
    tb.set(Enable=True, CFG=CONFIG, FEEDBACK=500.0)
    tb.run(30)
    assert tb.state()["STATE"] == "RUNNING"

    # Take the feedback away and the fault timer fires after 500 ms.  500 ms is
    # 50 cycles at 10 ms, plus the cycle that starts the timer and reports ET=0,
    # so the trip happens on the 51st scan.
    tb.set(FEEDBACK=0.0)
    tb.run(51)
    assert tb.get("FAULTED") is True
    assert tb.state()["STATE"] == "FAULTED"


def test_fault_is_cleared_by_reset() -> None:
    tb = bench()
    tb.set(Enable=True, CFG=CONFIG, FEEDBACK=0.0)
    tb.run(80)
    assert tb.get("FAULTED") is True

    tb.scan(RESET=True)
    assert tb.get("FAULTED") is False
    assert tb.state()["STATE"] == "IDLE"


def test_disable_stops_the_drive() -> None:
    tb = bench()
    tb.set(Enable=True, CFG=CONFIG, FEEDBACK=700.0)
    tb.run(30)
    assert tb.state()["STATE"] == "RUNNING"

    tb.scan(Enable=False)
    assert tb.state()["STATE"] == "STOPPING"

    tb.step()
    assert tb.state()["STATE"] == "IDLE"
    assert tb.get("SPEED") == 0.0


def test_function_from_utils_can_be_called_directly() -> None:
    tb = bench()
    # A plain FUNCTION is testable on its own, without driving the whole FB.
    assert tb.runtime.call("Fb_Scale", v=75.0, inlo=0.0, inhi=100.0, outlo=0.0, outhi=1500.0) == 1125.0
    # Out-of-range input is clamped rather than extrapolated.
    assert tb.runtime.call("Fb_Scale", v=200.0, inlo=0.0, inhi=100.0, outlo=0.0, outhi=1500.0) == 1500.0


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in tests:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(tests)} example tests passed")
