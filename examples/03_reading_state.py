"""Reading values back: plain variables, nested blocks, enums and TIME.

    python3 examples/03_reading_state.py

``get`` takes the same dotted path the ST source uses (``travel.Q``), and hands
back a Python value: booleans for BOOL, floats for REAL, ints for INTEGER.  For
the two types that have no natural Python spelling -- TIME and enumerations --
``state()`` renders the readable form instead.
"""

import sys
from pathlib import Path

# This file sits in examples/, so running it directly puts examples/ -- not
# the repository root -- on sys.path, and `microtuss` would not be importable.
# Add the root.  After `pip install -e .` the package is on the path anyway,
# and this line changes nothing.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from microtuss import Testbench  # noqa: E402

HERE = Path(__file__).resolve().parent

tb = Testbench.from_file(HERE / "valve.st", "FB_Valve", cycle_time="100ms")
tb.scan(Cmd=True)
tb.run(20)

print("single values")
print(f"  get('Open')          -> {tb.get('Open')!r}")
print(f"  get('travel.ET')     -> {tb.get('travel.ET')!r}  (nanoseconds)")
print(f"  get('travel.Q')      -> {tb.get('travel.Q')!r}")

print("\nan enumeration is an integer, but reads as its member")
print(f"  get('State')         -> {tb.get('State')!r}")
print(f"  state()['STATE']     -> {tb.state()['STATE']!r}")

print("\nstate() -- everything at once, TIME as T#..., enums by name")
for name, value in tb.state().items():
    if name == "TRAVEL":  # a nested block, already covered above
        continue
    print(f"  {name:<12} {value!r}")

print("\nwriting goes through the same names")
tb.set("TravelTime", "T#500ms")
tb.set(State="Closed")  # enums accept the member name, quoted or not
print(f"  TravelTime           -> {tb.state()['TRAVELTIME']!r}")
print(f"  State                -> {tb.get('State')!r}")
