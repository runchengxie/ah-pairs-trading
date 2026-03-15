"""Pairs trading package built around a cointegration workflow."""

from .config import BacktestConfig, PipelineConfig, RollingConfig, SegmentWindow

__all__ = [
    "BacktestConfig",
    "PipelineConfig",
    "RollingConfig",
    "SegmentWindow",
]
