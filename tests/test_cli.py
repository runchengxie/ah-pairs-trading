"""Tests for the command-line entry point."""

from __future__ import annotations

from ah_pairs_trading import cli
from ah_pairs_trading import pipeline


def test_cli_prints_rendered_scorecard(monkeypatch, capsys) -> None:
    """The CLI should print the full rendered scorecard, not a partial summary."""

    dummy_result = object()

    monkeypatch.setattr(pipeline, "run_ah_relative_value_pipeline", lambda config: dummy_result)
    monkeypatch.setattr(pipeline, "build_pipeline_summary", lambda config, result: {"ok": True, "result": result})
    monkeypatch.setattr(pipeline, "render_pipeline_scorecard", lambda summary: "FULL SCORECARD")

    exit_code = cli.main(["--constant-fx-rate", "0.92"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured.out.strip() == "FULL SCORECARD"
