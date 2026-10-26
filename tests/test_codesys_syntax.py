"""CODESYS spellings that a verbatim export contains but the standard omits.

These all came out of a real block export: ``FUNCTION_BLOCK FINAL``, a
block-private enumeration written inline, bit access on a status word, struct
members with defaults, and a ``;`` after ``END_STRUCT`` that CODESYS does not
write.  Each is a small extension of the standard, and each changes behaviour
if it is ignored rather than implemented.
"""

import unittest

from microtuss.core.errors import IecNameError, IecTypeError
from microtuss.core.interpreter import Runtime
from microtuss.core.model import load_sources


def build(src, pou="FB", **kw):
    return Runtime(load_sources({"<t>": src}), pou, **kw)


def run(src, pou="FB", scans=1, **inputs):
    rt = build(src, pou)
    if inputs:
        rt.set(**inputs)
    rt.run(scans)
    return rt


class TestModifiers(unittest.TestCase):
    def test_final_before_the_block_name_is_accepted(self):
        rt = run("""
            FUNCTION_BLOCK FINAL FB
            VAR_OUTPUT x : INT; END_VAR
                x := 3;
            END_FUNCTION_BLOCK
        """)
        self.assertEqual(rt.get("X"), 3)

    def test_abstract_and_access_modifiers_are_accepted(self):
        rt = run("""
            ABSTRACT FUNCTION_BLOCK FB
            VAR_OUTPUT x : INT; END_VAR
                x := 4;
            END_FUNCTION_BLOCK
        """)
        self.assertEqual(rt.get("X"), 4)

    def test_modifiers_do_not_leak_into_the_next_declaration(self):
        # Two blocks in a row: the modifier run must not swallow the second.
        model = load_sources({"<t>": """
            FUNCTION_BLOCK FINAL A
            END_FUNCTION_BLOCK
            FUNCTION_BLOCK B
            END_FUNCTION_BLOCK
        """})
        self.assertEqual({"A", "B"}, set(model.pous))


class TestPragmas(unittest.TestCase):
    """``{attribute 'symbol' := 'none'}`` is metadata for the OPC layer."""

    SRC = """
        TYPE T_P : STRUCT
            {attribute 'symbol' := 'read'}
            V : REAL;
        END_STRUCT END_TYPE

        FUNCTION_BLOCK FB
        VAR_INPUT
        {attribute 'symbol' := 'none'}
            rIn : REAL;
        {attribute 'symbol' := 'readwrite'}
            p : T_P;
        END_VAR
        VAR_OUTPUT
        {attribute 'symbol' := 'read'}
            o : REAL;
        END_VAR
            o := rIn;
        END_FUNCTION_BLOCK
    """

    def test_attributes_are_ignored(self):
        rt = run(self.SRC)
        rt.set(RIN=2.5)
        rt.run(1)
        self.assertEqual(rt.get("O"), 2.5)

    def test_attributes_change_nothing(self):
        import re

        stripped = re.sub(r"\{[^}]*\}\n?", "", self.SRC)
        a = run(self.SRC)
        b = run(stripped)
        a.set(RIN=7.25)
        b.set(RIN=7.25)
        a.run(1)
        b.run(1)
        self.assertEqual(a.snapshot(), b.snapshot())


