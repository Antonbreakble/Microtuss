import unittest

from microtuss.core.errors import IecSyntaxError
from microtuss.core.lexer import tokenize


def kinds(src):
    return [(t.kind, t.value) for t in tokenize(src) if t.kind != "eof"]


class TestNumbers(unittest.TestCase):
    def test_based_integers(self):
        self.assertEqual(kinds("16#FF"), [("int", 255)])
        self.assertEqual(kinds("2#1010"), [("int", 10)])
        self.assertEqual(kinds("8#17"), [("int", 15)])
        self.assertEqual(kinds("0x1F"), [("int", 31)])

    def test_underscores_in_digits(self):
        self.assertEqual(kinds("1_000_000"), [("int", 1000000)])

    def test_reals_vs_range_operator(self):
        # '..' must not be mistaken for a decimal point.
        self.assertEqual(kinds("1.5"), [("real", 1.5)])
        self.assertEqual(
            kinds("1..10"),
            [("int", 1), ("op", ".."), ("int", 10)],
        )
        self.assertEqual(kinds("1.5E3"), [("real", 1500.0)])
        self.assertEqual(kinds("2.5e-2"), [("real", 0.025)])

    def test_field_access_after_integer(self):
        # A bare '.' followed by a name is a field access, not a fraction.
        self.assertEqual(kinds("3.5"), [("real", 3.5)])


class TestLiterals(unittest.TestCase):
    def test_typed_integer(self):
        toks = tokenize("INT#5")
        self.assertEqual((toks[0].kind, toks[0].value, toks[0].type_name), ("int", 5, "INT"))

    def test_typed_real_and_string(self):
        self.assertEqual(kinds("REAL#2"), [("real", 2.0)])
        self.assertEqual(kinds("STRING#'abc'"), [("str", "abc")])

    def test_time_literals(self):
        self.assertEqual(kinds("T#100ms")[0][1], 100_000_000)
        self.assertEqual(kinds("TIME#1m30s")[0][1], 90_000_000_000)
        self.assertEqual(kinds("T#1h")[0][1], 3_600_000_000_000)
        self.assertEqual(kinds("T#1.5s")[0][1], 1_500_000_000)
        self.assertEqual(kinds("T#-2s")[0][1], -2_000_000_000)
        # T#0s must not be confused with the identifier T.
        self.assertEqual(kinds("T#0s")[0][1], 0)

    def test_date_literals(self):
        self.assertEqual(kinds("D#2024-02-29")[0][0], "date")
        self.assertEqual(kinds("TOD#12:30:15")[0][1], (12 * 3600 + 30 * 60 + 15) * 10**9)
        self.assertEqual(kinds("DT#2024-01-01-00:00:00")[0][0], "dt")

    def test_invalid_date_rejected(self):
        with self.assertRaises(IecSyntaxError):
            tokenize("D#2023-02-29")

    def test_tod_out_of_range(self):
        with self.assertRaises(IecSyntaxError):
            tokenize("TOD#25:00:00")

    def test_string_escapes(self):
        self.assertEqual(kinds("'don''t'"), [("str", "don't")])
        self.assertEqual(kinds("'a$nb'"), [("str", "a\nb")])
        self.assertEqual(kinds("'100$$'"), [("str", "100$")])
        # A trailing '$' escapes the closing quote, so the literal never ends.
        with self.assertRaises(IecSyntaxError):
            tokenize("'100$'")
        self.assertEqual(kinds('"wide"'), [("wstr", "wide")])


class TestTriviaAndIdentifiers(unittest.TestCase):
    def test_nested_comments(self):
        self.assertEqual(kinds("A (* x (* y *) z *) B"), [("ident", "A"), ("ident", "B")])

    def test_unterminated_comment_reports_position(self):
        with self.assertRaises(IecSyntaxError) as cm:
            tokenize("A (* unterminated")
        self.assertEqual(cm.exception.line, 1)
        self.assertEqual(cm.exception.message, "unterminated comment '(*'")

    def test_pragma_is_dropped(self):
        self.assertEqual(kinds("{attribute 'hide'} X"), [("ident", "X")])

    def test_line_comments(self):
        self.assertEqual(kinds("A // comment\nB"), [("ident", "A"), ("ident", "B")])

    def test_identifiers_are_case_insensitive(self):
        self.assertEqual(kinds("myVar")[0][1], "MYVAR")
        # ...but the original spelling survives for error messages.
        self.assertEqual(tokenize("myVar")[0].raw, "myVar")

    def test_keywords_recognised_case_insensitively(self):
        self.assertEqual(kinds("end_if"), [("kw", "END_IF")])

    def test_enum_hash_is_a_token(self):
        self.assertEqual(
            kinds("Color#Red"),
            [("ident", "COLOR"), ("op", "#"), ("ident", "RED")],
        )

    def test_address_literal(self):
        self.assertEqual(kinds("%IX0.0"), [("address", "%IX0.0")])

    def test_line_tracking(self):
        toks = tokenize("A\n\n  B")
        self.assertEqual((toks[0].line, toks[0].col), (1, 1))
        self.assertEqual((toks[1].line, toks[1].col), (3, 3))

    def test_unexpected_character(self):
        with self.assertRaises(IecSyntaxError) as cm:
            tokenize("A ? B")
        self.assertIn("unexpected character", cm.exception.message)


if __name__ == "__main__":
    unittest.main()
