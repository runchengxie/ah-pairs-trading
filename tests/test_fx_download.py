"""Tests for the standalone FX downloader."""

from __future__ import annotations

import io
import json

import pandas as pd

from ah_pairs_trading import fx_download


class _DummyResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False


def test_build_frankfurter_timeseries_url_uses_requested_window() -> None:
    """The downloader should encode the requested date range and currencies."""

    url = fx_download.build_frankfurter_timeseries_url("2024-01-01", "2024-01-10")

    assert url.startswith("https://api.frankfurter.dev/v1/2024-01-01..2024-01-10?")
    assert "base=EUR" in url
    assert "symbols=CNY%2CHKD" in url


def test_download_hkdcny_history_crosses_eur_reference_rates(monkeypatch) -> None:
    """HKD/CNY should be derived as EUR/CNY divided by EUR/HKD."""

    payload = {
        "amount": 1.0,
        "base": "EUR",
        "start_date": "2024-01-02",
        "end_date": "2024-01-03",
        "rates": {
            "2024-01-02": {"CNY": 7.8, "HKD": 8.6},
            "2024-01-03": {"CNY": 7.9, "HKD": 8.7},
        },
    }

    def fake_urlopen(request, timeout=30.0):
        return _DummyResponse(json.dumps(payload).encode("utf-8"))

    monkeypatch.setattr("ah_pairs_trading.fx_download.urlopen", fake_urlopen)

    frame, metadata = fx_download.download_hkdcny_history("2024-01-02", "2024-01-03")

    assert frame.index.equals(pd.to_datetime(["2024-01-02", "2024-01-03"]))
    assert frame.loc["2024-01-02", "fx_rate"] == 7.8 / 8.6
    assert frame.loc["2024-01-03", "eur_cny"] == 7.9
    assert metadata.provider == "Frankfurter (ECB reference rates)"
    assert metadata.resolved_start_date == "2024-01-02"
    assert metadata.resolved_end_date == "2024-01-03"
    assert metadata.observation_count == 2


def test_write_fx_history_csv_preserves_project_compatible_columns(tmp_path) -> None:
    """The CSV writer should keep a `date` column and an `fx_rate` column."""

    frame = pd.DataFrame(
        {
            "fx_rate": [0.91, 0.92],
            "eur_cny": [7.8, 7.9],
            "eur_hkd": [8.57, 8.59],
        },
        index=pd.to_datetime(["2024-01-02", "2024-01-03"]),
    )

    output_path = fx_download.write_fx_history_csv(frame, tmp_path / "hkdcny.csv")
    reloaded = pd.read_csv(output_path)

    assert list(reloaded.columns) == ["date", "fx_rate", "eur_cny", "eur_hkd"]
    assert reloaded.loc[0, "date"] == "2024-01-02"
    assert reloaded.loc[1, "fx_rate"] == 0.92


def test_main_writes_csv_and_prints_summary(monkeypatch, tmp_path, capsys) -> None:
    """The CLI should save a CSV and print a compact summary."""

    payload = {
        "start_date": "2024-01-02",
        "end_date": "2024-01-05",
        "rates": {
            "2024-01-02": {"CNY": 7.8, "HKD": 8.6},
            "2024-01-05": {"CNY": 7.9, "HKD": 8.7},
        },
    }

    def fake_urlopen(request, timeout=30.0):
        return _DummyResponse(json.dumps(payload).encode("utf-8"))

    monkeypatch.setattr("ah_pairs_trading.fx_download.urlopen", fake_urlopen)

    output_path = tmp_path / "fx" / "hkdcny.csv"
    exit_code = fx_download.main(
        [
            "--start-date",
            "2024-01-02",
            "--end-date",
            "2024-01-05",
            "--output-csv",
            str(output_path),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert output_path.exists()
    assert "FX Download Summary" in captured.out
    assert "Frankfurter (ECB reference rates)" in captured.out
    assert str(output_path) in captured.out
