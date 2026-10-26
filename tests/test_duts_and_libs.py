"""Tests for user-defined DUTs and for libraries that override the stdlib.

The scenarios here mirror how real CODESYS projects are laid out: a ``.st``
file of DUTs (``TYPE ... END_TYPE``), a ``utils`` file with shared function
blocks, and the block under test referring to both.
"""

import unittest

from iecst.testing.bench import Testbench
from iecst.core.errors import IecError, IecNameError, IecTypeError
from iecst.core.interpreter import Runtime
from iecst.core.model import load_sources

DUTS = """
TYPE
    E_Mode : (Idle, Auto, Manual := 5, Fault);
    T_Limits : STRUCT
        Lo : INT;
        Hi : INT;
    END_STRUCT;
    T_Axis : STRUCT
        Pos : REAL;
        Mode : E_Mode;
        Lim : T_Limits;
    END_STRUCT;
    T_AxisArr : ARRAY[1..3] OF T_Axis;
    T_LimArr : ARRAY[0..1] OF T_Limits;
END_TYPE
"""

# A block named TON living in a library file: it must shadow the built-in one.
UTILS = """
FUNCTION_BLOCK TON
VAR_INPUT
    IN : BOOL;
    PT : TIME;
END_VAR
VAR_OUTPUT
    Q : BOOL;
    ET : TIME;
END_VAR
VAR
    _at : TIME;
    _timing : BOOL;
END_VAR
    IF NOT IN THEN
        Q := FALSE;
        ET := T#0s;
        _timing := FALSE;
        RETURN;
    END_IF;
    IF NOT _timing THEN
        _at := TIME();
        _timing := TRUE;
    END_IF;
    ET := TIME() - _at;
    IF ET >= PT THEN
        Q := TRUE;
        ET := PT;
    ELSE
        Q := FALSE;
    END_IF;
END_FUNCTION_BLOCK

FUNCTION FC_Wrap : INT
VAR_INPUT
    v : INT;
    lim : T_Limits;
END_VAR
    FC_Wrap := LIMIT(lim.Lo, v, lim.Hi);
END_FUNCTION
"""

MAIN = """
FUNCTION_BLOCK FB_Use
VAR_INPUT
    Cmd : BOOL;
    PT : TIME;
    Lim : T_Limits;
END_VAR
VAR_OUTPUT
    Done : BOOL;
    Elapsed : TIME;
    AxisPos : REAL;
    Wrapped : INT;
END_VAR
VAR
    t : TON;
    axis : T_Axis;
    axes : T_AxisArr;
    mode : E_Mode;
END_VAR
    t(IN := Cmd, PT := PT, Q => Done, ET => Elapsed);

    axis.Pos := Lim.Hi - Lim.Lo;
    axis.Mode := E_Mode#Manual;
    axis.Lim := Lim;
    axes[2] := axis;
    AxisPos := axes[2].Pos + axes[2].Lim.Lo;
    mode := axes[2].Mode;

    Wrapped := FC_Wrap(v := 999, lim := Lim);
END_FUNCTION_BLOCK
"""


def bench(**kwargs):
    return Testbench(
        load_sources({
            "duts.st": DUTS,
            "utils.st": UTILS,
            "main.st": MAIN,
        } | kwargs),
        "FB_Use",
        cycle_time="10ms",
    )


