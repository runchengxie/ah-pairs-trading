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


def test_cli_passes_same_issuer_and_benchmark_flags(monkeypatch) -> None:
    """CLI arguments should flow into the pipeline config without silent defaults."""

    captured_config = {}

    def fake_run(config):
        captured_config["config"] = config
        return object()

    monkeypatch.setattr(pipeline, "run_ah_relative_value_pipeline", fake_run)
    monkeypatch.setattr(pipeline, "build_pipeline_summary", lambda config, result: {"ok": True})
    monkeypatch.setattr(pipeline, "render_pipeline_scorecard", lambda summary: "ok")

    exit_code = cli.main(
        [
            "--constant-fx-rate",
            "0.92",
            "--entry-signal-mode",
            "zscore",
            "--return-filter-mode",
            "ema",
            "--return-filter-window",
            "7",
            "--return-filter-min-periods",
            "5",
            "--benchmark-mode",
            "internal",
            "--internal-benchmark-weighting",
            "equal_weight",
            "--same-issuer-check",
            "warn",
        ]
    )

    config = captured_config["config"]
    assert exit_code == 0
    assert config.benchmark_mode == "internal"
    assert config.internal_benchmark_weighting == "equal_weight"
    assert config.same_issuer_check == "warn"
    assert config.strategy.entry_signal_mode == "zscore"
    assert config.strategy.return_filter_mode == "ema"
    assert config.strategy.return_filter_window == 7
    assert config.strategy.return_filter_min_periods == 5
