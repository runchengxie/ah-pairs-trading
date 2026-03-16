"""Lightweight disk-backed cache helpers for market data and pipeline stages."""

from __future__ import annotations

import hashlib
import json
import os
import pickle
from dataclasses import asdict, is_dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Callable, TypeVar

import pandas as pd

T = TypeVar("T")


def _normalize_for_hash(value: Any) -> Any:
    if is_dataclass(value):
        return _normalize_for_hash(asdict(value))
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _normalize_for_hash(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))}
    if isinstance(value, (list, tuple)):
        return [_normalize_for_hash(item) for item in value]
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return value


def build_cache_key(payload: Any) -> str:
    normalized = _normalize_for_hash(payload)
    encoded = json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def file_signature(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    resolved = Path(path)
    if not resolved.exists():
        return {"path": str(resolved), "exists": False}
    stat = resolved.stat()
    return {
        "path": str(resolved),
        "exists": True,
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


def frame_digest(frame: pd.DataFrame | pd.Series) -> str:
    if isinstance(frame, pd.Series):
        hashed = pd.util.hash_pandas_object(frame, index=True).to_numpy()
        metadata = {"name": frame.name, "dtype": str(frame.dtype)}
    else:
        hashed = pd.util.hash_pandas_object(frame, index=True).to_numpy()
        metadata = {
            "columns": [str(column) for column in frame.columns],
            "dtypes": {str(column): str(dtype) for column, dtype in frame.dtypes.items()},
        }

    digest = hashlib.sha256()
    digest.update(hashed.tobytes())
    digest.update(json.dumps(metadata, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8"))
    return digest.hexdigest()


def atomic_pickle_dump(value: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: str | None = None
    try:
        with NamedTemporaryFile(dir=path.parent, delete=False) as handle:
            pickle.dump(value, handle, protocol=pickle.HIGHEST_PROTOCOL)
            temp_path = handle.name
        os.replace(temp_path, path)
    finally:
        if temp_path is not None and os.path.exists(temp_path):
            os.unlink(temp_path)


def atomic_json_dump(payload: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: str | None = None
    try:
        with NamedTemporaryFile(dir=path.parent, delete=False, mode="w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True, ensure_ascii=False)
            temp_path = handle.name
        os.replace(temp_path, path)
    finally:
        if temp_path is not None and os.path.exists(temp_path):
            os.unlink(temp_path)


def load_pickle(path: Path) -> Any:
    with path.open("rb") as handle:
        return pickle.load(handle)


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


class StageCache:
    """Disk-backed stage cache that can resume deterministic pipeline steps."""

    def __init__(self, cache_dir: Path | None, *, refresh: bool = False) -> None:
        self.cache_dir = None if cache_dir is None else Path(cache_dir) / "pipeline"
        self.refresh = refresh

    @property
    def enabled(self) -> bool:
        return self.cache_dir is not None

    def load_or_compute(self, stage_name: str, payload: Any, compute: Callable[[], T]) -> T:
        if not self.enabled:
            return compute()

        cache_key = build_cache_key(payload)
        artifact_path = self.cache_dir / stage_name / f"{cache_key}.pkl"
        metadata_path = artifact_path.with_suffix(".json")
        if artifact_path.exists() and not self.refresh:
            return load_pickle(artifact_path)

        value = compute()
        atomic_pickle_dump(value, artifact_path)
        atomic_json_dump({"stage": stage_name, "payload": _normalize_for_hash(payload)}, metadata_path)
        return value