class TestInlineEnum(unittest.TestCase):
    """``Command : (NO_CMD := 0, SET_SHIFT := 1);`` -- no type name to qualify."""

    SRC = """
        FUNCTION_BLOCK FB
        VAR
            Command : (NO_CMD := 0, SET_SHIFT := 1, DROP_SHIFT := 2);
            stCmd : INT;
            shift : REAL;
            value : REAL;
        END_VAR
            value := 42.0;
            CASE stCmd OF
                SET_SHIFT:
                    shift := value;
                DROP_SHIFT:
                    shift := 0.0;
            END_CASE
            stCmd := Command;
            Command := 0;
        END_FUNCTION_BLOCK
    """

    def test_members_are_visible_unqualified_in_the_pou(self):
        rt = run(self.SRC)
        rt.set(STCMD=1)  # SET_SHIFT
        rt.run(1)
        self.assertEqual(rt.get("SHIFT"), 42.0)

    def test_drop_shift_arm(self):
        rt = run(self.SRC)
        rt.set(STCMD=2)  # DROP_SHIFT
        rt.run(1)
        self.assertEqual(rt.get("SHIFT"), 0.0)

    def test_a_pulse_on_command_is_latched_then_cleared(self):
        # The block latches Command into stCmd at the end of the scan, so the
        # command issued now is acted on by the next scan.
        rt = run(self.SRC)
        rt.set(COMMAND=1)  # SET_SHIFT, while stCmd is still NO_CMD
        rt.run(1)
        self.assertEqual(rt.get("STCMD"), 1, "the command was latched")
        self.assertEqual(rt.get("COMMAND"), 0, "and the input was cleared")
        self.assertEqual(rt.get("SHIFT"), 0.0, "but has not taken effect yet")

        rt.run(1)
        self.assertEqual(rt.get("SHIFT"), 42.0, "now the CASE acts on it")

    def test_drop_shift_command_clears_the_shift(self):
        rt = build(self.SRC)
        rt.set(STCMD=1, SHIFT=99.0)  # already shifted, CASE_SET
        rt.run(1)
        self.assertEqual(rt.get("SHIFT"), 42.0)

        rt.set(COMMAND=2)  # DROP_SHIFT arrives
        rt.run(1)
        self.assertEqual(rt.get("SHIFT"), 42.0, "still the scan that latches it")
        rt.run(1)
        self.assertEqual(rt.get("SHIFT"), 0.0)

    def test_member_names_do_not_escape_the_block(self):
        # An unrelated block must not see FB's private members.
        with self.assertRaises(IecNameError):
            run("""
                FUNCTION_BLOCK FB
                VAR c : (A := 0, B := 1); END_VAR
                    c := 0;
                END_FUNCTION_BLOCK
                FUNCTION_BLOCK Other
                VAR_OUTPUT x : INT; END_VAR
                    x := B;
                END_FUNCTION_BLOCK
            """, pou="OTHER")

    def test_a_block_named_like_a_modifier_is_a_known_limitation(self):
        # "FUNCTION_BLOCK Final" is read as a FINAL block with a missing name.
        # No CODESYS export does this, and resolving it would mean guessing
        # which of two readings was meant, so it is refused rather than
        # silently choosing one.
        from microtuss.core.errors import IecSyntaxError

        with self.assertRaises(IecSyntaxError):
            load_sources({"<t>": "FUNCTION_BLOCK Final END_FUNCTION_BLOCK"})


class TestEnumBaseType(unittest.TestCase):
    def test_enum_over_a_user_defined_base(self):
        rt = run("""
            TYPE T_Base : DINT; END_TYPE
            TYPE T_Mode : T_Base (Off := 0, On := 100); END_TYPE
            FUNCTION_BLOCK FB
            VAR m : T_Mode; END_VAR
                m := On;
            END_FUNCTION_BLOCK
        """)
        self.assertEqual(rt.get("M"), 100)

    def test_base_type_fixes_the_width_so_values_wrap(self):
        rt = run("""
            TYPE T_B : SINT; END_TYPE
            TYPE T_E : T_B (Big := 200); END_TYPE
            FUNCTION_BLOCK FB
            VAR e : T_E; END_VAR
                e := Big;
            END_FUNCTION_BLOCK
        """)
        # SINT is 8 bits, so 200 becomes -56 -- the width comes from the base.
        self.assertEqual(rt.get("E"), -56)

    def test_members_without_values_count_up_from_the_last(self):
        rt = run("""
            TYPE T_E : (Idle, Auto, Manual := 5, Fault); END_TYPE
            FUNCTION_BLOCK FB
            VAR e : T_E; END_VAR
                e := Fault;
            END_FUNCTION_BLOCK
        """)
        self.assertEqual(rt.get("E"), 6)


