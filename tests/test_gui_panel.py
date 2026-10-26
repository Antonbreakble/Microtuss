"""The GUI's logic, exercised without opening a window.

Everything the window decides is in ``microtuss.gui.panel``, so it can be tested
here: which blocks a folder offers, how a typed value becomes a value, and what
one scan writes and reads.  No tkinter is imported by any of it.
"""

import tempfile
import unittest
from pathlib import Path

from microtuss.core.errors import IecError, IecRuntimeError, IecTypeError
from microtuss.gui.panel import Panel, decode_text, render, runnable_pous

PROJECT = Path("examples/project")


class TestWhatAFolderOffers(unittest.TestCase):
    def test_only_blocks_that_can_be_driven_are_offered(self):
        # examples/project also holds the library TON and the FUNCTION Pct;
        # neither has an instance, so neither belongs in the picker.
        self.assertEqual(["FB_TANK", "TON"], runnable_pous(PROJECT))

    def test_a_bad_folder_reports_rather_than_raising_something_odd(self):
        with self.assertRaises(IecError):
            runnable_pous("no/such/folder")


class TestDecodingWhatTheUserTyped(unittest.TestCase):
    """The form knows the declared type, so it decodes instead of guessing."""

    def setUp(self):
        self.panel = Panel()
        self.panel.open(PROJECT, "FB_Tank", cycle_time="100ms")

    def variable(self, name):
        return self.panel.row(name).variable

    def test_a_number_is_read_as_a_number(self):
        # This is the case --set cannot get right without guessing: the entry
        # holds text, the declaration says REAL, so "95" means 95.0.
        self.assertEqual(95.0, decode_text(self.variable("Level"), "95"))
        self.assertEqual(95.5, decode_text(self.variable("Level"), " 95.5 "))

    def test_a_time_stays_text_for_the_engine_to_decode(self):
        # "200ms" is not a Python literal; the engine owns that spelling.
        self.assertEqual("200ms", decode_text(self.variable("SettleTime"), "200ms"))
        self.panel.set_active("SettleTime", True)
        self.panel.set_text("SettleTime", "200ms")
        self.panel.step()
        self.assertEqual(200_000_000, self.panel.session.get("SettleTime"))

    def test_an_enumeration_is_given_by_member_name(self):
        # examples/project has no enumeration on an input, so this needs one.
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, "M.st").write_text(
                "TYPE E_Mode : (Off, Manual, Auto); END_TYPE\n"
                "FUNCTION_BLOCK FB_M\n"
                "VAR_INPUT Mode : E_Mode; END_VAR\n"
                "VAR_OUTPUT Seen : INT; END_VAR\n"
                "    Seen := Mode;\n"
                "END_FUNCTION_BLOCK\n",
                encoding="utf-8",
            )
            panel = Panel()
            panel.open(folder, "FB_M")

            # The member names are what the form puts in the dropdown.
            self.assertEqual(["OFF", "MANUAL", "AUTO"], list(panel.row("Mode").variable.enum_members))

            panel.set_active("Mode", True)
            panel.set_text("Mode", "AUTO")
            panel.step()
            self.assertEqual(2, panel.session.get("Seen"))

    def test_a_struct_is_written_as_json(self):
        self.panel.set_active("Limits", True)
        self.panel.set_text("Limits", '{"lo": 0.0, "hi": 20.0}')
        self.panel.step()
        self.assertEqual(0.0, self.panel.session.get("Limits.Lo"))
        self.assertEqual(20.0, self.panel.session.get("Limits.Hi"))

    def test_a_struct_entry_starts_at_the_declared_value(self):
        # So a DUT can be edited in place rather than written from scratch.
        self.assertEqual('{"LO": 10.0, "HI": 90.0}', self.panel.row("Limits").text)

    def test_text_that_is_not_a_number_says_so(self):
        with self.assertRaises(IecTypeError) as caught:
            decode_text(self.variable("Level"), "high")
        self.assertIn("not a valid REAL", caught.exception.message)

    def test_malformed_json_says_so(self):
        with self.assertRaises(IecTypeError):
            decode_text(self.variable("Limits"), "{lo: 1}")

    def test_a_bool_reads_words_as_well_as_a_checkbox(self):
        boolean = self.variable("Fill")
        self.assertIs(True, decode_text(boolean, "TRUE"))
        self.assertIs(False, decode_text(boolean, "false"))
        with self.assertRaises(IecTypeError):
            decode_text(boolean, "maybe")