class TestUserDefinedTypes(unittest.TestCase):
    def test_struct_dut_resolves_across_files(self):
        tb = bench()
        tb.set(LIM={"lo": 10, "hi": 40})
        tb.run(1)
        self.assertEqual(tb.get("AXISPOS"), 40.0)

    def test_struct_accepted_from_a_dict_case_insensitively(self):
        tb = bench()
        # Members are Lo/Hi; a lowercase dict must still bind.
        tb.set(LIM={"lo": 1, "hi": 5})
        tb.run(1)
        self.assertEqual(tb.get("AXIS.LIM.LO"), 1)
        self.assertEqual(tb.get("AXIS.LIM.HI"), 5)

    def test_unknown_struct_member_is_reported(self):
        tb = bench()
        with self.assertRaises(IecTypeError) as cm:
            tb.set(LIM={"low": 1, "hi": 5})
        self.assertIn("LOW", cm.exception.message)
        self.assertIn("LO", cm.exception.message)

    def test_enum_dut_members_and_explicit_values(self):
        tb = bench()
        tb.set(LIM={"lo": 0, "hi": 1})
        tb.run(1)
        # Manual := 5 in the DUT, so the FB's assignment must store 5.
        self.assertEqual(tb.get("MODE"), 5)
        # ...and the rendered state shows the name, not the ordinal.
        self.assertEqual(tb.state()["MODE"], "MANUAL")

    def test_enum_member_can_be_set_by_name(self):
        tb = bench()
        tb.set(LIM={"lo": 0, "hi": 1})
        # Fault follows Manual := 5, so it is 6, not an index into the list.
        tb.set(MODE="E_Mode#Fault")
        self.assertEqual(tb.get("MODE"), 6)
        tb.set(MODE="Idle")
        self.assertEqual(tb.get("MODE"), 0)

    def test_unknown_enum_member_lists_the_valid_ones(self):
        tb = bench()
        with self.assertRaises(IecNameError) as cm:
            tb.set(MODE="Nonexistent")
        self.assertIn("IDLE", cm.exception.message)

    def test_array_of_a_struct_dut(self):
        tb = bench()
        tb.set(LIM={"lo": 2, "hi": 7})
        tb.run(1)
        # axes[2] was assigned a whole struct, which is a value copy.
        self.assertEqual(tb.get("AXES"), tb.get("AXES"))
        state = tb.state()["AXES"]
        self.assertEqual(len(state), 3)
        self.assertEqual(state[1]["LIM"], {"LO": 2, "HI": 7})
        # Element 1 was never touched, so it must be all defaults.
        self.assertEqual(state[0]["LIM"], {"LO": 0, "HI": 0})

    def test_whole_dut_given_as_one_dict(self):
        # A whole DUT arrives as one dict, with the enum member spelled out two
        # levels down.  Element 1 of the array is the one the FB never assigns,
        # so what we set is still there after the scan.
        tb = bench()
        tb.set("AXES[1]", {"pos": 1.5, "mode": "E_Mode#Manual", "lim": {"lo": 3, "hi": 9}})
        tb.run(1)
        self.assertEqual(tb.get("AXES[1].POS"), 1.5)
        self.assertEqual(tb.get("AXES[1].LIM.LO"), 3)
        # get() reports the ordinal, state() the member name -- see
        # test_enum_dut_members_and_explicit_values for the convention.
        self.assertEqual(tb.get("AXES[1].MODE"), 5)
        self.assertEqual(tb.state()["AXES"][0]["MODE"], "MANUAL")

    def test_nested_dut_dict_via_an_input(self):
        # Same thing through a VAR_INPUT of the block under test.
        tb = bench()
        tb.set(LIM={"lo": 2, "hi": 6})
        tb.run(1)
        self.assertEqual(tb.get("AXIS.LIM.LO"), 2)
        self.assertEqual(tb.get("AXIS.LIM.HI"), 6)

    def test_time_string_inside_a_dut_member(self):
        tb = bench()
        tb.set(CMD=True, PT="T#30ms")
        tb.run(1)
        self.assertEqual(tb.get("T.PT"), 30_000_000)

    def test_indexed_path_reads_an_array_of_duts(self):
        tb = bench()
        tb.set(LIM={"lo": 4, "hi": 8})
        tb.run(1)
        # axes[2] holds a copy of axis, so Pos is Hi - Lo and Lim is Lim.
        self.assertEqual(tb.get("AXES[2].POS"), 4.0)
        self.assertEqual(tb.get("AXES[2].LIM.LO"), 4)
        self.assertEqual(tb.state()["AXES"][1]["MODE"], "MANUAL")

    def test_indexed_path_writes_into_an_array_of_duts(self):
        tb = bench()
        tb.set("AXES[3].LIM.HI", 77)
        tb.run(1)
        self.assertEqual(tb.get("AXES[3].LIM.HI"), 77)
        # The FB only ever touches axes[2]; element 3 must be left alone.
        self.assertEqual(tb.get("AXES[3].LIM.LO"), 0)

    def test_failed_expectation_names_the_enum_member(self):
        tb = bench()
        tb.set(LIM={"lo": 0, "hi": 1})
        tb.run(1)
        tb.expect(AXISPOS=0.0)
        failure = tb.failures[0]
        # "expected 0.0, got 1.0" -- and the enum case must read as a name.
        self.assertEqual(failure.describe().count("FAIL"), 1)
        tb.expect(MODE="AUTO")
        self.assertIn("got MANUAL", tb.failures[-1].describe())

    def test_struct_argument_to_a_user_function(self):
        tb = bench()
        tb.set(LIM={"lo": 0, "hi": 100})
        tb.run(1)
        self.assertEqual(tb.get("WRAPPED"), 100)

    def test_nested_dut_referenced_before_declaration(self):
        # T_Axis uses T_Limits, and the files are supplied in an order where a
        # naive single pass would fail.
        model = load_sources({
            "a_uses.st": "FUNCTION_BLOCK FB VAR p : T_Inner; END_VAR END_FUNCTION_BLOCK",
            "b_types.st": """
                TYPE T_Outer : STRUCT x : INT; END_STRUCT; END_TYPE
                TYPE T_Inner : STRUCT o : T_Outer; n : INT; END_STRUCT; END_TYPE
            """,
        })
        inner = model.type_of("T_INNER")
        self.assertIn("O", inner.fields)
        self.assertIn("X", model.type_of("T_OUTER").fields)

    def test_recursive_struct_is_rejected(self):
        with self.assertRaises(IecTypeError) as cm:
            load_sources({"t": "TYPE R : STRUCT r : R; END_STRUCT; END_TYPE"})
        self.assertIn("could not be resolved", cm.exception.message)
        self.assertIn("R", cm.exception.message)

    def test_unknown_dut_is_reported(self):
        with self.assertRaises(IecTypeError) as cm:
            load_sources({"t": "FUNCTION_BLOCK FB VAR x : NoSuchDut; END_VAR END_FUNCTION_BLOCK"})
        self.assertIn("unknown type", cm.exception.message)