class TestBitAccess(unittest.TestCase):
    SRC = """
        FUNCTION_BLOCK FB
        VAR_OUTPUT w : DWORD; b : BOOL; n : INT; END_VAR
            w.0 := TRUE;
            w.3 := TRUE;
            b := w.1;
            n := w.3;
        END_FUNCTION_BLOCK
    """

    def test_bits_can_be_set_and_read(self):
        rt = run(self.SRC)
        self.assertEqual(rt.get("W"), 0b1001)
        self.assertFalse(rt.get("B"))
        self.assertTrue(rt.get("N"))

    def test_a_later_whole_word_write_replaces_the_bits(self):
        rt = run("""
            FUNCTION_BLOCK FB
            VAR_OUTPUT w : DWORD; b : BOOL; END_VAR
                w.3 := TRUE;
                w := 2#1010;
                b := w.3;
            END_FUNCTION_BLOCK
        """)
        self.assertEqual(rt.get("W"), 0b1010)
        self.assertTrue(rt.get("B"))

    def test_clearing_a_bit(self):
        rt = run("""
            FUNCTION_BLOCK FB
            VAR_OUTPUT w : WORD; END_VAR
                w := 16#FFFF;
                w.0 := FALSE;
            END_FUNCTION_BLOCK
        """)
        self.assertEqual(rt.get("W"), 0xFFFE)

    def test_bit_of_a_nested_struct_member(self):
        rt = run("""
            TYPE T_Status : STRUCT s : DWORD; END_STRUCT END_TYPE
            FUNCTION_BLOCK FB
            VAR_OUTPUT o : T_Status; END_VAR
                o.s.5 := TRUE;
            END_FUNCTION_BLOCK
        """)
        self.assertEqual(rt.get("O.S"), 32)

    def test_bit_index_out_of_range_is_reported(self):
        with self.assertRaises(IecTypeError) as cm:
            run("""
                FUNCTION_BLOCK FB
                VAR w : WORD; END_VAR
                    w.16 := TRUE;
                END_FUNCTION_BLOCK
            """)
        self.assertIn("bit 16", cm.exception.message)

    def test_bit_of_a_real_is_rejected(self):
        with self.assertRaises(IecTypeError):
            run("""
                FUNCTION_BLOCK FB
                VAR r : REAL; END_VAR
                    r.1 := TRUE;
                END_FUNCTION_BLOCK
            """)

    def test_struct_member_named_like_a_number_is_unaffected(self):
        # A struct with a numeric-looking member cannot happen (identifiers
        # cannot start with a digit), but a member called X must still work.
        rt = run("""
            TYPE T_P : STRUCT X : INT; END_STRUCT END_TYPE
            FUNCTION_BLOCK FB
            VAR_OUTPUT p : T_P; w : BYTE; END_VAR
                p.X := 9;
                w.2 := TRUE;
            END_FUNCTION_BLOCK
        """)
        self.assertEqual(rt.get("P.X"), 9)
        self.assertEqual(rt.get("W"), 4)


class TestStructMemberDefaults(unittest.TestCase):
    """A DUT's ``:= `` defaults are part of its definition, not decoration."""

    SRC = """
        TYPE T_PAR : STRUCT
            FiltrationRate : REAL := 1;
            InLow : REAL := 4;
            InHigh : REAL := 20;
            Name : STRING[8] := 'default';
        END_STRUCT END_TYPE

        FUNCTION_BLOCK FB
        VAR_INPUT PAR : T_PAR; END_VAR
        VAR_OUTPUT o : REAL; s : STRING; END_VAR
            o := PAR.InLow + PAR.InHigh;
            s := PAR.Name;
        END_FUNCTION_BLOCK
    """

    def test_defaults_are_in_place_without_any_assignment(self):
        rt = run(self.SRC)
        rt.run(1)
        self.assertEqual(rt.get("O"), 24.0)
        self.assertEqual(rt.get("S"), "default")

    def test_a_partial_dict_keeps_the_other_defaults(self):
        rt = build(self.SRC)
        rt.set(PAR={"inlow": 0.0})
        rt.run(1)
        self.assertEqual(rt.get("O"), 20.0, "InHigh kept its own default")

    def test_nested_struct_defaults(self):
        rt = run("""
            TYPE T_Inner : STRUCT Lo : REAL := 1.5; END_STRUCT END_TYPE
            TYPE T_Outer : STRUCT In : T_Inner; END_STRUCT END_TYPE
            FUNCTION_BLOCK FB
            VAR_INPUT p : T_Outer; END_VAR
            VAR_OUTPUT o : REAL; END_VAR
                o := p.In.Lo;
            END_FUNCTION_BLOCK
        """)
        rt.run(1)
        self.assertEqual(rt.get("O"), 1.5)

    def test_default_from_a_global_constant(self):
        rt = run("""
            VAR_GLOBAL CONSTANT
                C_MAX : INT := 77;
            END_VAR
            TYPE T_P : STRUCT Lim : INT := C_MAX; END_STRUCT END_TYPE
            FUNCTION_BLOCK FB
            VAR_INPUT p : T_P; END_VAR
            VAR_OUTPUT o : INT; END_VAR
                o := p.Lim;
            END_FUNCTION_BLOCK
        """)
        rt.run(1)
        self.assertEqual(rt.get("O"), 77)

    def test_default_naming_an_enum_member(self):
        rt = run("""
            TYPE T_E : (Off, On); END_TYPE
            TYPE T_P : STRUCT M : T_E := On; END_STRUCT END_TYPE
            FUNCTION_BLOCK FB
            VAR_INPUT p : T_P; END_VAR
            VAR_OUTPUT o : INT; END_VAR
                o := p.M;
            END_FUNCTION_BLOCK
        """)
        rt.run(1)
        self.assertEqual(rt.get("O"), 1)


