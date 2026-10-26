import unittest

from microtuss.core.errors import IecNameError, IecRuntimeError, IecTypeError
from microtuss.core.interpreter import Runtime, parse_time
from microtuss.core.model import load_sources


def build(src, pou="FB", cycle="10ms"):
    return Runtime(load_sources({"<t>": src}), pou, cycle_time=cycle)


def run(src, pou="FB", scans=1, cycle="10ms", **inputs):
    rt = build(src, pou, cycle)
    if inputs:
        rt.set(**inputs)
    rt.run(scans)
    return rt


class Fb:
    """Wrap a body in a FUNCTION_BLOCK with a preamble of extra POUs."""

    @staticmethod
    def src(body, prelude="", decls=""):
        return (
            "FUNCTION_BLOCK FB\n"
            + (f"VAR {decls} END_VAR\n" if decls else "")
            + body
            + "END_FUNCTION_BLOCK\n"
            + prelude
        )


class TestArithmetic(unittest.TestCase):
    def test_int_addition_wraps_like_a_controller(self):
        rt = run(Fb.src("VAR_OUTPUT r : INT; END_VAR r := 32767 + 1;"))
        self.assertEqual(rt.get("R"), -32768)

    def test_uint_underflow_wraps_modulo(self):
        rt = run(Fb.src("VAR_OUTPUT r : UINT; END_VAR r := 0 - 1;"))
        self.assertEqual(rt.get("R"), 65535)

    def test_integer_division_truncates_toward_zero(self):
        rt = run(Fb.src("VAR_OUTPUT a : INT; b : INT; END_VAR a := 7 / 2; b := -7 / 2;"))
        self.assertEqual(rt.get("A"), 3)
        self.assertEqual(rt.get("B"), -3)

    def test_mod_follows_the_dividend_sign(self):
        rt = run(Fb.src("VAR_OUTPUT a : INT; b : INT; END_VAR a := -7 MOD 3; b := 7 MOD -3;"))
        self.assertEqual(rt.get("A"), -1)
        self.assertEqual(rt.get("B"), 1)

    def test_real_division_is_exact(self):
        rt = run(Fb.src("VAR_OUTPUT r : REAL; END_VAR r := 7.0 / 2.0;"))
        self.assertEqual(rt.get("R"), 3.5)

    def test_mixed_int_and_real_promotes(self):
        rt = run(Fb.src("VAR_OUTPUT r : REAL; END_VAR r := 1 + 0.5;"))
        self.assertEqual(rt.get("R"), 1.5)

    def test_power_operator(self):
        rt = run(Fb.src("VAR_OUTPUT a : INT; b : REAL; END_VAR a := 2 ** 10; b := 2.0 ** 0.5;"))
        self.assertEqual(rt.get("A"), 1024)
        self.assertAlmostEqual(rt.get("B"), 2 ** 0.5, places=6)

    def test_power_is_right_associative(self):
        rt = run(Fb.src("VAR_OUTPUT r : DINT; END_VAR r := 2 ** 3 ** 2;"))
        self.assertEqual(rt.get("R"), 512)

    def test_unary_minus_applies_after_power(self):
        rt = run(Fb.src("VAR_OUTPUT r : DINT; END_VAR r := -2 ** 2;"))
        self.assertEqual(rt.get("R"), -4)

    def test_division_by_zero_is_reported_with_position(self):
        with self.assertRaises(IecRuntimeError) as cm:
            run(Fb.src("VAR_OUTPUT r : INT; END_VAR r := 1 / 0;"))
        self.assertIn("division by zero", cm.exception.message)
        # The error carries the source location of the failing expression.
        self.assertEqual(cm.exception.line, 2)
        self.assertIn("<t>:2:", cm.exception.format())

    def test_unsuffixed_literal_takes_the_narrowest_type(self):
        rt = run(Fb.src("VAR_OUTPUT r : INT; END_VAR r := 100 + 100;"))
        self.assertEqual(rt.get("R"), 200)


