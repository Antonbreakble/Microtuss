import struct
import unittest

from iecst.errors import IecTypeError
from iecst.values import (
    ARRAY,
    BUILTIN_TYPES,
    ENUM,
    INT,
    STRUCT,
    ArrayValue,
    Cell,
    IecType,
    coerce,
    default_value,
    round_half_away,
    wrap_int,
)


class TestIntegerWrapping(unittest.TestCase):
    def test_signed_overflow_wraps(self):
        self.assertEqual(wrap_int(32768, BUILTIN_TYPES["INT"]), -32768)
        self.assertEqual(wrap_int(-32769, BUILTIN_TYPES["INT"]), 32767)
        self.assertEqual(wrap_int(200, BUILTIN_TYPES["SINT"]), -56)

    def test_unsigned_wraps_modulo(self):
        self.assertEqual(wrap_int(-1, BUILTIN_TYPES["UINT"]), 65535)
        self.assertEqual(wrap_int(256, BUILTIN_TYPES["BYTE"]), 0)
        self.assertEqual(wrap_int(65536, BUILTIN_TYPES["UINT"]), 0)

    def test_bool_is_one_bit(self):
        self.assertEqual(wrap_int(5, BUILTIN_TYPES["BOOL"]), 1)


class TestRounding(unittest.TestCase):
    def test_halves_away_from_zero(self):
        # A controller rounds 2.5 to 3; Python's round() would give 2.
        self.assertEqual(round_half_away(2.5), 3)
        self.assertEqual(round_half_away(-2.5), -3)
        self.assertEqual(round_half_away(2.4), 2)

    def test_coercion_to_int_uses_plc_rounding(self):
        self.assertEqual(coerce(2.5, BUILTIN_TYPES["INT"]), 3)
        self.assertEqual(coerce(-2.5, BUILTIN_TYPES["INT"]), -3)


class TestRealPrecision(unittest.TestCase):
    def test_real_is_stored_as_float32(self):
        value = coerce(0.1, BUILTIN_TYPES["REAL"])
        self.assertEqual(value, struct.unpack("<f", struct.pack("<f", 0.1))[0])
        self.assertNotEqual(value, 0.1)

    def test_lreal_keeps_double_precision(self):
        self.assertEqual(coerce(0.1, BUILTIN_TYPES["LREAL"]), 0.1)


class TestArrays(unittest.TestCase):
    def setUp(self):
        self.t = IecType("A", ARRAY, elem=BUILTIN_TYPES["INT"], bounds=((1, 8),))

    def test_build_and_pad(self):
        a = ArrayValue(self.t)
        a.build([1, 2, 3])
        self.assertEqual([c.value for c in a.items], [1, 2, 3, 0, 0, 0, 0, 0])

    def test_indexing_is_relative_to_lower_bound(self):
        a = ArrayValue(self.t)
        a.build(list(range(1, 9)))
        self.assertEqual(a.cell_at([1]).value, 1)
        self.assertEqual(a.cell_at([8]).value, 8)

    def test_bounds_are_checked(self):
        a = ArrayValue(self.t)
        a.build_defaults()
        for bad in (0, 9):
            with self.assertRaises(IecTypeError):
                a.cell_at([bad])

    def test_too_many_initialisers(self):
        a = ArrayValue(self.t)
        with self.assertRaises(IecTypeError):
            a.build(list(range(20)))

    def test_multi_dimensional_flat_storage(self):
        t = IecType("M", ARRAY, elem=BUILTIN_TYPES["REAL"], bounds=((1, 2), (1, 3)))
        m = ArrayValue(t)
        m.build_defaults()
        self.assertEqual(len(m.items), 6)
        m.cell_at([1, 1]).set(1.0)
        m.cell_at([2, 3]).set(9.0)
        self.assertEqual(m.cell_at([1, 1]).value, 1.0)
        self.assertEqual(m.cell_at([2, 3]).value, 9.0)
        self.assertEqual(m.cell_at([1, 2]).value, 0.0)

    def test_wrong_index_arity(self):
        t = IecType("M", ARRAY, elem=BUILTIN_TYPES["REAL"], bounds=((1, 2), (1, 3)))
        m = ArrayValue(t)
        m.build_defaults()
        with self.assertRaises(IecTypeError):
            m.cell_at([1])


