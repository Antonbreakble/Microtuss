"""Tests for a real ``AnalogInput`` block, taken from a CODESYS project.

    python3 benchmarks/analog_input/test_analog_input.py

The block is used exactly as the project ships it: every file under ``src/``
is loaded as-is, one file per CODESYS object, with no edits and no stubs.

What the block does, from the source:

* ``rIn`` (4..20 mA by default) is scaled onto ``PvLow..PvHigh`` (0..100) and
  then filtered by ``EmaFilter`` with the factor ``PAR.FiltrationRate``.
* ``OUT.PV`` is that filtered value less ``PAR.PvShift``.
* Four alarm bits compare the *filtered* value against LL/L/H/HH, each with a
  deadband of ``(PvHigh - PvLow) / 100 * DeadbandRate``.
* ``OUT.Status`` packs eight conditions into individual bits, which is why the
  interpreter has to support ``Status.3 := ...``.
* A ``Command`` pulse shifts or unshifts the output: SET_SHIFT captures the
  current value into ``PvShift``, DROP_SHIFT zeroes it again.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from iecst import Testbench  # noqa: E402

HERE = Path(__file__).resolve().parent
PROJECT = HERE / "src"

# Status word bit numbers, from the comment block in AIOut.st.
BIT_FAULT = 0
BIT_IMITATION = 1
BIT_FILTERING = 2
BIT_HH = 3
BIT_H = 4
BIT_L = 5
BIT_LL = 6
BIT_POS_OVERFLOW = 8
BIT_NEG_OVERFLOW = 10


def bench(**kwargs) -> Testbench:
    """Load every .st file under src/ and instantiate the block."""
    sources = {str(p): p.read_text(encoding="utf-8-sig") for p in sorted(PROJECT.rglob("*.st"))}
    from iecst.model import load_sources

    return Testbench(load_sources(sources), "AnalogInput", cycle_time="10ms", **kwargs)


def settle(tb: Testbench, raw: float, scans: int = 12) -> None:
    """Hold *raw* at the input until the filter has caught up."""
    for _ in range(scans):
        tb.set(RIN=raw)
        tb.step()


def status(tb: Testbench) -> int:
    return tb.get("OUT.STATUS")


def bit(tb: Testbench, index: int) -> bool:
    return bool((status(tb) >> index) & 1)


# -- scaling ---------------------------------------------------------------


def test_defaults_come_from_the_parameter_dut() -> None:
    tb = bench()
    # AI_PAR ships defaults -- InLow := 4, HH := 80 -- and the block depends on
    # them, so they must be in place before anything is driven.
    assert tb.get("PAR.INLOW") == 4.0
    assert tb.get("PAR.INHIGH") == 20.0
    assert tb.get("PAR.HH") == 80.0
    assert tb.get("PAR.FILTRATIONRATE") == 1.0


def test_scales_the_raw_input_onto_the_output_range() -> None:
    tb = bench()
    for raw, expected in ((4.0, 0.0), (12.0, 50.0), (20.0, 100.0)):
        settle(tb, raw)
        assert tb.get("OUT.PV") == expected, f"{raw} mA should read {expected}"


def test_a_custom_input_range_is_honoured() -> None:
    tb = bench()
    tb.set(PAR={"inlow": 0.0, "inhigh": 10.0})
    settle(tb, 2.5)
    assert tb.get("OUT.PV") == 25.0


def test_a_zero_width_input_range_is_a_fault_not_a_division() -> None:
    tb = bench()
    tb.set(PAR={"inlow": 5.0, "inhigh": 5.0})
    tb.set(RIN=5.0)
    tb.step()
    assert bit(tb, BIT_FAULT) is True
    tb.set(PAR={"inlow": 4.0, "inhigh": 20.0})
    tb.step()
    assert bit(tb, BIT_FAULT) is False


# -- filtering -------------------------------------------------------------


def test_the_filter_is_off_at_the_default_rate() -> None:
    tb = bench()
    settle(tb, 12.0)
    # FiltrationRate is 1, so the filtered value is the input itself...
    assert tb.get("FILTEREDVALUE") == 50.0
    # ...and bit 2 of the status word reports "not filtering".
    assert bit(tb, BIT_FILTERING) is False


def test_the_filter_slows_the_output_down() -> None:
    tb = bench()
    settle(tb, 4.0)  # start from a settled 0 %, not from an unset input
    assert tb.get("OUT.PV") == 0.0

    tb.set(PAR={"filtrationrate": 0.25})
    tb.step()
    # The status word is built at the end of a scan, so the bit only appears
    # once the block has run with the new rate.
    assert bit(tb, BIT_FILTERING) is True

    tb.set(RIN=20.0)
    tb.step()
    # A quarter of the way from 0 to 100 on the first scan.
    assert tb.get("OUT.PV") == 25.0, tb.get("OUT.PV")

    for _ in range(40):
        tb.set(RIN=20.0)
        tb.step()
    assert abs(tb.get("OUT.PV") - 100.0) < 0.1, "should converge on the target"


def test_a_rate_above_one_is_clamped_by_limit_r() -> None:
    tb = bench()
    tb.set(PAR={"filtrationrate": 5.0})
    tb.set(RIN=20.0)
    tb.step()
    # LIMIT_R clamps Alpha to 1, so the filter passes the value straight through
    # rather than overshooting past it.
    assert tb.get("OUT.PV") == 100.0


# -- alarms ----------------------------------------------------------------


def test_no_alarms_in_the_middle_of_the_range() -> None:
    tb = bench()
    settle(tb, 12.0)
    assert (bit(tb, BIT_LL), bit(tb, BIT_L), bit(tb, BIT_H), bit(tb, BIT_HH)) == (
        False, False, False, False,
    )


def test_ll_alarm_at_the_bottom() -> None:
    tb = bench()
    settle(tb, 4.0)  # 0 %
    assert bit(tb, BIT_LL) is True
    assert tb.get("LLALARM") is True
    assert bit(tb, BIT_L) is False


def test_l_alarm_band() -> None:
    tb = bench()
    settle(tb, 8.0)  # 25 %, below L = 40
    assert bit(tb, BIT_L) is True
    assert bit(tb, BIT_LL) is False

    settle(tb, 11.2)  # 45 %, above L
    assert bit(tb, BIT_L) is False


def test_h_alarm_band() -> None:
    tb = bench()
    settle(tb, 16.0)  # 75 %, above H = 60 and below HH = 80
    assert bit(tb, BIT_H) is True
    assert bit(tb, BIT_HH) is False


def test_hh_alarm_at_the_top() -> None:
    tb = bench()
    settle(tb, 20.0)  # 100 %
    assert bit(tb, BIT_HH) is True
    assert tb.get("HHALARM") is True
    # HH is exclusive of H: once HH trips, H no longer does.
    assert bit(tb, BIT_H) is False


def test_the_alarm_bands_have_a_deadband() -> None:
    # The deadband is added to the H threshold, so the block needs to go
    # further past 60 % before H trips.
    # 4..20 mA over 0..100 %: 13.92 mA is 62 %.
    plain = bench()
    settle(plain, 12.32)  # 52 %...
    assert bit(plain, BIT_H) is False

    plain2 = bench()
    settle(plain2, 13.92)  # ...62 %, past H = 60
    assert bit(plain2, BIT_H) is True, "trips without a deadband"

    dead = bench()
    dead.set(PAR={"deadbandrate": 5.0})  # 5 % of 0..100
    settle(dead, 13.92)  # same 62 %, but the threshold is now 65
    assert bit(dead, BIT_H) is False, "the deadband holds it off"


def test_the_deadband_applies_to_both_ends() -> None:
    # L uses ``<= L - Deadband``, so it too needs a bigger excursion.
    plain = bench()
    settle(plain, 10.0)  # 37.5 %, below L = 40
    assert bit(plain, BIT_L) is True

    dead = bench()
    dead.set(PAR={"deadbandrate": 5.0})
    settle(dead, 10.0)  # threshold is now 35
    assert bit(dead, BIT_L) is False


def test_an_alarm_uses_the_filtered_value_not_the_raw_one() -> None:
    tb = bench()
    tb.set(PAR={"filtrationrate": 0.1})
    tb.set(RIN=20.0)  # raw is at 100 %
    tb.step()
    # One tenth of the way there: nowhere near the HH threshold yet.
    assert tb.get("OUT.PV") == 10.0
    assert bit(tb, BIT_HH) is False

    for _ in range(60):
        tb.set(RIN=20.0)
        tb.step()
    assert bit(tb, BIT_HH) is True


# -- range overflow --------------------------------------------------------


def test_positive_overflow_sets_its_own_bits() -> None:
    tb = bench()
    tb.set(RIN=23.0)  # above InPositiveOverflow = 22.5
    tb.step()
    assert tb.get("ISPOSITIVEOVERFLOW") is True
    assert bit(tb, BIT_POS_OVERFLOW) is True
    assert bit(tb, BIT_FAULT) is True
    assert bit(tb, BIT_NEG_OVERFLOW) is False


def test_negative_overflow_sets_its_own_bits() -> None:
    tb = bench()
    tb.set(RIN=3.0)  # below InNegativeOverflow = 3.7
    tb.step()
    assert tb.get("ISNEGATIVEOVERFLOW") is True
    assert bit(tb, BIT_NEG_OVERFLOW) is True
    assert bit(tb, BIT_FAULT) is True


def test_the_value_is_still_scaled_when_out_of_range() -> None:
    tb = bench()
    settle(tb, 23.0)
    # Overflow is reported, not clipped: 23 mA is 118.75 % of a 4..20 range.
    assert tb.get("OUT.PV") == 118.75


def test_overflows_do_not_trip_the_alarm_bits_by_themselves() -> None:
    tb = bench()
    tb.set(RIN=23.0)
    tb.step()
    # HH is true here, but because 118.75 > 80, not because of the overflow.
    assert bit(tb, BIT_HH) is True
    assert bit(tb, BIT_LL) is False


# -- diagnostics switch ----------------------------------------------------


def test_without_diagnostics_the_fault_bits_stay_clear() -> None:
    tb = bench()
    tb.set(WITHDIAGNOSTIC=False, RIN=23.0)
    tb.step()
    # The conditions are still computed...
    assert tb.get("ISPOSITIVEOVERFLOW") is True
    # ...but are not reported.
    assert bit(tb, BIT_FAULT) is False
    assert bit(tb, BIT_POS_OVERFLOW) is False


def test_bhealth_is_folded_into_the_fault_bit() -> None:
    tb = bench()
    tb.set(BHEALTH=True, RIN=12.0)
    tb.step()
    assert bit(tb, BIT_FAULT) is True


# -- imitation -------------------------------------------------------------


def test_imitation_takes_the_value_from_rinimit() -> None:
    tb = bench()
    tb.set(IMITATION=True, RIN=4.0, RINIMIT=16.0)
    tb.run(1)
    assert tb.get("OUT.PV") == 75.0, "the imitation input drives the block"
    assert bit(tb, BIT_IMITATION) is True


def test_the_imitation_bit_is_clear_when_off() -> None:
    tb = bench()
    settle(tb, 12.0)
    assert bit(tb, BIT_IMITATION) is False


# -- shift command ---------------------------------------------------------


def test_set_shift_captures_the_current_value() -> None:
    tb = bench()
    settle(tb, 12.0)
    assert tb.get("OUT.PV") == 50.0

    # The command is latched at the end of one scan and acted on by the next.
    tb.set(COMMAND=1)
    tb.step()
    assert tb.get("PAR.PVSHIFT") == 0.0, "not yet"
    tb.step()
    assert tb.get("PAR.PVSHIFT") == 50.0
    # PV for the scan that takes the shift is still the old one: PvShift is
    # applied earlier in the body than the CASE that updates it.
    assert tb.get("OUT.PV") == 50.0

    tb.step()
    assert tb.get("OUT.PV") == 0.0, "now PV is the filtered value less the shift"

    # Holding the input steady, the output stays at zero.
    settle(tb, 12.0)
    assert tb.get("OUT.PV") == 0.0


def test_drop_shift_restores_the_value() -> None:
    tb = bench()
    settle(tb, 12.0)
    tb.set(COMMAND=1)
    tb.step()
    tb.step()
    tb.step()
    assert tb.get("PAR.PVSHIFT") == 50.0
    assert tb.get("OUT.PV") == 0.0

    tb.set(COMMAND=2)
    tb.step()  # latches DROP_SHIFT
    tb.step()  # PvShift goes to 0; PV is computed before that
    assert tb.get("PAR.PVSHIFT") == 0.0
    tb.step()
    assert tb.get("OUT.PV") == 50.0, "the shift is gone"


def test_the_command_input_is_cleared_after_it_is_taken() -> None:
    tb = bench()
    tb.set(COMMAND=1)
    tb.step()
    assert tb.get("COMMAND") == 0
    assert tb.get("STCMD") == 1


def test_no_command_leaves_the_shift_alone() -> None:
    tb = bench()
    tb.set(PAR={"pvshift": 10.0})
    settle(tb, 12.0)
    assert tb.get("OUT.PV") == 40.0


# -- dependencies in their own right ---------------------------------------


def test_limit_r_clamps_at_both_ends() -> None:
    tb = bench()
    call = tb.runtime.call
    assert call("LIMIT_R", IN=5.0, LOW=0.0, HIGH=1.0) == 1.0
    assert call("LIMIT_R", IN=-5.0, LOW=0.0, HIGH=1.0) == 0.0
    assert call("LIMIT_R", IN=0.5, LOW=0.0, HIGH=1.0) == 0.5


def test_limit_r_defaults_its_bounds_to_zero_and_one() -> None:
    tb = bench()
    # Low and High carry DUT-style defaults in the POU header.
    assert tb.runtime.call("LIMIT_R", IN=7.0) == 1.0


def test_scale_r_maps_the_range() -> None:
    tb = bench()
    call = tb.runtime.call
    assert call("SCALE_R", RIN=12.0, RINLOW=4.0, RINHIGH=20.0, ROUTLOW=0.0, ROUTHIGH=100.0) == 50.0
    assert call("SCALE_R", RIN=4.0, RINLOW=4.0, RINHIGH=20.0, ROUTLOW=0.0, ROUTHIGH=100.0) == 0.0


def test_scale_r_returns_zero_for_a_zero_width_range() -> None:
    tb = bench()
    assert tb.runtime.call(
        "SCALE_R", RIN=1.0, RINLOW=1.0, RINHIGH=1.0, ROUTLOW=0.0, ROUTHIGH=100.0
    ) == 0.0


def test_ema_filter_moves_towards_the_input() -> None:
    from iecst.interpreter import Frame

    tb = bench()
    rt = tb.runtime
    # An FB can be driven on its own: run its POU against a fresh instance each
    # scan, which is what the parent block does when it calls InnerFilter(...).
    entry = rt.model.pou("EMAFILTER")
    fb = rt.instantiate(rt.model.type_of("EMAFILTER"))
    fb.vars["ALPHA"].value = 0.5

    seen = []
    for _ in range(3):
        fb.vars["IN"].value = 100.0
        rt.execute_block(entry.defn.body, Frame(fb.vars, entry=entry, instance=fb))
        seen.append(fb.vars["OUT"].value)
    assert seen == [50.0, 75.0, 87.5], seen


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in tests:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - a test runner reports, not raises
            failed += 1
            print(f"FAIL {fn.__name__}: {type(exc).__name__}: {exc}")
        else:
            print(f"ok   {fn.__name__}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