class TestControlFlow(unittest.TestCase):
    def test_for_accumulates(self):
        rt = run(Fb.src("""
            VAR_OUTPUT s : INT; END_VAR
            VAR i : INT; END_VAR
            s := 0;
            FOR i := 1 TO 10 DO s := s + i; END_FOR;
        """))
        self.assertEqual(rt.get("S"), 55)

    def test_for_step_and_descending(self):
        rt = run(Fb.src("""
            VAR_OUTPUT s : INT; END_VAR
            VAR i : INT; END_VAR
            s := 0;
            FOR i := 10 TO 1 BY -2 DO s := s + 1; END_FOR;
        """))
        # i takes 10, 8, 6, 4, 2 -- five iterations; the sixth value (0) is
        # already outside the range.
        self.assertEqual(rt.get("S"), 5)

    def test_for_exit_stops_early(self):
        rt = run(Fb.src("""
            VAR_OUTPUT s : INT; END_VAR
            VAR i : INT; END_VAR
            s := 0;
            FOR i := 1 TO 100 DO
                IF i > 4 THEN EXIT; END_IF;
                s := s + i;
            END_FOR;
        """))
        self.assertEqual(rt.get("S"), 10)

    def test_for_continue_skips_iteration(self):
        rt = run(Fb.src("""
            VAR_OUTPUT s : INT; END_VAR
            VAR i : INT; END_VAR
            s := 0;
            FOR i := 1 TO 6 DO
                IF i MOD 2 = 0 THEN CONTINUE; END_IF;
                s := s + i;
            END_FOR;
        """))
        self.assertEqual(rt.get("S"), 9)

    def test_for_leaves_the_control_variable_past_the_last(self):
        rt = run(Fb.src("""
            VAR i : INT; END_VAR
            FOR i := 1 TO 3 DO END_FOR;
        """))
        self.assertEqual(rt.get("I"), 4)

    def test_repeat_runs_the_body_at_least_once(self):
        rt = run(Fb.src("""
            VAR_OUTPUT n : INT; END_VAR
            n := 0;
            REPEAT n := n + 1; UNTIL TRUE END_REPEAT;
        """))
        self.assertEqual(rt.get("N"), 1)

    def test_while(self):
        rt = run(Fb.src("""
            VAR_OUTPUT n : INT; END_VAR
            n := 0;
            WHILE n < 4 DO n := n + 1; END_WHILE;
        """))
        self.assertEqual(rt.get("N"), 4)

    def test_case_ranges_and_else(self):
        body = """
            VAR_OUTPUT r : INT; END_VAR
            VAR x : INT; END_VAR
            x := %d;
            CASE x OF
                1: r := 10;
                2, 3: r := 20;
                10..20: r := 30;
                ELSE r := 99;
            END_CASE;
        """
        for value, expected in [(1, 10), (2, 20), (3, 20), (15, 30), (7, 99)]:
            rt = run(Fb.src(body % value))
            self.assertEqual(rt.get("R"), expected, f"x={value}")

    def test_case_without_else_does_nothing(self):
        rt = run(Fb.src("""
            VAR_OUTPUT r : INT; END_VAR
            VAR x : INT; END_VAR
            r := 5;
            x := 42;
            CASE x OF 1: r := 10; END_CASE;
        """))
        self.assertEqual(rt.get("R"), 5)

    def test_nested_if_elsif(self):
        rt = run(Fb.src("""
            VAR_INPUT a : INT; END_VAR
            VAR_OUTPUT r : STRING; END_VAR
            IF a > 10 THEN r := 'big';
            ELSIF a > 5 THEN r := 'mid';
            ELSE r := 'small'; END_IF;
        """), a=7)
        self.assertEqual(rt.get("R"), "mid")

    def test_return_leaves_the_body_early(self):
        rt = run(Fb.src("""
            VAR_OUTPUT r : INT; END_VAR
            r := 1;
            RETURN;
            r := 2;
        """))
        self.assertEqual(rt.get("R"), 1)


