"""What a mistake looks like when a test is wrong.

    python3 examples/08_mistakes.py

Every one of these is a *test-side* error: a misspelled name, a value of the
wrong type, an index outside the declared bounds.  They are reported rather
than silently coerced, because a test that quietly passes on a typo is worse
than one that fails.
"""

import sys
from pathlib import Path

# This file sits in examples/, so running it directly puts examples/ -- not
# the repository root -- on sys.path, and `microtuss` would not be importable.
# Add the root.  After `pip install -e .` the package is on the path anyway,
# and this line changes nothing.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from microtuss import Testbench  # noqa: E402
from microtuss.core.model import load_sources  # noqa: E402

HERE = Path(__file__).resolve().parent
MODEL = load_sources(
    {
        str(p): p.read_text(encoding="utf-8-sig")
        for p in sorted((HERE / "project").glob("*.st"))
    }
)


def show(what: str, action) -> None:
    try:
        action()
    except Exception as exc:  # noqa: BLE001 - the point is to print it
        print(f"{what}\n    {type(exc).__name__}: {exc}\n")
    else:
        print(f"{what}\n    (no error)\n")


tb = Testbench(MODEL, "FB_Tank")

show("a misspelled variable", lambda: tb.get("Levels"))

show("a misspelled DUT member", lambda: tb.set(Limits={"lol": 1.0}))

show("an unknown enumeration member", lambda: tb.set(State="FULLISH"))

show("a value of the wrong type", lambda: tb.set(Level="high"))

show("reading through something that is not a struct", lambda: tb.get("Level.Hi"))

# A failing expectation is not an exception: it is recorded, with both values
# and the scan it happened at, so one run reports every mismatch.
tb.scan(Fill=True, Level=20.0)
tb.expect(State="FULL", Percent=0.0)
print("a failing expectation")
for failure in tb.failures:
    print(f"    {failure.describe()}")

print("\nand in the shell, the same thing decides the exit code:")
print("    python3 -m microtuss run examples/project/Tank.st \\")
print("        --include examples/project/Types.st --include examples/project/Utils.st \\")
print("        --set Fill=true --scans 5 --expect State=Full")
