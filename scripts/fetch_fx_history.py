#!/usr/bin/env python3
"""Download HKD/CNY FX history into a project-compatible CSV."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ah_pairs_trading.fx_download import main


if __name__ == "__main__":
    raise SystemExit(main())