class TestCellsAndAliasing(unittest.TestCase):
    def test_cells_are_the_unit_of_assignment(self):
        original = Cell(10, BUILTIN_TYPES["INT"], "position")
        alias = original  # what a VAR_IN_OUT binding looks like
        alias.set(42)
        self.assertEqual(original.value, 42)

    def test_cell_coerces_on_write(self):
        cell = Cell(0, BUILTIN_TYPES["INT"])
        cell.set(40000)
        self.assertEqual(cell.value, -25536)


class TestNestedMemberDecoding(unittest.TestCase):
    """Convenient spellings must work inside an aggregate, not just at the top.

    ``tb.set(CFG={"mode": "E_OpMode#Automatic", "ramptime": "200ms"})`` assigns
    into a struct field, so the decoding has to live in ``coerce`` rather than
    only in the runtime's top-level path.
    """

    OP_MODE = IecType("E_OPMODE", ENUM, bits=16, members={"OFF": 0, "MANUAL": 10, "AUTOMATIC": 20})

    def test_enum_member_by_bare_name(self):
        self.assertEqual(coerce("Automatic", self.OP_MODE), 20)

    def test_enum_member_by_qualified_name(self):
        self.assertEqual(coerce("E_OpMode#Automatic", self.OP_MODE), 20)

    def test_enum_name_is_case_insensitive(self):
        self.assertEqual(coerce("automatic", self.OP_MODE), 20)

    def test_unknown_enum_member_lists_the_alternatives(self):
        with self.assertRaises(IecTypeError) as caught:
            coerce("Auto", self.OP_MODE)
        self.assertIn("AUTOMATIC", str(caught.exception))

    def test_enum_inside_a_struct(self):
        t = IecType("CFG", STRUCT, fields={"MODE": self.OP_MODE})
        self.assertEqual(coerce({"mode": "Automatic"}, t).fields["MODE"].value, 20)

    def test_time_string_inside_a_struct(self):
        t = IecType("CFG", STRUCT, fields={"RAMP": BUILTIN_TYPES["TIME"]})
        self.assertEqual(coerce({"ramp": "200ms"}, t).fields["RAMP"].value, 200_000_000)

    def test_t_prefixed_time_string_inside_a_struct(self):
        t = IecType("CFG", STRUCT, fields={"RAMP": BUILTIN_TYPES["TIME"]})
        self.assertEqual(coerce({"ramp": "T#1s500ms"}, t).fields["RAMP"].value, 1_500_000_000)

    def test_enum_inside_an_array_of_structs(self):
        elem = IecType("CFG", STRUCT, fields={"MODE": self.OP_MODE})
        t = IecType("CFGS", ARRAY, elem=elem, bounds=((1, 2),))
        a = coerce([{"mode": "Manual"}, {"mode": "Off"}], t)
        self.assertEqual([c.value.fields["MODE"].value for c in a.items], [10, 0])

    def test_deeply_nested_members(self):
        inner = IecType("R", STRUCT, fields={"RAMP": BUILTIN_TYPES["TIME"]})
        outer = IecType("CFG", STRUCT, fields={"MODE": self.OP_MODE, "RANGE": inner})
        cfg = coerce({"mode": "Automatic", "range": {"ramp": "2s"}}, outer)
        self.assertEqual(cfg.fields["MODE"].value, 20)
        self.assertEqual(cfg.fields["RANGE"].value.fields["RAMP"].value, 2_000_000_000)


class TestDefaults(unittest.TestCase):
    def test_struct_defaults(self):
        t = IecType("P", STRUCT, fields={"X": BUILTIN_TYPES["REAL"], "N": BUILTIN_TYPES["INT"]})
        s = default_value(t)
        self.assertEqual({k: c.value for k, c in s.fields.items()}, {"X": 0.0, "N": 0})

    def test_unknown_member_rejected(self):
        t = IecType("P", STRUCT, fields={"X": BUILTIN_TYPES["REAL"]})
        s = default_value(t)
        with self.assertRaises(IecTypeError):
            s.cell("NOPE")

    def test_array_default_has_correct_size(self):
        t = IecType("A", ARRAY, elem=BUILTIN_TYPES["INT"], bounds=((-2, 2),))
        a = default_value(t)
        self.assertEqual(len(a.items), 5)


if __name__ == "__main__":
    unittest.main()
