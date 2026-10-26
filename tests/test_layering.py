"""The layers must stay separate, and only point downwards.

``iecst.core`` is the engine; ``iecst.testing`` and ``iecst.cli`` sit on top of
it; a GUI would be a third peer of ``cli``.  If the core ever starts importing a
front end, the boundary is gone and every future front end inherits the others'
baggage -- so the rule is checked rather than trusted.

The check runs in a fresh interpreter: by the time a test in this file runs, the
test runner has already imported ``iecst.testing`` itself, which would mask the
very leak being looked for.
"""

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Each entry: a module a front end may import, and the packages it must not
# drag in by doing so.
FORBIDDEN = {
    "iecst.core.session": ("iecst.testing", "iecst.cli", "iecst.gui"),
    "iecst.core.model": ("iecst.testing", "iecst.cli", "iecst.gui"),
    "iecst.testing.bench": ("iecst.cli", "iecst.gui"),
    # The two front ends are peers: neither is the other's dependency, so
    # neither may import the other.
    "iecst.cli": ("iecst.gui",),
    "iecst.cli.commands": ("iecst.gui",),
    "iecst.gui.panel": ("iecst.testing", "iecst.cli"),
    "iecst.gui": ("iecst.testing", "iecst.cli"),
}


def _import_trace(module: str) -> str:
    """Import *module* in a clean interpreter and report what came with it."""
    script = (
        "import sys\n"
        f"import {module}\n"
        "print(' '.join(sorted(m for m in sys.modules if m.startswith('iecst'))))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


class TestLayerBoundaries(unittest.TestCase):
    def test_layers_only_import_downwards(self):
        for module, forbidden in FORBIDDEN.items():
            with self.subTest(module=module):
                imported = _import_trace(module).split()
                for prefix in forbidden:
                    leaked = [m for m in imported if m.startswith(prefix)]
                    self.assertEqual(
                        [], leaked, f"{module} must not pull in {prefix}: {leaked}"
                    )

    def test_the_engine_is_importable_without_a_front_end(self):
        imported = _import_trace("iecst.core.session").split()
        self.assertIn("iecst.core.session", imported)
        # Nothing above the core, and nothing from the standard library that
        # only a command line needs.
        self.assertNotIn("iecst.testing", imported)

    def test_the_testbench_is_usable_without_the_cli(self):
        imported = _import_trace("iecst.testing.bench").split()
        self.assertIn("iecst.testing.bench", imported)
        self.assertNotIn("iecst.cli", imported)

    def test_the_panel_needs_no_display(self):
        # The window is the only part that wants tkinter; the logic behind it
        # must import and run on a machine with no screen (a test runner, CI).
        imported = _import_trace("iecst.gui.panel").split()
        self.assertIn("iecst.gui.panel", imported)
        self.assertNotIn("tkinter", imported)

    def test_importing_the_gui_package_does_not_open_the_window(self):
        imported = _import_trace("iecst.gui").split()
        self.assertIn("iecst.gui.panel", imported)
        self.assertNotIn("tkinter", imported)
        self.assertNotIn("iecst.gui.app", imported)


class TestPublicFacade(unittest.TestCase):
    """``from iecst import X`` must keep working for every advertised name."""

    def test_every_exported_name_resolves(self):
        import iecst

        for name in iecst.__all__:
            with self.subTest(name=name):
                self.assertTrue(hasattr(iecst, name), f"{name} is advertised but missing")

    def test_an_unknown_name_raises_attribute_error(self):
        import iecst

        with self.assertRaises(AttributeError):
            iecst.NoSuchThing  # noqa: B018 - the access is the test


if __name__ == "__main__":
    unittest.main()