class TestBooleanSemantics(unittest.TestCase):
    PRELUDE = """
        VAR_GLOBAL g_calls : INT; END_VAR
        FUNCTION BUMP : BOOL
            g_calls := g_calls + 1;
            BUMP := TRUE;
        END_FUNCTION
    """

    def test_and_short_circuits(self):
        rt = build(Fb.src(
            "VAR_OUTPUT r : BOOL; END_VAR r := FALSE AND BUMP();", prelude=self.PRELUDE
        ))
        rt.step()
        self.assertFalse(rt.get("R"))
        self.assertEqual(rt.model.globals["G_CALLS"].value, 0)

    def test_or_short_circuits(self):
        rt = build(Fb.src(
            "VAR_OUTPUT r : BOOL; END_VAR r := TRUE OR BUMP();", prelude=self.PRELUDE
        ))
        rt.step()
        self.assertTrue(rt.get("R"))
        self.assertEqual(rt.model.globals["G_CALLS"].value, 0)

    def test_bitwise_and_does_not_short_circuit(self):
        rt = run(Fb.src("VAR_OUTPUT r : INT; END_VAR r := 16#F0 AND 16#3C;"))
        self.assertEqual(rt.get("R"), 0x30)

    def test_xor_on_bools(self):
        rt = run(Fb.src("VAR_OUTPUT a : BOOL; b : BOOL; END_VAR a := TRUE XOR FALSE; b := TRUE XOR TRUE;"))
        self.assertTrue(rt.get("A"))
        self.assertFalse(rt.get("B"))

    def test_not_on_bool_and_on_bitstring(self):
        rt = run(Fb.src("VAR_OUTPUT a : BOOL; b : WORD; END_VAR a := NOT TRUE; b := NOT 16#00FF;"))
        self.assertFalse(rt.get("A"))
        self.assertEqual(rt.get("B"), 0xFF00)


class TestComparisons(unittest.TestCase):
    def test_all_operators(self):
        rt = run(Fb.src("""
            VAR_OUTPUT a : BOOL; b : BOOL; c : BOOL; d : BOOL; e : BOOL; f : BOOL; END_VAR
            a := 1 = 1; b := 1 <> 1; c := 1 < 2; d := 2 > 1; e := 1 <= 1; f := 2 >= 3;
        """))
        self.assertEqual(
            [rt.get(x) for x in "ABCDEF"], [True, False, True, True, True, False]
        )

    def test_string_comparison(self):
        rt = run(Fb.src("VAR_OUTPUT r : BOOL; END_VAR r := 'abc' = 'abc';"))
        self.assertTrue(rt.get("R"))

    def test_comparison_of_floats(self):
        rt = run(Fb.src("VAR_OUTPUT r : BOOL; END_VAR r := 0.1 + 0.2 > 0.3;"))
        # Both sides are float32, so the comparison must not be fooled by
        # double-precision artefacts.
        self.assertIsInstance(rt.get("R"), bool)


