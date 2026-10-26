import unittest

from iecst.errors import IecRuntimeError
from iecst.interpreter import Runtime
from iecst.model import load_sources
from iecst.stdlib import format_time, format_tod


def run(src, pou="FB", scans=1, cycle="10ms", **inputs):
    model = load_sources({"<t>": src})
    rt = Runtime(model, pou, cycle_time=cycle)
    for name, value in inputs.items():
        rt.set(name, value)
    rt.run(scans)
    return rt


class TestFormatTime(unittest.TestCase):
    def test_components(self):
        self.assertEqual(format_time(0), "T#0s")
        self.assertEqual(format_time(1_000_000_000), "T#1s")
        self.assertEqual(format_time(90_500_000_000), "T#1m30s500ms")
        self.assertEqual(format_time(86_400_000_000_000), "T#1d")
        self.assertEqual(format_time(-2_000_000_000), "-T#2s")

    def test_sub_millisecond(self):
        self.assertEqual(format_time(1_500), "T#1us500ns")

    def test_tod(self):
        self.assertEqual(format_tod(0), "TOD#00:00:00")
        self.assertEqual(format_tod(3_600_000_000_000 + 60_000_000_000), "TOD#01:01:00")


class TestConversions(unittest.TestCase):
    def test_int_conversion_wraps(self):
        rt = run("""
            FUNCTION_BLOCK FB
            VAR a : INT; b : DINT; c : REAL; d : SINT; END_VAR
                a := INT(40000);
                b := DINT(2.5);
                c := REAL(3);
                d := SINT(200);
            END_FUNCTION_BLOCK
        """)
        self.assertEqual(rt.get("A"), -25536)
        self.assertEqual(rt.get("B"), 3)
        self.assertEqual(rt.get("C"), 3.0)
        self.assertEqual(rt.get("D"), -56)

    def test_x_to_y_style_names(self):
        rt = run("""
            FUNCTION_BLOCK FB
            VAR a : INT; b : REAL; END_VAR
                a := REAL_TO_INT(2.7);
                b := INT_TO_REAL(5);
            END_FUNCTION_BLOCK
        """)
        self.assertEqual(rt.get("A"), 3)
        self.assertEqual(rt.get("B"), 5.0)

    def test_bool_conversion(self):
        rt = run("""
            FUNCTION_BLOCK FB
            VAR a : BOOL; b : BOOL; END_VAR
                a := BOOL(5);
                b := BOOL(0);
            END_FUNCTION_BLOCK
        """)
        self.assertTrue(rt.get("A"))
        self.assertFalse(rt.get("B"))


class TestMathLibrary(unittest.TestCase):
    def test_abs_min_max_limit(self):
        rt = run("""
            FUNCTION_BLOCK FB
            VAR a : INT; b : INT; c : INT; d : INT; END_VAR
                a := ABS(-7);
                b := MIN(3, 1, 2);
                c := MAX(3, 1, 2);
                d := LIMIT(0, 150, 100);
            END_FUNCTION_BLOCK
        """)
        self.assertEqual((rt.get("A"), rt.get("B"), rt.get("C"), rt.get("D")), (7, 1, 3, 100))

    def test_sqrt_and_trig(self):
        rt = run("""
            FUNCTION_BLOCK FB
            VAR a : REAL; b : REAL; END_VAR
                a := SQRT(16.0);
                b := SIN(0.0);
            END_FUNCTION_BLOCK
        """)
        self.assertEqual(rt.get("A"), 4.0)
        self.assertEqual(rt.get("B"), 0.0)

    def test_sel_and_mux(self):
        rt = run("""
            FUNCTION_BLOCK FB
            VAR a : INT; b : INT; c : INT; END_VAR
                a := SEL(TRUE, 10, 20);
                b := SEL(FALSE, 10, 20);
                c := MUX(1, 100, 200, 300);
            END_FUNCTION_BLOCK
        """)
        self.assertEqual((rt.get("A"), rt.get("B"), rt.get("C")), (20, 10, 200))

    def test_mux_selector_out_of_range(self):
        with self.assertRaises(IecRuntimeError) as cm:
            run("""
                FUNCTION_BLOCK FB
                VAR c : INT; END_VAR
                    c := MUX(9, 1, 2);
                END_FUNCTION_BLOCK
            """)
        self.assertIn("outside", cm.exception.message)


