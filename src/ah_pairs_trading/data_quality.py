"""Source-agnostic data QA helpers for historical price spot checks."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .data import standardize_history_frame


@dataclass(frozen=True, slots=True)
class HistoryComparisonSummary:
    """High-level consistency summary for two normalized history frames."""

    left_label: str
    right_label: str
    left_observations: int
    right_observations: int
    overlap_observations: int
    left_only_dates: int
    right_only_dates: int
    left_overlap_ratio: float
    right_overlap_ratio: float
    max_abs_close_diff: float | None
    mean_abs_close_diff: float | None
    max_rel_close_diff: float | None
    mean_rel_close_diff: float | None

    def as_dict(self) -> dict[str, float | int | str | None]:
        return {
            "left_label": self.left_label,
            "right_label": self.right_label,
            "left_observations": self.left_observations,
            "right_observations": self.right_observations,
            "overlap_observations": self.overlap_observations,
            "left_only_dates": self.left_only_dates,
            "right_only_dates": self.right_only_dates,
            "left_overlap_ratio": self.left_overlap_ratio,
            "right_overlap_ratio": self.right_overlap_ratio,
            "max_abs_close_diff": self.max_abs_close_diff,
            "mean_abs_close_diff": self.mean_abs_close_diff,
            "max_rel_close_diff": self.max_rel_close_diff,
            "mean_rel_close_diff": self.mean_rel_close_diff,
        }


def compare_history_frames(
    left_frame: pd.DataFrame,
    right_frame: pd.DataFrame,
    *,
    left_label: str = "left",
    right_label: str = "right",
) -> HistoryComparisonSummary:
    """Compare two price-history frames after shared normalization."""

    left_history = standardize_history_frame(left_frame)[["close"]].rename(columns={"close": "left_close"})
    right_history = standardize_history_frame(right_frame)[["close"]].rename(columns={"close": "right_close"})
    overlap = left_history.join(right_history, how="inner").dropna()

    max_abs_close_diff = None
    mean_abs_close_diff = None
    max_rel_close_diff = None
    mean_rel_close_diff = None
    if not overlap.empty:
        absolute_diff = (overlap["left_close"] - overlap["right_close"]).abs()
        denominator = overlap["right_close"].abs().replace(0.0, pd.NA)
        relative_diff = (absolute_diff / denominator).dropna()
        max_abs_close_diff = float(absolute_diff.max())
        mean_abs_close_diff = float(absolute_diff.mean())
        max_rel_close_diff = float(relative_diff.max()) if not relative_diff.empty else None
        mean_rel_close_diff = float(relative_diff.mean()) if not relative_diff.empty else None

    return HistoryComparisonSummary(
        left_label=left_label,
        right_label=right_label,
        left_observations=int(len(left_history)),
        right_observations=int(len(right_history)),
        overlap_observations=int(len(overlap)),
        left_only_dates=int(len(left_history.index.difference(right_history.index))),
        right_only_dates=int(len(right_history.index.difference(left_history.index))),
        left_overlap_ratio=float(len(overlap) / len(left_history)) if len(left_history) else 0.0,
        right_overlap_ratio=float(len(overlap) / len(right_history)) if len(right_history) else 0.0,
        max_abs_close_diff=max_abs_close_diff,
        mean_abs_close_diff=mean_abs_close_diff,
        max_rel_close_diff=max_rel_close_diff,
        mean_rel_close_diff=mean_rel_close_diff,
    )


def render_history_comparison(summary: HistoryComparisonSummary) -> str:
    """Render a terminal-friendly QA report for two data histories."""

    def format_percent(value: float | None) -> str:
        if value is None:
            return "n/a"
        return f"{value:.2%}"

    def format_float(value: float | None) -> str:
        if value is None:
            return "n/a"
        return f"{value:,.6f}"

    lines = [
        "History Spot Check",
        f"- Left source: {summary.left_label}",
        f"- Right source: {summary.right_label}",
        f"- Left observations: {summary.left_observations}",
        f"- Right observations: {summary.right_observations}",
        f"- Overlap observations: {summary.overlap_observations}",
        f"- Left-only dates: {summary.left_only_dates}",
        f"- Right-only dates: {summary.right_only_dates}",
        f"- Left overlap ratio: {format_percent(summary.left_overlap_ratio)}",
        f"- Right overlap ratio: {format_percent(summary.right_overlap_ratio)}",
        f"- Max abs close diff: {format_float(summary.max_abs_close_diff)}",
        f"- Mean abs close diff: {format_float(summary.mean_abs_close_diff)}",
        f"- Max relative close diff: {format_percent(summary.max_rel_close_diff)}",
        f"- Mean relative close diff: {format_percent(summary.mean_rel_close_diff)}",
    ]
    return "\n".join(lines)
