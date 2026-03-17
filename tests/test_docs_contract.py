"""Lightweight contract tests for README examples and docs navigation."""

from __future__ import annotations

from pathlib import Path

from ah_pairs_trading.fx_download import build_parser as build_fx_download_parser


PROJECT_ROOT = Path(__file__).resolve().parents[1]
README_PATH = PROJECT_ROOT / "README.md"


def test_readme_documents_config_and_testing_entrypoints() -> None:
    """README should advertise the supported config and test workflows."""

    readme = README_PATH.read_text(encoding="utf-8")

    assert "pairs-trading --config configs/petrochina_smoke.toml" in readme
    assert "pairs-trading --config configs/petrochina_research.toml" in readme
    assert "bash scripts/test.sh" in readme
    assert "scripts/test.sh unit" in readme
    assert "scripts/test.sh integration" in readme
    assert "scripts/test.sh coverage" in readme


def test_readme_links_only_to_existing_docs() -> None:
    """README navigation should not point at missing documentation pages."""

    expected_docs = [
        "docs/strategy_overview.md",
        "docs/execution_modes.md",
        "docs/data_and_fx.md",
        "docs/benchmark_and_metrics.md",
        "docs/cli_reference.md",
        "docs/common_errors.md",
        "docs/development.md",
        "docs/legacy/project_design_v1.0.md",
    ]
    readme = README_PATH.read_text(encoding="utf-8")

    for relative_path in expected_docs:
        assert relative_path in readme
        assert (PROJECT_ROOT / relative_path).is_file()


def test_fx_download_readme_example_matches_current_parser() -> None:
    """The README FX download example should remain a valid CLI invocation."""

    args = build_fx_download_parser().parse_args(
        [
            "--start-date",
            "2018-01-01",
            "--end-date",
            "2024-12-31",
            "--output-csv",
            "data/fx/hkdcny_2018_2024.csv",
        ]
    )

    assert args.start_date == "2018-01-01"
    assert args.end_date == "2024-12-31"
    assert args.output_csv == "data/fx/hkdcny_2018_2024.csv"
