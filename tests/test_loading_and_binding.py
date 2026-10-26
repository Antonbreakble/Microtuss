"""Loading a POU, and the two ways to bind a VAR_IN_OUT.

These cover entry points that a front end uses and the engine tests do not:
``from_folder`` inferring the POU, and ``bind`` wiring one block's storage into
another block's VAR_IN_OUT.  Both were broken while writing the Python guide --
the inference passed an empty name straight through, and ``bind`` treated the
second argument as a value to store rather than as storage to alias.
"""

import unittest
from pathlib import Path

from microtuss import Session, Testbench, load_sources
from microtuss.core.errors import IecError, IecNameError, IecSyntaxError, IecTypeError
from microtuss.core.session import check

DUTS = """
TYPE T_Limits : STRUCT
    Lo : REAL := 10.0;
    Hi : REAL := 90.0;
END_STRUCT END_TYPE
"""

MAIN = """
FUNCTION_BLOCK FB_Tank
VAR_INPUT Fill : BOOL;
END_VAR
VAR_OUTPUT State : INT;
END_VAR
    State := 1;
END_FUNCTION_BLOCK
"""

PRODUCER = """
FUNCTION_BLOCK Producer
VAR_IN_OUT pos : INT; END_VAR
    pos := pos + 1;
END_FUNCTION_BLOCK
"""

CONSUMER = """
FUNCTION_BLOCK Consumer
VAR_IN_OUT pos : INT; END_VAR
VAR_OUTPUT seen : INT; END_VAR
    seen := pos;
END_FUNCTION_BLOCK
"""


class TestFolderInference(unittest.TestCase):
    def test_the_sole_pou_is_inferred_when_no_name_is_given(self):
        # Only one FUNCTION_BLOCK in the tree, so the name need not be passed.
        session = Session.from_sources({"a.st": DUTS, "b.st": MAIN}, pou="FB_Tank")
        self.assertEqual("FB_TANK", session.pou_name.upper())

    def test_an_empty_name_is_not_silently_accepted(self):
        # ``from_folder(folder, None)`` must infer or complain, never pass an
        # empty string down to the runtime.
        with self.assertRaises(IecNameError) as cm:
            Session.from_folder("examples", None)
        self.assertIn("cannot infer", cm.exception.message)

    def test_the_error_lists_the_candidates(self):
        with self.assertRaises(IecNameError) as cm:
            Session.from_folder("examples", None)
        # Both blocks in examples/ are named, so the choice is possible.
        self.assertIn("FB_TANK", cm.exception.message)
        self.assertIn("FB_VALVE", cm.exception.message)

    def test_from_folder_accepts_an_explicit_name(self):
        session = Session.from_folder("examples/project", "FB_Tank", cycle_time="100ms")
        self.assertEqual("FB_Tank", session.pou_name)

    def test_the_testbench_infers_the_same_way_as_the_session(self):
        bench = Testbench.from_folder("examples/project", "FB_Tank", cycle_time="100ms")
        self.assertEqual("FB_Tank", bench.pou_name)