class TestBitStrings(unittest.TestCase):
    def test_shift_and_logic(self):
        rt = run("""
            FUNCTION_BLOCK FB
            VAR a : WORD; b : WORD; c : WORD; d : WORD; END_VAR
                a := SHL(16#0001, 4);
                b := SHR(16#0100, 4);
                c := AND(16#FF00, 16#0FF0);
                d := OR(16#00F0, 16#000F);
            END_FUNCTION_BLOCK
        """)
        self.assertEqual(rt.get("A"), 0x0010)
        self.assertEqual(rt.get("B"), 0x0010)
        self.assertEqual(rt.get("C"), 0x0F00)
        self.assertEqual(rt.get("D"), 0x00FF)

    def test_rotate(self):
        rt = run("""
            FUNCTION_BLOCK FB
            VAR a : WORD; b : WORD; END_VAR
                a := ROL(16#8001, 1);
                b := ROR(16#0001, 1);
            END_FUNCTION_BLOCK
        """)
        self.assertEqual(rt.get("A"), 0x0003)
        self.assertEqual(rt.get("B"), 0x8000)


class TestStringLibrary(unittest.TestCase):
    def test_basic_string_functions(self):
        rt = run("""
            FUNCTION_BLOCK FB
            VAR n : INT; a : STRING; b : STRING; c : STRING; d : STRING; f : INT; END_VAR
                n := LEN('hello');
                a := LEFT('hello', 2);
                b := RIGHT('hello', 2);
                c := MID('hello', 2, 2);
                d := CONCAT('ab', 'cd');
                f := FIND('hello world', 'world');
            END_FUNCTION_BLOCK
        """)
        self.assertEqual(rt.get("N"), 5)
        self.assertEqual(rt.get("A"), "he")
        self.assertEqual(rt.get("B"), "lo")
        self.assertEqual(rt.get("C"), "el")
        self.assertEqual(rt.get("D"), "abcd")
        self.assertEqual(rt.get("F"), 7)

    def test_replace_and_delete(self):
        rt = run("""
            FUNCTION_BLOCK FB
            VAR a : STRING; b : STRING; END_VAR
                a := REPLACE('abcdef', 'XY', 2, 3);
                b := DELETE('abcdef', 2, 2);
            END_FUNCTION_BLOCK
        """)
        self.assertEqual(rt.get("A"), "abXYef")
        self.assertEqual(rt.get("B"), "adef")


class TestTimers(unittest.TestCase):
    TON = """
        FUNCTION_BLOCK FB
        VAR IN : BOOL; PT : TIME; Q : BOOL; ET : TIME; END_VAR
            ton1(IN := IN, PT := PT, Q => Q, ET => ET);
        END_FUNCTION_BLOCK
        VAR ton1 : TON; END_VAR
    """

    def test_ton_does_not_fire_before_pt(self):
        src = """
            FUNCTION_BLOCK FB
            VAR_INPUT IN : BOOL; END_VAR
            VAR_OUTPUT Q : BOOL; ET : TIME; END_VAR
            VAR ton1 : TON; END_VAR
                ton1(IN := IN, PT := T#100ms, Q => Q, ET => ET);
            END_FUNCTION_BLOCK
        """
        rt = run(src, scans=0, cycle="10ms")
        rt.set("IN", True)

        # The scan on which IN turns TRUE establishes the start instant and
        # leaves ET at zero, so nine more scans are needed to reach 100 ms.
        rt.step()
        self.assertEqual(rt.get("ET"), 0)
        rt.run(5)
        self.assertFalse(rt.get("Q"))
        self.assertEqual(rt.get("ET"), 50_000_000)

        rt.run(5)
        self.assertTrue(rt.get("Q"), "TON must fire once ET reaches PT")
        self.assertEqual(rt.get("ET"), 100_000_000)

    def test_ton_resets_when_input_drops(self):
        src = """
            FUNCTION_BLOCK FB
            VAR_INPUT IN : BOOL; END_VAR
            VAR_OUTPUT Q : BOOL; ET : TIME; END_VAR
            VAR t : TON; END_VAR
                t(IN := IN, PT := T#50ms, Q => Q, ET => ET);
            END_FUNCTION_BLOCK
        """
        rt = run(src, scans=0, cycle="10ms")
        rt.set("IN", True)
        rt.run(7)
        self.assertTrue(rt.get("Q"))
        rt.set("IN", False)
        rt.step()
        self.assertFalse(rt.get("Q"))
        self.assertEqual(rt.get("ET"), 0)

    def test_tof_holds_output_after_input_drops(self):
        src = """
            FUNCTION_BLOCK FB
            VAR_INPUT IN : BOOL; END_VAR
            VAR_OUTPUT Q : BOOL; END_VAR
            VAR t : TOF; END_VAR
                t(IN := IN, PT := T#50ms, Q => Q);
            END_FUNCTION_BLOCK
        """
        rt = run(src, scans=1, cycle="10ms")
        rt.set("IN", True)
        rt.step()
        self.assertTrue(rt.get("Q"))
        rt.set("IN", False)
        rt.run(3)
        self.assertTrue(rt.get("Q"), "TOF must keep Q high during PT after IN falls")
        rt.run(3)
        self.assertFalse(rt.get("Q"))

    def test_tp_pulse_width_is_independent_of_input(self):
        src = """
            FUNCTION_BLOCK FB
            VAR_INPUT IN : BOOL; END_VAR
            VAR_OUTPUT Q : BOOL; END_VAR
            VAR t : TP; END_VAR
                t(IN := IN, PT := T#30ms, Q => Q);
            END_FUNCTION_BLOCK
        """
        rt = run(src, scans=1, cycle="10ms")
        rt.set("IN", True)
        rt.step()
        self.assertTrue(rt.get("Q"))
        rt.set("IN", False)
        rt.run(2)
        self.assertTrue(rt.get("Q"), "pulse must continue although IN fell")
        rt.run(2)
        self.assertFalse(rt.get("Q"))


