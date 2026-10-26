"""The layers must stay separate, and only point downwards.

``microtuss.core`` is the engine; ``microtuss.testing`` and ``microtuss.cli`` sit
on top of it; ``microtuss.gui`` is a peer of ``cli`` rather than a layer above
it.  If the core ever starts importing a front end, the boundary is gone and
every front end inherits the others' baggage -- so the rule is checked rather
than trusted.

The check runs in a fresh interpreter: by the time a test in this file runs, the
test runner has already imported ``microtuss.testing`` itself, which would mask the
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
    "microtuss.core.session": ("microtuss.testing", "microtuss.cli", "microtuss.gui"),
    "microtuss.core.model": ("microtuss.testing", "microtuss.cli", "microtuss.gui"),
    "microtuss.testing.bench": ("microtuss.cli", "microtuss.gui"),
    # The two front ends are peers: neither is the other's dependency, so
    # neither may import the other.
    "microtuss.cli": ("microtuss.gui",),
    "microtuss.cli.commands": ("microtuss.gui",),
    "microtuss.gui.panel": ("microtuss.testing", "microtuss.cli"),
    "microtuss.gui": ("microtuss.testing", "microtuss.cli"),
}


def _import_trace(module: str) -> str:
    """Import *module* in a clean interpreter and report what came with it."""
    script = (
        "import sys\n"
        f"import {module}\n"
        "print(' '.join(sorted(m for m in sys.modules if m.startswith('microtuss'))))\n"
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
        imported = _import_trace("microtuss.core.session").split()
        self.assertIn("microtuss.core.session", imported)
        # Nothing above the core, and nothing from the standard library that
        # only a command line needs.
        self.assertNotIn("microtuss.testing", imported)

    def test_the_testbench_is_usable_without_the_cli(self):
        imported = _import_trace("microtuss.testing.bench").split()
        self.assertIn("microtuss.testing.bench", imported)
        self.assertNotIn("microtuss.cli", imported)

    def test_the_panel_needs_no_display(self):
        # The window is the only part that wants tkinter; the logic behind it
        # must import and run on a machine with no screen (a test runner, CI).
        imported = _import_trace("microtuss.gui.panel").split()
        self.assertIn("microtuss.gui.panel", imported)
        self.assertNotIn("tkinter", imported)

    def test_importing_the_gui_package_does_not_open_the_window(self):
        imported = _import_trace("microtuss.gui").split()
        self.assertIn("microtuss.gui.panel", imported)
        self.assertNotIn("tkinter", imported)
        self.assertNotIn("microtuss.gui.app", imported)


class TestPublicFacade(unittest.TestCase):
    """``from microtuss import X`` must keep working for every advertised name."""

    def test_every_exported_name_resolves(self):
        import microtuss

        for name in microtuss.__all__:
            with self.subTest(name=name):
                self.assertTrue(hasattr(microtuss, name), f"{name} is advertised but missing")

    def test_an_unknown_name_raises_attribute_error(self):
        import microtuss

        with self.assertRaises(AttributeError):
            microtuss.NoSuchThing  # noqa: B018 - the access is the test


if __name__ == "__main__":
    unittest.main()