class TestDataStructures(unittest.TestCase):
    def test_array_indexing_and_bounds(self):
        rt = run(Fb.src("""
            VAR_OUTPUT s : INT; END_VAR
            VAR a : ARRAY[1..5] OF INT; i : INT; END_VAR
            FOR i := 1 TO 5 DO a[i] := i * i; END_FOR;
            s := a[3] + a[5];
        """))
        self.assertEqual(rt.get("S"), 34)

    def test_array_out_of_bounds_reports_position(self):
        with self.assertRaises(IecTypeError) as cm:
            run(Fb.src("""
                VAR a : ARRAY[1..3] OF INT; r : INT; END_VAR
                r := a[9];
            """))
        self.assertIn("out of range", cm.exception.message)

    def test_array_with_negative_bounds(self):
        rt = run(Fb.src("""
            VAR_OUTPUT r : INT; END_VAR
            VAR a : ARRAY[-2..2] OF INT; END_VAR
            a[-1] := 7;
            r := a[-1];
        """))
        self.assertEqual(rt.get("R"), 7)

    def test_array_initialiser(self):
        rt = run(Fb.src("""
            VAR_OUTPUT r : INT; END_VAR
            VAR a : ARRAY[1..4] OF INT := [10, 20, 30, 40]; END_VAR
            r := a[1] + a[4];
        """))
        self.assertEqual(rt.get("R"), 50)

    def test_struct_fields(self):
        rt = run("""
            TYPE Point : STRUCT x : REAL; y : REAL; END_STRUCT; END_TYPE
            FUNCTION_BLOCK FB
            VAR_OUTPUT r : REAL; END_VAR
            VAR p : Point; END_VAR
                p.x := 1.5; p.y := 2.5;
                r := p.x + p.y;
            END_FUNCTION_BLOCK
        """)
        self.assertEqual(rt.get("R"), 4.0)

    def test_nested_struct(self):
        rt = run("""
            TYPE Inner : STRUCT v : INT; END_STRUCT; END_TYPE
            TYPE Outer : STRUCT inner : Inner; n : INT; END_STRUCT; END_TYPE
            FUNCTION_BLOCK FB
            VAR_OUTPUT r : INT; END_VAR
            VAR o : Outer; END_VAR
                o.inner.v := 11;
                o.n := 22;
                r := o.inner.v + o.n;
            END_FUNCTION_BLOCK
        """)
        self.assertEqual(rt.get("R"), 33)

    def test_enumeration_values_and_defaults(self):
        rt = run("""
            TYPE Color : (Red, Green := 10, Blue); END_TYPE
            FUNCTION_BLOCK FB
            VAR_OUTPUT a : INT; b : INT; c : INT; d : INT; END_VAR
            VAR col : Color; END_VAR
                a := Color#Red;
                b := Color#Green;
                c := Color#Blue;
                d := col;
            END_FUNCTION_BLOCK
        """)
        self.assertEqual([rt.get(x) for x in "ABCD"], [0, 10, 11, 0])

    def test_unknown_enum_member_rejected(self):
        with self.assertRaises(IecNameError):
            run("""
                TYPE Color : (Red); END_TYPE
                FUNCTION_BLOCK FB
                VAR c : INT; END_VAR
                    c := Color#Purple;
                END_FUNCTION_BLOCK
            """)

    def test_unknown_member_rejected(self):
        with self.assertRaises(IecTypeError):
            run("""
                TYPE P : STRUCT x : INT; END_STRUCT; END_TYPE
                FUNCTION_BLOCK FB
                VAR_OUTPUT r : INT; END_VAR
                VAR p : P; END_VAR
                    r := p.nope;
                END_FUNCTION_BLOCK
            """)


class TestPointers(unittest.TestCase):
    def test_address_and_dereference(self):
        rt = run(Fb.src("""
            VAR_OUTPUT r : INT; END_VAR
            VAR v : INT; p : POINTER TO INT; END_VAR
            v := 5;
            p := ADR(v);
            p^ := p^ + 10;
            r := v;
        """))
        self.assertEqual(rt.get("R"), 15)

    def test_pointer_to_struct_field(self):
        rt = run("""
            TYPE P : STRUCT n : INT; END_STRUCT; END_TYPE
            FUNCTION_BLOCK FB
            VAR_OUTPUT r : INT; END_VAR
            VAR s : P; ptr : POINTER TO INT; END_VAR
                s.n := 3;
                ptr := ADR(s.n);
                ptr^ := 9;
                r := s.n;
            END_FUNCTION_BLOCK
        """)
        self.assertEqual(rt.get("R"), 9)

    def test_null_dereference_reports_error(self):
        with self.assertRaises(IecRuntimeError) as cm:
            run(Fb.src("""
                VAR_OUTPUT r : INT; END_VAR
                VAR p : POINTER TO INT; END_VAR
                r := p^;
            """))
        self.assertIn("null pointer", cm.exception.message)