class TestEdgeDetection(unittest.TestCase):
    def test_r_trig_fires_once_per_edge(self):
        rt = run("""
            FUNCTION_BLOCK FB
            VAR_INPUT CLK : BOOL; END_VAR
            VAR_OUTPUT Q : BOOL; END_VAR
            VAR d : R_TRIG; END_VAR
                d(CLK := CLK, Q => Q);
            END_FUNCTION_BLOCK
        """, scans=1)
        rt.set("CLK", True)
        rt.step()
        self.assertTrue(rt.get("Q"))
        rt.step()
        self.assertFalse(rt.get("Q"), "R_TRIG must fire on the edge only")
        rt.set("CLK", False)
        rt.step()
        self.assertFalse(rt.get("Q"))
        rt.set("CLK", True)
        rt.step()
        self.assertTrue(rt.get("Q"))

    def test_f_trig(self):
        rt = run("""
            FUNCTION_BLOCK FB
            VAR_INPUT CLK : BOOL; END_VAR
            VAR_OUTPUT Q : BOOL; END_VAR
            VAR d : F_TRIG; END_VAR
                d(CLK := CLK, Q => Q);
            END_FUNCTION_BLOCK
        """, scans=1)
        rt.set("CLK", True)
        rt.step()
        self.assertFalse(rt.get("Q"))
        rt.set("CLK", False)
        rt.step()
        self.assertTrue(rt.get("Q"))


class TestCounters(unittest.TestCase):
    def test_ctu_counts_edges_and_resets(self):
        rt = run("""
            FUNCTION_BLOCK FB
            VAR_INPUT CU : BOOL; R : BOOL; END_VAR
            VAR_OUTPUT CV : INT; Q : BOOL; END_VAR
            VAR c : CTU; END_VAR
                c(CU := CU, R := R, PV := 3, CV => CV, Q => Q);
            END_FUNCTION_BLOCK
        """, scans=1)

        # Holding CU high for several scans must count only one edge.
        rt.set("CU", True)
        rt.run(5)
        self.assertEqual(rt.get("CV"), 1)

        for _ in range(2):
            rt.set("CU", False)
            rt.step()
            rt.set("CU", True)
            rt.step()
        self.assertEqual(rt.get("CV"), 3)
        self.assertTrue(rt.get("Q"))

        rt.set("R", True)
        rt.step()
        self.assertEqual(rt.get("CV"), 0)
        self.assertFalse(rt.get("Q"))


class TestLatchingFbs(unittest.TestCase):
    def test_sr_is_set_dominant(self):
        rt = run("""
            FUNCTION_BLOCK FB
            VAR_INPUT S : BOOL; R : BOOL; END_VAR
            VAR_OUTPUT Q : BOOL; END_VAR
            VAR l : SR; END_VAR
                l(SET1 := S, RESET := R, Q1 => Q);
            END_FUNCTION_BLOCK
        """, scans=1)
        rt.set(S=True, R=True)
        rt.step()
        self.assertTrue(rt.get("Q"), "SR is set-dominant")
        rt.set(S=False, R=False)
        rt.step()
        self.assertTrue(rt.get("Q"), "SR keeps its state when neither input is set")

    def test_rs_is_reset_dominant(self):
        rt = run("""
            FUNCTION_BLOCK FB
            VAR_INPUT S : BOOL; R : BOOL; END_VAR
            VAR_OUTPUT Q : BOOL; END_VAR
            VAR l : RS; END_VAR
                l(SET := S, RESET1 := R, Q1 => Q);
            END_FUNCTION_BLOCK
        """, scans=1)
        rt.set(S=True, R=True)
        rt.step()
        self.assertFalse(rt.get("Q"), "RS is reset-dominant")
        rt.set(S=True, R=False)
        rt.step()
        self.assertTrue(rt.get("Q"))


if __name__ == "__main__":
    unittest.main()
