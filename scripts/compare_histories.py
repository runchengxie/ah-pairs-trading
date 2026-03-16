#!/usr/bin/env python3
"""Spot-check two local history CSVs and print a consistency summary."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ah_pairs_trading.data_quality import compare_history_frames, render_history_comparison


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compare two history CSV files for data QA spot checks.")
    parser.add_argument("--left-csv", required=True, help="Path to the first history CSV.")
    parser.add_argument("--right-csv", required=True, help="Path to the second history CSV.")
    parser.add_argument("--left-label", default="left", help="Display label for the first source.")
    parser.add_argument("--right-label", default="right", help="Display label for the second source.")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    left_frame = pd.read_csv(args.left_csv)
    right_frame = pd.read_csv(args.right_csv)
    summary = compare_history_frames(
        left_frame,
        right_frame,
        left_label=args.left_label,
        right_label=args.right_label,
    )
    print(render_history_comparison(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
