"""Make the flat pipeline modules importable in the tests."""

from __future__ import annotations

import sys
from pathlib import Path

TOOLS_DATASET = Path(__file__).resolve().parents[2] / "tools" / "dataset"
if str(TOOLS_DATASET) not in sys.path:
    sys.path.insert(0, str(TOOLS_DATASET))