class TestOptionalTerminators(unittest.TestCase):
    """CODESYS omits the ``;`` after END_STRUCT; it is required after an alias."""

    def test_no_semicolon_after_end_struct(self):
        rt = run("""
            TYPE T_P : STRUCT X : INT; END_STRUCT END_TYPE
            FUNCTION_BLOCK FB
            VAR_OUTPUT o : INT; p : T_P; END_VAR
                o := p.X;
            END_FUNCTION_BLOCK
        """)
        self.assertEqual(rt.get("O"), 0)

    def test_no_semicolon_after_a_bare_enum(self):
        rt = run("""
            TYPE T_E : (A, B) END_TYPE
            FUNCTION_BLOCK FB
            VAR_OUTPUT o : INT; e : T_E; END_VAR
                e := B;
                o := e;
            END_FUNCTION_BLOCK
        """)
        self.assertEqual(rt.get("O"), 1)

    def test_semicolon_after_end_struct_is_still_fine(self):
        rt = run("""
            TYPE T_P : STRUCT X : INT; END_STRUCT; END_TYPE
            FUNCTION_BLOCK FB
            VAR_OUTPUT o : INT; p : T_P; END_VAR
                o := p.X;
            END_FUNCTION_BLOCK
        """)
        self.assertEqual(rt.get("O"), 0)

    def test_an_alias_still_needs_its_semicolon(self):
        from microtuss.core.errors import IecSyntaxError

        with self.assertRaises(IecSyntaxError):
            load_sources({"<t>": "TYPE T_A : INT END_TYPE"})


class TestMissingClosingKeyword(unittest.TestCase):
    """A CODESYS object export has no closing keyword; the file just stops."""

    def test_a_block_ending_at_eof_is_accepted(self):
        model = load_sources({"<t>": "FUNCTION_BLOCK FB\nVAR_OUTPUT x : INT; END_VAR\nx := 1;"})
        self.assertIn("FB", model.pous)
        rt = Runtime(model, "FB")
        rt.run(1)
        self.assertEqual(rt.get("X"), 1)

    def test_a_function_ending_at_eof_is_accepted(self):
        # A FUNCTION has no instance to run, so it is exercised through call().
        model = load_sources({"<t>": """
            FUNCTION F : INT
            VAR_INPUT a : INT; END_VAR
                F := a * 2;
        """})
        self.assertIn("F", model.pous)
        rt = Runtime(load_sources({"<t>": """
            FUNCTION F : INT
            VAR_INPUT a : INT; END_VAR
                F := a * 2;
            END_FUNCTION
            FUNCTION_BLOCK FB
            VAR_OUTPUT o : INT; END_VAR
                o := F(a := 21);
            END_FUNCTION_BLOCK
        """}), "FB")
        rt.run(1)
        self.assertEqual(rt.get("O"), 42)

    def test_a_types_only_file_ending_at_eof_is_accepted(self):
        model = load_sources({"<t>": "TYPE T_P : STRUCT X : INT; END_STRUCT END_TYPE"})
        self.assertIsNotNone(model.type_of("T_P"))

    def test_several_blocks_each_in_their_own_file(self):
        model = load_sources({
            "a.st": "FUNCTION_BLOCK A\nVAR_OUTPUT x : INT; END_VAR\nx := 1;",
            "b.st": "FUNCTION_BLOCK B\nVAR a : A; END_VAR\na();",
        })
        self.assertEqual({"A", "B"}, set(model.pous))

    def test_a_truncated_statement_is_still_reported(self):
        # Dropping the closing keyword must not turn a real syntax error into
        # something that silently parses.
        from microtuss.core.errors import IecSyntaxError

        with self.assertRaises(IecSyntaxError):
            load_sources({"<t>": "FUNCTION_BLOCK FB\nVAR_OUTPUT x : INT; END_VAR\nx := ;"})

    def test_an_unterminated_var_section_is_still_reported(self):
        from microtuss.core.errors import IecSyntaxError

        with self.assertRaises(IecSyntaxError):
            load_sources({"<t>": "FUNCTION_BLOCK FB\nVAR_OUTPUT x : INT;\nx := 1;"})

    def test_the_closing_keyword_is_still_accepted(self):
        rt = run("""
            FUNCTION_BLOCK FB
            VAR_OUTPUT x : INT; END_VAR
                x := 2;
            END_FUNCTION_BLOCK
        """)
        self.assertEqual(rt.get("X"), 2)

    def test_code_after_the_closing_keyword_still_parses(self):
        model = load_sources({"<t>": """
            FUNCTION_BLOCK A
            END_FUNCTION_BLOCK
            FUNCTION_BLOCK B
            END_FUNCTION_BLOCK
        """})
        self.assertEqual({"A", "B"}, set(model.pous))


