import unittest

from microtuss.core import ast_nodes as A
from microtuss.core.errors import IecSyntaxError
from microtuss.core.parser import parse_text


def parse(src):
    return parse_text(src, "<test>")


def first_pou(src):
    return parse("FUNCTION_BLOCK FB " + src + " END_FUNCTION_BLOCK").pous[0]


class TestDeclarations(unittest.TestCase):
    def test_var_sections(self):
        fb = first_pou("""
            VAR_INPUT a : INT; END_VAR
            VAR_OUTPUT b : BOOL; END_VAR
            VAR_IN_OUT c : REAL; END_VAR
            VAR d : INT; e, f : BOOL; END_VAR
        """)
        sections = {v.section for v in fb.vars}
        self.assertEqual(sections, {"input", "output", "inout", "local"})
        names = [n for v in fb.vars for n in v.names]
        self.assertEqual(names, ["A", "B", "C", "D", "E", "F"])

    def test_initialisers(self):
        fb = first_pou("VAR x : INT := 5; t : TIME := T#1s; END_VAR")
        self.assertEqual(fb.vars[0].init.value, 5)
        self.assertEqual(fb.vars[1].init.value, 1_000_000_000)

    def test_array_with_negative_bound(self):
        fb = first_pou("VAR a : ARRAY[-5..5] OF INT; END_VAR")
        spec = fb.vars[0].type
        self.assertEqual(spec.name, "ARRAY")
        # -5 is a unary minus applied to a literal, not a literal itself.
        self.assertIsInstance(spec.ranges[0][0], A.Unary)
        self.assertEqual(spec.ranges[0][0].operand.value, 5)

    def test_multidimensional_array(self):
        fb = first_pou("VAR a : ARRAY[1..2, 1..3] OF REAL; END_VAR")
        self.assertEqual(len(fb.vars[0].type.ranges), 2)

    def test_struct_inside_var(self):
        fb = first_pou("VAR p : STRUCT x : INT; y : INT; END_STRUCT; END_VAR")
        self.assertIsNotNone(fb.vars[0].type.fields)
        self.assertEqual(len(fb.vars[0].type.fields), 2)

    def test_pointer_and_reference(self):
        fb = first_pou("VAR p : POINTER TO INT; r : REFERENCE TO INT; END_VAR")
        self.assertTrue(fb.vars[0].type.ptr)
        self.assertTrue(fb.vars[1].type.ref)

    def test_string_length(self):
        fb = first_pou("VAR s : STRING[20]; END_VAR")
        self.assertEqual(fb.vars[0].type.length, 20)

    def test_at_address_is_recorded(self):
        fb = first_pou("VAR x AT %IX0.0 : BOOL; END_VAR")
        self.assertEqual(fb.vars[0].at, "%IX0.0")

    def test_constant_flag(self):
        fb = first_pou("VAR CONSTANT k : INT := 1; END_VAR")
        self.assertTrue(fb.vars[0].constant)


class TestTypesSection(unittest.TestCase):
    def test_enumeration_with_explicit_values(self):
        unit = parse("TYPE Color : (Red, Green := 10, Blue); END_TYPE")
        enum = unit.types[0]
        self.assertIsInstance(enum, A.EnumDef)
        self.assertEqual(enum.members[1].name, "GREEN")
        self.assertEqual(enum.members[1].value.value, 10)

    def test_enum_with_base_type(self):
        unit = parse("TYPE E : DINT (A, B); END_TYPE")
        self.assertEqual(unit.types[0].base.name, "DINT")

    def test_struct_type(self):
        unit = parse("TYPE P : STRUCT x : REAL; y : REAL; END_STRUCT; END_TYPE")
        self.assertIsInstance(unit.types[0], A.StructDef)

    def test_alias(self):
        unit = parse("TYPE MyInt : INT; END_TYPE")
        self.assertIsInstance(unit.types[0], A.TypeAlias)


