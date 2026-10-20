"""Make core/ importable as plain modules (the same way `python3 core/cli.py` runs)."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (os.path.join(ROOT, "core"), os.path.join(ROOT, "samples")):
    if p not in sys.path:
        sys.path.insert(0, p)