class TestLibraryOverridesStdlib(unittest.TestCase):
    def test_library_fb_shadows_the_builtin(self):
        tb = bench()
        # The name TON must resolve to the library's FUNCTION_BLOCK, not to
        # the interpreter's native implementation.
        self.assertEqual(tb.model.type_of("TON").impl.kind, "FUNCTION_BLOCK")
        # There is no built-in _TIMING member, so seeing it proves the library
        # body actually ran.
        tb.set(CMD=True, PT="10ms", LIM={"lo": 0, "hi": 1})
        tb.step()
        tb.step()
        self.assertTrue(tb.get("T._TIMING"))

    def test_library_ton_timing_matches_the_builtin_behaviour(self):
        tb = bench()
        tb.set(CMD=True, PT="100ms", LIM={"lo": 0, "hi": 1})
        # The library TON starts its clock on the scan where IN goes TRUE and
        # reports ET = 0 there, exactly like the built-in.
        tb.step()
        self.assertEqual(tb.get("ELAPSED"), 0)
        tb.run(10)
        self.assertEqual(tb.get("ELAPSED"), 100_000_000)
        self.assertTrue(tb.get("DONE"))

    def test_library_ton_does_not_fire_early(self):
        tb = bench()
        tb.set(CMD=True, PT="500ms", LIM={"lo": 0, "hi": 1})
        tb.run(10)
        self.assertFalse(tb.get("DONE"))
        self.assertEqual(tb.get("ELAPSED"), 90_000_000)

    def test_library_ton_resets(self):
        tb = bench()
        tb.set(CMD=True, PT="30ms", LIM={"lo": 0, "hi": 1})
        tb.run(5)
        self.assertTrue(tb.get("DONE"))
        tb.set(CMD=False)
        tb.step()
        self.assertFalse(tb.get("DONE"))
        self.assertEqual(tb.get("ELAPSED"), 0)


class TestClockFunctions(unittest.TestCase):
    def test_time_reads_the_virtual_clock(self):
        model = load_sources({"t": """
            FUNCTION_BLOCK FB
            VAR_OUTPUT a : TIME; b : TIME; END_VAR
                a := TIME();
                b := TIME() - T#0s;
            END_FUNCTION_BLOCK
        """})
        rt = Runtime(model, "FB", cycle_time="25ms")
        rt.step()
        self.assertEqual(rt.get("A"), 0)
        rt.step()
        rt.step()
        self.assertEqual(rt.get("A"), 50_000_000)

    def test_time_with_an_argument_still_converts(self):
        rt = Runtime(load_sources({"t": """
            FUNCTION_BLOCK FB
            VAR_OUTPUT r : TIME; END_VAR
                r := TIME(250);
            END_FUNCTION_BLOCK
        """}), "FB")
        rt.step()
        self.assertEqual(rt.get("R"), 250)

    def test_tod_reads_the_clock(self):
        rt = Runtime(load_sources({"t": """
            FUNCTION_BLOCK FB
            VAR_OUTPUT r : TOD; END_VAR
                r := TOD();
            END_FUNCTION_BLOCK
        """}), "FB", cycle_time="1s")
        rt.run(3)
        self.assertEqual(rt.get("R"), 2_000_000_000)


class TestCheckAcrossFiles(unittest.TestCase):
    def test_check_reports_pous_from_every_file(self):
        from iecst.core.session import check

        problems = check({"duts.st": DUTS, "utils.st": UTILS, "main.st": MAIN})
        self.assertEqual(problems, [])


if __name__ == "__main__":
    unittest.main()
