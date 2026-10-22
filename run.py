"""Run from the repository without requiring an editable install."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "backend/src"))

if __name__ == "__main__":
    from finwhiz.cli import main

    main()
