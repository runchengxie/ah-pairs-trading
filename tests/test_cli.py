"""Tests for the command-line entry point."""

from __future__ import annotations

from pathlib import Path

import pytest

from ah_pairs_trading import cli
from ah_pairs_trading import pipeline


def test_cli_parser_uses_artifacts_cache_dir_by_default() -> None:
    """The default cache directory should live under `artifacts/cache`."""

    args = cli.build_parser().parse_args([])

    assert args.cache_dir == Path("artifacts/cache/ah_pairs_trading")
    assert args.output_dir is None


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
            "--hedge-ratio-mode",
            "rolling",
            "--return-filter-mode",
            "ema",
            "--cointegration-gate-mode",
            "significant",
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
    assert config.strategy.hedge_ratio_mode == "rolling"
    assert config.strategy.return_filter_mode == "ema"
    assert config.strategy.cointegration_gate_mode == "significant"
    assert config.strategy.return_filter_window == 7
    assert config.strategy.return_filter_min_periods == 5


def test_cli_loads_smoke_preset_via_config_flag(monkeypatch) -> None:
    """`--config` should accept the checked-in smoke preset without extra glue code."""

    captured_config = {}

    def fake_run(config):
        captured_config["config"] = config
        return object()

    monkeypatch.setattr(pipeline, "run_ah_relative_value_pipeline", fake_run)
    monkeypatch.setattr(pipeline, "build_pipeline_summary", lambda config, result: {"ok": True})
    monkeypatch.setattr(pipeline, "render_pipeline_scorecard", lambda summary: "ok")

    exit_code = cli.main(["--config", "configs/petrochina_smoke.toml"])

    config = captured_config["config"]
    assert exit_code == 0
    assert config.a_symbol == "601857"
    assert config.h_symbol == "00857"
    assert config.data.constant_fx_rate == 0.92
    assert config.require_significant_cointegration is False
    assert config.cache_dir == Path("artifacts/cache/ah_pairs_trading").resolve()
    assert config.output_dir == Path("artifacts/runs/petrochina_ah_smoke").resolve()


def test_cli_loads_research_preset_via_config_flag(monkeypatch) -> None:
    """The checked-in research preset should stay aligned with current CLI flags."""

    captured_config = {}

    def fake_run(config):
        captured_config["config"] = config
        return object()

    monkeypatch.setattr(pipeline, "run_ah_relative_value_pipeline", fake_run)
    monkeypatch.setattr(pipeline, "build_pipeline_summary", lambda config, result: {"ok": True})
    monkeypatch.setattr(pipeline, "render_pipeline_scorecard", lambda summary: "ok")

    exit_code = cli.main(["--config", "configs/petrochina_research.toml"])

    config = captured_config["config"]
    assert exit_code == 0
    assert config.a_symbol == "601857"
    assert config.h_symbol == "00857"
    assert config.data.fx_csv_path == Path("data/fx/hkdcny_2018_2024.csv").resolve()
    assert config.strategy.hedge_ratio_mode == "rolling"
    assert config.strategy.cointegration_gate_mode == "significant"
    assert config.same_issuer_check == "strict"
    assert config.output_dir == Path("artifacts/runs/petrochina_ah_research").resolve()


def test_cli_explicit_flags_override_config_values(monkeypatch, tmp_path) -> None:
    """Explicit CLI flags should win over values loaded from a TOML preset."""

    config_path = tmp_path / "preset.toml"
    config_path.write_text(
        "\n".join(
            [
                'a_symbol = "601857"',
                'h_symbol = "00857"',
                'fx_csv = "data/fx/from_config.csv"',
                'z_grid = [1.1, 1.6]',
                'benchmark_mode = "internal"',
                'output_dir = "artifacts/runs/from_config"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    captured_config = {}

    def fake_run(config):
        captured_config["config"] = config
        return object()

    monkeypatch.setattr(pipeline, "run_ah_relative_value_pipeline", fake_run)
    monkeypatch.setattr(pipeline, "build_pipeline_summary", lambda config, result: {"ok": True})
    monkeypatch.setattr(pipeline, "render_pipeline_scorecard", lambda summary: "ok")

    exit_code = cli.main(
        [
            "--config",
            str(config_path),
            "--benchmark-mode",
            "off",
            "--output-dir",
            "artifacts/runs/from_cli",
            "--z-grid",
            "2.0,2.5",
        ]
    )

    config = captured_config["config"]
    assert exit_code == 0
    assert config.benchmark_mode == "off"
    assert config.output_dir == Path("artifacts/runs/from_cli")
    assert config.data.fx_csv_path == (tmp_path / "data/fx/from_config.csv").resolve()
    assert config.strategy.entry_z_candidates == (2.0, 2.5)


def test_cli_rejects_unknown_config_keys(tmp_path) -> None:
    """TOML presets should fail fast on typos instead of silently drifting."""

    config_path = tmp_path / "bad.toml"
    config_path.write_text('a_symbol = "601857"\nunknown_key = "boom"\n', encoding="utf-8")

    with pytest.raises(ValueError, match="unsupported keys: unknown_key"):
        cli.main(["--config", str(config_path), "--constant-fx-rate", "0.92"])