class TestBindAliasesStorage(unittest.TestCase):
    """``bind(name, cell)`` aliases; ``bind(name, value)`` writes."""

    def _pair(self):
        model = load_sources({"p.st": PRODUCER, "c.st": CONSUMER})
        return model, Testbench(model, "Producer"), Testbench(model, "Consumer")

    def test_two_blocks_can_share_one_cell(self):
        _model, producer, consumer = self._pair()
        cell = producer.runtime.bind("pos")
        consumer.runtime.bind("pos", cell)

        producer.step()
        self.assertEqual(1, producer.get("pos"))
        consumer.step()
        self.assertEqual(1, consumer.get("seen"), "the consumer reads the same storage")

        producer.step()
        consumer.step()
        self.assertEqual(2, consumer.get("seen"))

    def test_a_plain_value_still_writes(self):
        _model, producer, _consumer = self._pair()
        producer.runtime.bind("pos", 41)
        self.assertEqual(41, producer.get("pos"))

    def test_binding_a_cell_to_something_that_is_not_inout_is_refused(self):
        model = load_sources({"<t>": """
            FUNCTION_BLOCK FB
            VAR_IN_OUT pos : INT; END_VAR
            VAR plain : INT; END_VAR
                pos := pos + plain;
            END_FUNCTION_BLOCK
        """})
        bench = Testbench(model, "FB")
        alias = bench.runtime.bind("pos")
        with self.assertRaises(IecTypeError) as cm:
            bench.runtime.bind("plain", alias)
        self.assertIn("not a VAR_IN_OUT", cm.exception.message)

    def test_an_inout_chain_resolves_to_the_real_storage(self):
        # Binding a VAR_IN_OUT to another VAR_IN_OUT must land on the cell
        # holding the storage, not on the link.
        model = load_sources({"p.st": PRODUCER, "c.st": CONSUMER, "d.st": """
            FUNCTION_BLOCK Middle
            VAR_IN_OUT pos : INT; END_VAR
                pos := pos;
            END_FUNCTION_BLOCK
        """})
        producer = Testbench(model, "Producer")
        middle = Testbench(model, "Middle")
        consumer = Testbench(model, "Consumer")

        producer.runtime.bind("pos", 7)
        middle.runtime.bind("pos", producer.runtime.bind("pos"))
        consumer.runtime.bind("pos", middle.runtime.bind("pos"))

        consumer.step()
        self.assertEqual(7, consumer.get("seen"))


class TestCheckReportsRatherThanRaises(unittest.TestCase):
    """``check`` answers with a list of problems; it never raises.

    Its docstring promised exactly that, but it delegated to ``load``, which
    raises while linking -- so a syntax error escaped as an exception and an
    unknown type came back as one.  A caller that wants a report, not a
    traceback, needs all three outcomes to be the same shape.
    """

    def test_well_formed_sources_produce_no_problems(self):
        self.assertEqual([], check({"a.st": MAIN, "b.st": DUTS}))

    def test_a_syntax_error_is_reported_not_raised(self):
        problems = check({"bad.st": "FUNCTION_BLOCK B\nx := ;\nEND_FUNCTION_BLOCK"})
        self.assertEqual(1, len(problems))
        self.assertIsInstance(problems[0], IecSyntaxError)
        self.assertIn("expected an expression", problems[0].message)

    def test_an_unknown_type_is_reported_not_raised(self):
        problems = check({"a.st": "FUNCTION_BLOCK B VAR x : NoSuchType; END_VAR END_FUNCTION_BLOCK"})
        self.assertEqual(1, len(problems))
        self.assertIsInstance(problems[0], IecTypeError)
        self.assertIn("NOSUCHTYPE", problems[0].message)

    def test_the_reported_problem_keeps_its_coordinate(self):
        # .format() is what the CLI prints, so the position must survive.
        problem = check({"bad.st": "FUNCTION_BLOCK B\nx := ;\nEND_FUNCTION_BLOCK"})[0]
        self.assertEqual("bad.st", problem.source)
        self.assertEqual(2, problem.line)

    def test_a_folder_is_accepted_like_the_other_loaders(self):
        # A CODESYS project is a folder, so this is the natural call -- and it
        # used to fail with a bare IsADirectoryError, not an IecError.
        self.assertEqual([], check("examples/project"))
        self.assertEqual([], check(Path("examples/project")))

    def test_a_missing_file_is_reported_not_raised(self):
        # "It never raises" has to include the file not being there at all;
        # otherwise the CLI dumps a traceback for a plain typo in a path.
        problems = check("no/such/file.st")
        self.assertEqual(1, len(problems))
        self.assertIsInstance(problems[0], IecError)
        self.assertIn("cannot read", problems[0].message)


if __name__ == "__main__":
    unittest.main()
