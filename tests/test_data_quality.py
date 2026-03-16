"""Tests for history spot-check QA helpers."""

from __future__ import annotations

import pandas as pd
import pytest

from ah_pairs_trading.data_quality import compare_history_frames, render_history_comparison


def test_compare_history_frames_reports_overlap_and_price_diffs() -> None:
    """Spot checks should quantify both date coverage and close-price divergence."""

    left_frame = pd.DataFrame(
        {
            "date": ["2024-01-02", "2024-01-03", "2024-01-04"],
            "close": [10.0, 10.5, 11.0],
        }
    )
    right_frame = pd.DataFrame(
        {
            "date": ["2024-01-03", "2024-01-04", "2024-01-05"],
            "close": [10.45, 10.95, 11.2],
        }
    )

    summary = compare_history_frames(left_frame, right_frame, left_label="akshare", right_label="efinance")
    rendered = render_history_comparison(summary)

    assert summary.left_observations == 3
    assert summary.right_observations == 3
    assert summary.overlap_observations == 2
    assert summary.left_only_dates == 1
    assert summary.right_only_dates == 1
    assert summary.max_abs_close_diff == pytest.approx(0.05)
    assert "Left source: akshare" in rendered
    assert "Right source: efinance" in rendered
