"""``python -m microtuss.gui [folder] [pou]`` opens the window, optionally pre-filled."""

from __future__ import annotations

import sys

from microtuss.gui.app import main

if __name__ == "__main__":
    positional = sys.argv[1:]
    raise SystemExit(main(*(positional[:2])))
