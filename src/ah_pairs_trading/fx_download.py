"""Download HKD/CNY FX history from Frankfurter's ECB-backed reference rates."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd

_DEFAULT_API_BASE_URL = "https://api.frankfurter.dev/v1"
_DEFAULT_TIMEOUT_SECONDS = 30.0
_DEFAULT_SYMBOLS = ("CNY", "HKD")


@dataclass(slots=True)
class FxDownloadMetadata:
    """Human-readable metadata for a downloaded FX history."""

    provider: str
    requested_start_date: str
    requested_end_date: str
    resolved_start_date: str
    resolved_end_date: str
    observation_count: int
    source_url: str


def _normalize_iso_date(value: str) -> str:
    return pd.Timestamp(value).strftime("%Y-%m-%d")


def build_frankfurter_timeseries_url(
    start_date: str,
    end_date: str,
    *,
    api_base_url: str = _DEFAULT_API_BASE_URL,
    base: str = "EUR",
    symbols: tuple[str, ...] = _DEFAULT_SYMBOLS,
) -> str:
    """Build a Frankfurter time-series URL for the requested date window."""

    normalized_start_date = _normalize_iso_date(start_date)
    normalized_end_date = _normalize_iso_date(end_date)
    if normalized_end_date < normalized_start_date:
        raise ValueError("The FX download end date must be on or after the start date.")

    query = urlencode({"base": base, "symbols": ",".join(symbols)})
    return f"{api_base_url.rstrip('/')}/{normalized_start_date}..{normalized_end_date}?{query}"


def _load_json(url: str, *, timeout_seconds: float) -> dict[str, object]:
    request = Request(url, headers={"User-Agent": "ah-pairs-trading/0.1"})
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            payload = json.load(response)
    except HTTPError as exc:
        error_payload = exc.read().decode("utf-8", errors="replace").strip()
        raise RuntimeError(
            f"Frankfurter request failed with HTTP {exc.code} for `{url}`. Response: {error_payload or '<empty>'}"
        ) from exc
    except URLError as exc:
        raise RuntimeError(f"Frankfurter request failed for `{url}`: {exc.reason}") from exc

    if not isinstance(payload, dict):
        raise RuntimeError("Frankfurter returned a non-object JSON payload.")
    return payload


def build_hkdcny_frame(payload: dict[str, object]) -> pd.DataFrame:
    """Convert Frankfurter EUR reference rates into an HKD/CNY series."""

    raw_rates = payload.get("rates")
    if not isinstance(raw_rates, dict) or not raw_rates:
        raise ValueError("Frankfurter returned no daily FX rates for the requested window.")

    records: list[dict[str, float | str]] = []
    for date_str, day_rates in raw_rates.items():
        if not isinstance(day_rates, dict):
            raise ValueError(f"Frankfurter returned an invalid daily rates payload for `{date_str}`.")
        if "CNY" not in day_rates or "HKD" not in day_rates:
            raise ValueError(f"Frankfurter daily payload for `{date_str}` is missing `CNY` or `HKD`.")

        eur_cny = float(day_rates["CNY"])
        eur_hkd = float(day_rates["HKD"])
        if eur_hkd == 0.0:
            raise ValueError(f"Frankfurter daily payload for `{date_str}` returned `HKD=0`, cannot cross FX.")

        records.append(
            {
                "date": _normalize_iso_date(date_str),
                "fx_rate": eur_cny / eur_hkd,
                "eur_cny": eur_cny,
                "eur_hkd": eur_hkd,
            }
        )

    frame = pd.DataFrame.from_records(records)
    frame["date"] = pd.to_datetime(frame["date"])
    frame = frame.sort_values("date").set_index("date")
    frame.index = frame.index.tz_localize(None)
    return frame[["fx_rate", "eur_cny", "eur_hkd"]]


def download_hkdcny_history(
    start_date: str,
    end_date: str,
    *,
    api_base_url: str = _DEFAULT_API_BASE_URL,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
) -> tuple[pd.DataFrame, FxDownloadMetadata]:
    """Download HKD/CNY history via Frankfurter's ECB-backed EUR reference rates."""

    normalized_start_date = _normalize_iso_date(start_date)
    normalized_end_date = _normalize_iso_date(end_date)
    source_url = build_frankfurter_timeseries_url(
        normalized_start_date,
        normalized_end_date,
        api_base_url=api_base_url,
    )
    payload = _load_json(source_url, timeout_seconds=timeout_seconds)
    frame = build_hkdcny_frame(payload)
    metadata = FxDownloadMetadata(
        provider="Frankfurter (ECB reference rates)",
        requested_start_date=normalized_start_date,
        requested_end_date=normalized_end_date,
        resolved_start_date=_normalize_iso_date(str(payload.get("start_date", frame.index.min()))),
        resolved_end_date=_normalize_iso_date(str(payload.get("end_date", frame.index.max()))),
        observation_count=len(frame),
        source_url=source_url,
    )
    return frame, metadata


def write_fx_history_csv(frame: pd.DataFrame, path: str | Path) -> Path:
    """Persist an FX history frame atomically in project-compatible CSV format."""

    output_path = Path(path)
    serialized = frame.reset_index().rename(columns={"index": "date"}).copy()
    serialized["date"] = pd.to_datetime(serialized["date"]).dt.strftime("%Y-%m-%d")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: str | None = None
    try:
        with NamedTemporaryFile(dir=output_path.parent, delete=False, mode="w", encoding="utf-8", newline="") as handle:
            serialized.to_csv(handle, index=False)
            temp_path = handle.name
        os.replace(temp_path, output_path)
    finally:
        if temp_path is not None and os.path.exists(temp_path):
            os.unlink(temp_path)
    return output_path


def render_fx_download_summary(metadata: FxDownloadMetadata, *, output_path: str | Path) -> str:
    """Render a compact download summary for the user."""

    output_path = Path(output_path)
    lines = [
        "FX Download Summary",
        f"- Provider: {metadata.provider}",
        f"- Requested window: {metadata.requested_start_date} to {metadata.requested_end_date}",
        f"- Resolved window: {metadata.resolved_start_date} to {metadata.resolved_end_date}",
        f"- Observations: {metadata.observation_count}",
        f"- Output CSV: {output_path}",
        f"- Source URL: {metadata.source_url}",
    ]
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    """Build the standalone FX download CLI parser."""

    parser = argparse.ArgumentParser(
        description="Download HKD/CNY FX history from Frankfurter using ECB-backed EUR reference rates."
    )
    parser.add_argument("--start-date", required=True, help="Requested start date in YYYY-MM-DD format.")
    parser.add_argument("--end-date", required=True, help="Requested end date in YYYY-MM-DD format.")
    parser.add_argument("--output-csv", required=True, help="Path to the output HKD/CNY CSV file.")
    parser.add_argument(
        "--api-base-url",
        default=_DEFAULT_API_BASE_URL,
        help="Frankfurter API base URL. Override only for testing or mirror endpoints.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=_DEFAULT_TIMEOUT_SECONDS,
        help="HTTP timeout in seconds.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the FX download CLI."""

    parser = build_parser()
    args = parser.parse_args(argv)

    frame, metadata = download_hkdcny_history(
        args.start_date,
        args.end_date,
        api_base_url=args.api_base_url,
        timeout_seconds=args.timeout_seconds,
    )
    output_path = write_fx_history_csv(frame, args.output_csv)
    print(render_fx_download_summary(metadata, output_path=output_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
