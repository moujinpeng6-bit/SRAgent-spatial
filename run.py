"""Run from a source checkout without installing dependencies."""

import sys
from pathlib import Path

if sys.version_info < (3, 11):
    raise SystemExit("Python 3.11 or newer is required")

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from sragent_spatial.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
