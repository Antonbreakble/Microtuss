"""Testing a FUNCTION, and driving a FUNCTION_BLOCK instance by hand.

    python3 examples/07_functions.py

A ``FUNCTION`` has no instance and no state, so it is called like a Python
function: arguments by name, the same names the ST declaration uses.  A
``FUNCTION_BLOCK`` can also be driven on its own -- useful when the block under
test is a helper of a larger one, and you want to pin it down in isolation.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from iecst import Testbench  # noqa: E402
from iecst.model import load_sources  # noqa: E402

HERE = Path(__file__).resolve().parent
MODEL = load_sources(
    {
        str(p): p.read_text(encoding="utf-8-sig")
        for p in sorted((HERE / "project").glob("*.st"))
    }
)
tb = Testbench(MODEL, "FB_Tank")
call = tb.runtime.call

print("calling a FUNCTION")
print(f"  Pct(v=25, lo=0, hi=50)   -> {call('Pct', v=25.0, lo=0.0, hi=50.0)!r}")
print(f"  Pct(v=10, lo=10, hi=10)  -> {call('Pct', v=10.0, lo=10.0, hi=10.0)!r}  (zero width)")

print("\ndriving the library's TON instance directly")
runtime = tb.runtime
inst = runtime.instantiate(runtime.model.type_of("TON"))
inst.vars["PT"].value = 200_000_000  # T#200ms, in nanoseconds


def tick() -> None:
    """Run one scan of the instance.

    ``runtime.step()`` does exactly this for the POU under test and then
    advances the clock, which is what makes the timeouts reproducible.
    """
    runtime.execute_instance(inst)
    runtime.now += runtime.cycle_ns


for _ in range(4):
    inst.vars["IN"].value = True
    tick()
    print(f"  IN=TRUE   Q={str(inst.vars['Q'].value):<5} ET={inst.vars['ET'].value / 1_000_000:.0f}ms")

inst.vars["IN"].value = False
tick()
print(f"  IN=FALSE  Q={str(inst.vars['Q'].value):<5} ET={inst.vars['ET'].value / 1_000_000:.0f}ms"
      f"   (the timer drops out with the input)")