class TestStatements(unittest.TestCase):
    def test_if_elsif_else(self):
        fb = first_pou("IF a THEN b := 1; ELSIF c THEN b := 2; ELSE b := 3; END_IF;")
        stmt = fb.body[0]
        self.assertEqual(len(stmt.branches), 2)
        self.assertEqual(len(stmt.orelse), 1)

    def test_case_with_ranges_and_multiple_labels(self):
        fb = first_pou("""
            CASE x OF
                1: y := 1;
                2, 3: y := 2;
                10..20: y := 3;
            ELSE
                y := 0;
            END_CASE;
        """)
        case = fb.body[0]
        self.assertEqual(len(case.arms), 3)
        self.assertEqual(len(case.arms[1].labels), 2)
        self.assertIsNotNone(case.arms[2].labels[0].hi)
        self.assertEqual(len(case.orelse), 1)

    def test_case_arm_bodies_do_not_swallow_next_label(self):
        fb = first_pou("CASE x OF 1: y := 1; 2: y := 2; END_CASE;")
        case = fb.body[0]
        self.assertEqual(len(case.arms), 2)
        self.assertEqual(len(case.arms[0].body), 1)
        self.assertEqual(len(case.arms[1].body), 1)

    def test_empty_case_arm_falls_through(self):
        fb = first_pou("CASE x OF 1, 2: ; 3: y := 1; END_CASE;")
        case = fb.body[0]
        self.assertEqual(len(case.arms), 2)
        self.assertEqual(case.arms[0].body, [])

    def test_case_arm_starting_with_an_fb_call(self):
        # A call is shaped like a label up to the '(', so the speculative
        # label parser must not swallow it.
        fb = first_pou("CASE x OF 1: ton1(IN := TRUE, PT := T#1s); 2: y := 1; END_CASE;")
        case = fb.body[0]
        self.assertEqual(len(case.arms), 2)
        self.assertIsInstance(case.arms[0].body[0], A.CallStatement)
        self.assertEqual(len(case.arms[1].body), 1)

    def test_case_arm_starting_with_an_indexed_assignment(self):
        # 'x[1]' also looks like a label until ':=' vs ':' disambiguates.
        fb = first_pou("CASE n OF 1: arr[1] := 5; 2: arr[2] := 6; END_CASE;")
        case = fb.body[0]
        self.assertEqual(len(case.arms), 2)
        self.assertIsInstance(case.arms[0].body[0], A.Assignment)

    def test_for_variants(self):
        fb = first_pou("FOR i := 1 TO 10 BY 2 DO END_FOR; FOR j := 10 TO 1 DO END_FOR;")
        self.assertEqual(fb.body[0].step.value, 2)
        self.assertIsNone(fb.body[1].step)

    def test_while_and_repeat(self):
        fb = first_pou("WHILE a DO a := a - 1; END_WHILE; REPEAT a := a + 1; UNTIL a > 5 END_REPEAT;")
        self.assertIsInstance(fb.body[0], A.While)
        self.assertIsInstance(fb.body[1], A.Repeat)

    def test_control_keywords(self):
        fb = first_pou("FOR i := 1 TO 2 DO EXIT; CONTINUE; RETURN; END_FOR;")
        body = fb.body[0].body
        self.assertIsInstance(body[0], A.Exit)
        self.assertIsInstance(body[1], A.Continue)
        self.assertIsInstance(body[2], A.Return)

    def test_stray_semicolons_are_dropped(self):
        fb = first_pou(";; a := 1;;")
        self.assertEqual([type(s).__name__ for s in fb.body], ["Assignment"])