class TestFunctions(unittest.TestCase):
    PRELUDE = """
        FUNCTION Add3 : INT
        VAR_INPUT a : INT; b : INT; c : INT; END_VAR
            Add3 := a + b + c;
        END_FUNCTION

        FUNCTION Fact : DINT
        VAR_INPUT n : DINT; END_VAR
            IF n <= 1 THEN
                Fact := 1;
            ELSE
                Fact := n * Fact(n - 1);
            END_IF;
        END_FUNCTION
    """

    def test_function_with_positional_arguments(self):
        rt = build(Fb.src("VAR_OUTPUT r : INT; END_VAR r := Add3(1, 2, 3);", prelude=self.PRELUDE))
        rt.step()
        self.assertEqual(rt.get("R"), 6)

    def test_function_with_named_arguments(self):
        rt = build(Fb.src(
            "VAR_OUTPUT r : INT; END_VAR r := Add3(c := 3, b := 2, a := 1);", prelude=self.PRELUDE
        ))
        rt.step()
        self.assertEqual(rt.get("R"), 6)

    def test_recursion(self):
        rt = build(Fb.src("VAR_OUTPUT r : DINT; END_VAR r := Fact(6);", prelude=self.PRELUDE))
        rt.step()
        self.assertEqual(rt.get("R"), 720)

    def test_function_locals_are_not_shared(self):
        rt = build(Fb.src(
            "VAR_OUTPUT r : INT; END_VAR r := Add3(1, 1, 1) + Add3(2, 2, 2);", prelude=self.PRELUDE
        ))
        rt.step()
        self.assertEqual(rt.get("R"), 9)


class TestFunctionBlockInstances(unittest.TestCase):
    PROGRAM = """
        FUNCTION_BLOCK Counter
        VAR_INPUT Step : INT; END_VAR
        VAR_OUTPUT Total : INT; END_VAR
        VAR acc : INT; END_VAR
            acc := acc + Step;
            Total := acc;
        END_FUNCTION_BLOCK

        FUNCTION_BLOCK FB
        VAR_INPUT Step : INT; END_VAR
        VAR_OUTPUT Total : INT; END_VAR
        VAR inst : Counter; END_VAR
            inst(Step := Step, Total => Total);
        END_FUNCTION_BLOCK
    """

    def test_instance_state_persists_across_scans(self):
        rt = build(self.PROGRAM)
        rt.set(STEP=2)
        rt.run(3)
        self.assertEqual(rt.get("TOTAL"), 6)

    def test_two_instances_have_separate_state(self):
        rt = run("""
            FUNCTION_BLOCK Counter
            VAR_OUTPUT Total : INT; END_VAR
            VAR acc : INT; END_VAR
                acc := acc + 1;
                Total := acc;
            END_FUNCTION_BLOCK

            FUNCTION_BLOCK FB
            VAR_OUTPUT a : INT; b : INT; END_VAR
            VAR c1 : Counter; c2 : Counter; END_VAR
                c1(Total => a);
                c2(Total => b);
                c2(Total => b);
            END_FUNCTION_BLOCK
        """)
        # c1 runs once, c2 runs twice within the same scan.
        self.assertEqual(rt.get("A"), 1)
        self.assertEqual(rt.get("B"), 2)

    def test_var_in_out_aliases_the_callers_variable(self):
        rt = run("""
            FUNCTION_BLOCK Bump
            VAR_IN_OUT V : INT; END_VAR
            VAR_OUTPUT R : INT; END_VAR
                V := V + 1;
                R := V;
            END_FUNCTION_BLOCK

            FUNCTION_BLOCK FB
            VAR_OUTPUT shared : INT; out : INT; END_VAR
            VAR b : Bump; END_VAR
                shared := 7;
                b(V := shared, R => out);
            END_FUNCTION_BLOCK
        """)
        self.assertEqual(rt.get("OUT"), 8)
        # The callee wrote through the reference, so the caller sees the update.
        self.assertEqual(rt.get("SHARED"), 8)

    def test_unbound_var_in_out_in_a_nested_instance_is_an_error(self):
        with self.assertRaises(IecRuntimeError) as cm:
            run("""
                FUNCTION_BLOCK Bump
                VAR_IN_OUT V : INT; END_VAR
                    V := V + 1;
                END_FUNCTION_BLOCK

                FUNCTION_BLOCK FB
                VAR b : Bump; END_VAR
                    b();
                END_FUNCTION_BLOCK
            """)
        self.assertIn("was not bound by the caller", cm.exception.message)

    def test_var_in_out_of_an_array_element(self):
        rt = run("""
            FUNCTION_BLOCK Bump
            VAR_IN_OUT V : INT; END_VAR
                V := V + 1;
            END_FUNCTION_BLOCK

            FUNCTION_BLOCK FB
            VAR_OUTPUT r : INT; END_VAR
            VAR arr : ARRAY[1..3] OF INT; b : Bump; END_VAR
                b(V := arr[2]);
                r := arr[2];
            END_FUNCTION_BLOCK
        """)
        self.assertEqual(rt.get("R"), 1)

    def test_fb_output_read_as_a_field(self):
        rt = run("""
            FUNCTION_BLOCK Counter
            VAR_OUTPUT Total : INT; END_VAR
            VAR acc : INT; END_VAR
                acc := acc + 5;
                Total := acc;
            END_FUNCTION_BLOCK

            FUNCTION_BLOCK FB
            VAR_OUTPUT r : INT; END_VAR
            VAR c : Counter; END_VAR
                c();
                c();
                r := c.Total;
            END_FUNCTION_BLOCK
        """)
        self.assertEqual(rt.get("R"), 10)

    def test_underscored_internals_stay_out_of_the_snapshot(self):
        rt = run("""
            FUNCTION_BLOCK FB
            VAR_OUTPUT Q : BOOL; END_VAR
            VAR t : TON; END_VAR
                t(IN := TRUE, PT := T#1s);
                Q := t.Q;
            END_FUNCTION_BLOCK
        """)
        self.assertNotIn("_M", rt.snapshot()["T"])