class TestGlobalInitialisers(unittest.TestCase):
    """``VAR_GLOBAL CONSTANT C_MAX : INT := 77`` must actually hold 77."""

    def test_a_constant_global_keeps_its_value(self):
        rt = run("""
            VAR_GLOBAL CONSTANT C_MAX : INT := 77; END_VAR
            FUNCTION_BLOCK FB
            VAR_OUTPUT o : INT; END_VAR
                o := C_MAX;
            END_FUNCTION_BLOCK
        """)
        self.assertEqual(rt.get("O"), 77)

    def test_a_plain_global_keeps_its_value(self):
        rt = run("""
            VAR_GLOBAL g : REAL := 2.5; END_VAR
            FUNCTION_BLOCK FB
            VAR_OUTPUT o : REAL; END_VAR
                o := g * 2.0;
            END_FUNCTION_BLOCK
        """)
        self.assertEqual(rt.get("O"), 5.0)

    def test_a_global_array_initialiser(self):
        rt = run("""
            VAR_GLOBAL tbl : ARRAY[1..3] OF INT := [7, 8, 9]; END_VAR
            FUNCTION_BLOCK FB
            VAR_OUTPUT o : INT; END_VAR
                o := tbl[1] + tbl[3];
            END_FUNCTION_BLOCK
        """)
        self.assertEqual(rt.get("O"), 16)

    def test_a_partially_initialised_global_array_is_zero_padded(self):
        rt = run("""
            VAR_GLOBAL tbl : ARRAY[1..4] OF INT := [1, 2]; END_VAR
            FUNCTION_BLOCK FB
            VAR_OUTPUT o : INT; END_VAR
                o := tbl[4];
            END_FUNCTION_BLOCK
        """)
        self.assertEqual(rt.get("O"), 0)


class TestNamedEnumMembersAreGlobal(unittest.TestCase):
    """IEC 61131-3 exposes enum members unqualified; CODESYS code relies on it."""

    SRC = """
        TYPE T_Mode : (Off, Manual := 10, Auto); END_TYPE
        FUNCTION_BLOCK FB
        VAR_OUTPUT m : T_Mode; n : INT; END_VAR
            m := Auto;
            n := Manual;
        END_FUNCTION_BLOCK
    """

    def test_a_member_can_be_used_unqualified(self):
        rt = run(self.SRC)
        self.assertEqual(rt.get("M"), 11)
        self.assertEqual(rt.get("N"), 10)

    def test_the_qualified_form_still_works(self):
        rt = run("""
            TYPE T_E : (A, B); END_TYPE
            FUNCTION_BLOCK FB
            VAR_OUTPUT m : T_E; END_VAR
                m := T_E#B;
            END_FUNCTION_BLOCK
        """)
        self.assertEqual(rt.get("M"), 1)

    def test_a_real_global_of_the_same_name_wins(self):
        rt = run("""
            VAR_GLOBAL Auto : INT := 99; END_VAR
            TYPE T_Mode : (Off, Manual, Auto); END_TYPE
            FUNCTION_BLOCK FB
            VAR_OUTPUT o : INT; END_VAR
                o := Auto;
            END_FUNCTION_BLOCK
        """)
        self.assertEqual(rt.get("O"), 99)

    def test_a_local_of_the_same_name_wins(self):
        rt = run("""
            TYPE T_Mode : (Off, Manual, Auto); END_TYPE
            FUNCTION_BLOCK FB
            VAR
                Auto : INT := 42;
                m : T_Mode;
            END_VAR
            VAR_OUTPUT o : INT; END_VAR
                m := Auto;
                o := m;
            END_FUNCTION_BLOCK
        """)
        # The local shadows the member, so m gets 42 (wrapped into the enum).
        self.assertEqual(rt.get("O"), 42)


if __name__ == "__main__":
    unittest.main()