class TestRunningScans(unittest.TestCase):
    def setUp(self):
        self.panel = Panel()
        self.panel.open(PROJECT, "FB_Tank", cycle_time="100ms")

    def test_only_driven_inputs_are_written(self):
        self.panel.set_active("Fill", True)
        self.panel.set_text("Fill", "TRUE")
        self.panel.set_active("Level", True)
        self.panel.set_text("Level", "95")
        self.panel.step()

        # Level was ticked and typed, so it took effect; Limits was not, so it
        # still holds the value the type declared.
        self.assertEqual(95.0, self.panel.session.get("Level"))
        self.assertEqual(10.0, self.panel.session.get("Limits.Lo"))

    def test_an_untouched_input_keeps_the_default(self):
        self.panel.step()
        self.assertEqual(50.0, self.panel.session.get("Level"))

    def test_a_run_holds_the_inputs_for_every_scan(self):
        self.panel.set_active("Fill", True)
        self.panel.set_text("Fill", "TRUE")
        self.panel.set_active("Level", True)
        self.panel.set_text("Level", "95")
        self.panel.run(3)

        self.assertEqual(3, self.panel.scans)
        self.assertEqual(300_000_000, self.panel.now)
        # SettleTime is T#300ms and the first scan reports ET = 0, so full is
        # reached on the fourth scan -- three is not yet enough.
        self.assertEqual("FILLING", self.panel.session.snapshot()["STATE"])
        self.panel.step()
        self.assertEqual("FULL", self.panel.session.snapshot()["STATE"])

    def test_only_watched_outputs_are_read_back(self):
        self.panel.set_active("Fill", True)
        self.panel.set_text("Fill", "TRUE")
        self.panel.set_active("Level", True)
        self.panel.set_text("Level", "95")
        self.panel.set_active("State", True)
        self.panel.step()

        # State only: PERCENT and OVERFLOWING were left unticked, and the
        # readout shows the enumeration by member name, not by ordinal.
        self.assertEqual({"STATE": "FILLING"}, self.panel.read())

    def test_the_status_line_counts_scans_and_virtual_time(self):
        self.panel.run(2)
        self.assertEqual("FB_Tank · 2 прохода · T#200ms", self.panel.describe())

    def test_the_status_line_agrees_with_the_number(self):
        self.panel.run(1)
        self.assertIn("1 проход ·", self.panel.describe())
        self.panel.run(4)
        self.assertIn("5 проходов ·", self.panel.describe())

    def test_a_bad_value_reaches_the_caller_instead_of_vanishing(self):
        self.panel.set_active("Level", True)
        self.panel.set_text("Level", "not a number")
        with self.assertRaises(IecTypeError):
            self.panel.step()

    def test_an_error_inside_the_block_reaches_the_caller_too(self):
        # The free run has to be able to stop on this, so it must not be
        # swallowed: a block that divides by an input it was handed.
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, "Div.st").write_text(
                "FUNCTION_BLOCK FB_Div\n"
                "VAR_INPUT d : INT; END_VAR\n"
                "VAR_OUTPUT r : INT; END_VAR\n"
                "    r := 10 / d;\n"
                "END_FUNCTION_BLOCK\n",
                encoding="utf-8",
            )
            panel = Panel()
            panel.open(folder, "FB_Div")
            panel.set_active("d", True)
            panel.set_text("d", "0")
            with self.assertRaises(IecRuntimeError):
                panel.step()

    def test_reset_starts_the_same_block_over(self):
        self.panel.set_active("Fill", True)
        self.panel.set_text("Fill", "TRUE")
        self.panel.run(4)
        self.assertEqual(4, self.panel.scans)

        self.panel.reset()
        self.assertEqual(0, self.panel.scans)
        self.assertEqual(0, self.panel.now)
        self.assertEqual("FB_TANK", self.panel.pou.upper())
        # A fresh instance: nothing has run, so the outputs are at their
        # declared defaults rather than where the previous run left them.
        self.assertEqual("IDLE", self.panel.session.snapshot()["STATE"])

    def test_reset_keeps_the_setup_the_user_made(self):
        # The window's checkboxes and entries are not redrawn by a reset, so
        # the panel behind them has to still agree with what they show.
        self.panel.set_active("Fill", True)
        self.panel.set_text("Fill", "TRUE")
        self.panel.set_active("Level", True)
        self.panel.set_text("Level", "95")
        self.panel.set_active("State", True)
        self.panel.run(1)

        self.panel.reset()
        self.assertTrue(self.panel.row("Fill").active)
        self.assertEqual("95", self.panel.row("Level").text)
        self.assertTrue(self.panel.row("State").active)
        # One that was never ticked stays unticked, at its declared value.
        self.assertFalse(self.panel.row("Limits").active)
        self.assertEqual('{"LO": 10.0, "HI": 90.0}', self.panel.row("Limits").text)

        # And the kept setup really is applied on the next scan.
        self.panel.step()
        self.assertEqual(95.0, self.panel.session.get("Level"))


class TestRender(unittest.TestCase):
    def test_a_bool_is_shown_the_way_a_controller_shows_it(self):
        self.assertEqual("TRUE", render(True))
        self.assertEqual("FALSE", render(False))

    def test_a_struct_is_shown_as_json(self):
        self.assertEqual('{"LO": 1.0}', render({"LO": 1.0}))


if __name__ == "__main__":
    unittest.main()