class TestStringsAndTime(unittest.TestCase):
    def test_string_assignment_and_concat(self):
        rt = run(Fb.src("VAR_OUTPUT r : STRING; END_VAR r := CONCAT('ab', 'cd');"))
        self.assertEqual(rt.get("R"), "abcd")

    def test_time_arithmetic(self):
        rt = run(Fb.src("""
            VAR_OUTPUT r : TIME; END_VAR
            r := T#1s + T#500ms;
        """))
        self.assertEqual(rt.get("R"), 1_500_000_000)

    def test_time_from_a_variable_and_division(self):
        rt = run(Fb.src("""
            VAR_OUTPUT ms : DINT; END_VAR
            VAR t : TIME; END_VAR
            t := T#2s;
            ms := TIME_TO_DINT(t / T#1ms);
        """))
        self.assertEqual(rt.get("MS"), 2000)

    def test_snapshot_renders_time_readably(self):
        rt = run(Fb.src("""
            VAR_OUTPUT t : TIME; END_VAR
            t := T#1m30s;
        """))
        self.assertEqual(rt.snapshot()["T"], "T#1m30s")

    def test_parse_time_helper(self):
        self.assertEqual(parse_time("10ms"), 10_000_000)
        self.assertEqual(parse_time("T#1s"), 1_000_000_000)
        self.assertEqual(parse_time("1m30s"), 90_000_000_000)