class TestExpressions(unittest.TestCase):
    def value(self, expr):
        return first_pou(f"y := {expr};").body[0].value

    def test_precedence_additive_over_multiplicative(self):
        node = self.value("1 + 2 * 3")
        self.assertEqual(node.op, "+")
        self.assertEqual(node.right.op, "*")

    def test_power_is_right_associative(self):
        node = self.value("2 ** 3 ** 2")
        self.assertEqual(node.op, "**")
        self.assertEqual(node.right.op, "**")

    def test_power_binds_tighter_than_unary_minus(self):
        # IEC 61131-3 reads -a**2 as -(a**2), not as (-a)**2.
        node = self.value("-a ** 2")
        self.assertIsInstance(node, A.Unary)
        self.assertEqual(node.op, "-")
        self.assertEqual(node.operand.op, "**")

    def test_comparison_is_looser_than_arithmetic(self):
        node = self.value("a + b > c")
        self.assertEqual(node.op, ">")
        self.assertEqual(node.left.op, "+")

    def test_not_binds_tighter_than_and(self):
        node = self.value("NOT a AND b")
        self.assertEqual(node.op, "AND")
        self.assertIsInstance(node.left, A.Unary)

    def test_and_binds_tighter_than_or(self):
        node = self.value("a OR b AND c")
        self.assertEqual(node.op, "OR")
        self.assertEqual(node.right.op, "AND")

    def test_parentheses_override(self):
        node = self.value("(1 + 2) * 3")
        self.assertEqual(node.op, "*")

    def test_field_and_index_chains(self):
        node = self.value("a.b[1].c")
        self.assertIsInstance(node, A.FieldAccess)
        self.assertIsInstance(node.base, A.IndexAccess)

    def test_dereference(self):
        node = self.value("p^ + 1")
        self.assertIsInstance(node.left, A.Deref)

    def test_array_literal(self):
        node = self.value("[1, 2, 3]")
        self.assertIsInstance(node, A.ArrayLiteral)
        self.assertEqual(len(node.items), 3)

    def test_named_arguments(self):
        node = self.value("f(a := 1, b := 2)")
        self.assertEqual([a.name for a in node.args], ["A", "B"])

    def test_output_binding(self):
        node = self.value("f(Q => done)")
        arg = node.args[0]
        self.assertTrue(arg.is_output)
        self.assertEqual(arg.name, "Q")
        self.assertEqual(arg.value.name, "DONE")

    def test_enum_value_expression(self):
        node = self.value("Color#Red")
        self.assertIsInstance(node, A.EnumValue)
        self.assertEqual((node.type_name, node.member), ("COLOR", "RED"))

    def test_std_operators(self):
        for expr, op in [("a <> b", "<>"), ("a <= b", "<="), ("a MOD b", "MOD"),
                         ("a XOR b", "XOR"), ("a >= b", ">=")]:
            self.assertEqual(self.value(expr).op, op, expr)

    def test_conversion_call(self):
        node = self.value("INT(x)")
        self.assertIsInstance(node, A.Call)
        self.assertEqual(node.callee.name, "INT")


class TestErrors(unittest.TestCase):
    def test_missing_end_if_reports_position(self):
        with self.assertRaises(IecSyntaxError) as cm:
            parse("FUNCTION_BLOCK FB IF a THEN b := 1; END_FUNCTION_BLOCK")
        self.assertIn("END_IF", cm.exception.message)
        self.assertNotIn("expected an expression", cm.exception.message)

    def test_statement_that_is_not_an_assignment(self):
        with self.assertRaises(IecSyntaxError):
            first_pou("a + b;")

    def test_unsupported_oo_extension_is_explicit(self):
        with self.assertRaises(IecSyntaxError) as cm:
            parse("METHOD M : INT END_METHOD")
        self.assertIn("not supported", cm.exception.message)

    def test_error_carries_line_number(self):
        with self.assertRaises(IecSyntaxError) as cm:
            parse("FUNCTION_BLOCK FB\nVAR x : INT; END_VAR\n  x := ;\nEND_FUNCTION_BLOCK")
        self.assertEqual(cm.exception.line, 3)


if __name__ == "__main__":
    unittest.main()