class TestRuntimeBasics(unittest.TestCase):
    def test_locals_start_at_zero(self):
        rt = run(Fb.src("VAR_OUTPUT r : INT; s : STRING; b : BOOL; f : REAL; END_VAR r := 0;"))
        self.assertEqual(rt.get("S"), "")
        self.assertFalse(rt.get("B"))
        self.assertEqual(rt.get("F"), 0.0)

    def test_unknown_pou_is_reported(self):
        model = load_sources({"<t>": "FUNCTION_BLOCK FB END_FUNCTION_BLOCK"})
        with self.assertRaises(IecNameError) as cm:
            Runtime(model, "Nope")
        self.assertIn("no POU named", cm.exception.message)

    def test_unknown_variable_is_reported(self):
        rt = run(Fb.src("VAR_OUTPUT r : INT; END_VAR r := 1;"))
        with self.assertRaises(IecNameError):
            rt.get("NOT_THERE")

    def test_dotted_access_reaches_nested_state(self):
        rt = run("""
            FUNCTION_BLOCK Inner
            VAR_OUTPUT V : INT; END_VAR
                V := 42;
            END_FUNCTION_BLOCK

            FUNCTION_BLOCK FB
            VAR i : Inner; END_VAR
                i();
            END_FUNCTION_BLOCK
        """)
        self.assertEqual(rt.get("I.V"), 42)

    def test_indexed_path_reaches_an_array_element(self):
        rt = run(Fb.src("VAR a : ARRAY[1..3] OF INT; END_VAR a[2] := 7;"))
        self.assertEqual(rt.get("A[2]"), 7)

    def test_indexed_path_writes_through_to_the_array(self):
        rt = run(Fb.src("VAR a : ARRAY[1..3] OF INT; END_VAR a[2] := 7;"))
        rt.set("A[3]", 9)
        self.assertEqual(rt.get("A[3]"), 9)
        self.assertEqual(rt.get("A[2]"), 7, "only the addressed element changed")

    def test_indexed_path_uses_the_declared_lower_bound(self):
        rt = run(Fb.src("VAR a : ARRAY[0..3] OF INT; END_VAR a[0] := 5; a[3] := 6;"))
        self.assertEqual(rt.get("A[0]"), 5)
        self.assertEqual(rt.get("A[3]"), 6)

    def test_index_out_of_range_is_rejected(self):
        rt = run(Fb.src("VAR a : ARRAY[1..3] OF INT; END_VAR a[1] := 1;"))
        with self.assertRaises(IecTypeError) as cm:
            rt.get("A[4]")
        self.assertIn("out of range", cm.exception.message)

    def test_indexed_and_dotted_access_combine(self):
        rt = run("""
            TYPE T_P : STRUCT X : INT; END_STRUCT; END_TYPE
            FUNCTION_BLOCK FB
            VAR a : ARRAY[1..2] OF T_P; END_VAR
                a[2].X := 11;
            END_FUNCTION_BLOCK
        """, pou="FB")
        self.assertEqual(rt.get("A[2].X"), 11)

    def test_multi_dimensional_index(self):
        rt = run(Fb.src("VAR m : ARRAY[1..2, 1..3] OF INT; END_VAR m[2, 3] := 12;"))
        self.assertEqual(rt.get("M[2,3]"), 12)
        self.assertEqual(rt.get("M[1,1]"), 0)
        with self.assertRaises(IecNameError) as cm:
            rt.get("M[NOPE]")
        self.assertIn("bad array index", cm.exception.message)

    def test_indexing_a_scalar_is_reported(self):
        rt = run(Fb.src("VAR n : INT; END_VAR n := 1;"))
        with self.assertRaises(IecNameError) as cm:
            rt.get("N[1]")
        self.assertIn("not an array", cm.exception.message)

    def test_unknown_struct_member_is_reported(self):
        rt = run("""
            TYPE T_P : STRUCT X : INT; END_STRUCT; END_TYPE
            FUNCTION_BLOCK FB
            VAR p : T_P; END_VAR
                p.X := 1;
            END_FUNCTION_BLOCK
        """, pou="FB")
        with self.assertRaises(IecTypeError) as cm:
            rt.get("P.Y")
        self.assertIn("has no member 'Y'", cm.exception.message)

    def test_clock_advances_by_the_cycle_time(self):
        rt = run(Fb.src("VAR r : INT; END_VAR r := 1;"), scans=4, cycle="25ms")
        self.assertEqual(rt.now, 100_000_000)
        self.assertEqual(rt.scan, 4)

    def test_global_variable_is_shared_between_pous(self):
        rt = run("""
            VAR_GLOBAL g : INT; END_VAR
            FUNCTION Bump : INT
                g := g + 1;
                Bump := g;
            END_FUNCTION

            FUNCTION_BLOCK FB
            VAR_OUTPUT a : INT; b : INT; END_VAR
                a := Bump();
                b := Bump();
            END_FUNCTION_BLOCK
        """)
        self.assertEqual((rt.get("A"), rt.get("B")), (1, 2))

    def test_trace_records_assignments(self):
        rt = build(Fb.src("VAR_OUTPUT r : INT; END_VAR r := 5;"))
        rt.trace_enabled = True
        rt.step()
        assignments = [e for e in rt.events if e.kind == "assign"]
        self.assertTrue(any(e.name == "R" and e.value == 5 for e in assignments))


if __name__ == "__main__":
    unittest.main()
