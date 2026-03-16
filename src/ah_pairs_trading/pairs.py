"""Registry and validation helpers for same-issuer A/H pairs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


PairValidationStatus = Literal["matched", "mismatch", "unknown"]


@dataclass(frozen=True, slots=True)
class PairRegistryEntry:
    """A curated A/H symbol mapping for the same listed issuer."""

    issuer_id: str
    issuer_name: str
    a_symbol: str
    h_symbol: str
    share_ratio: float = 1.0


@dataclass(frozen=True, slots=True)
class PairValidationResult:
    """Outcome of validating an A/H pair against the curated registry."""

    status: PairValidationStatus
    a_symbol: str
    h_symbol: str
    issuer_name: str | None
    message: str

    def as_dict(self) -> dict[str, str | None]:
        return {
            "status": self.status,
            "a_symbol": self.a_symbol,
            "h_symbol": self.h_symbol,
            "issuer_name": self.issuer_name,
            "message": self.message,
        }


_PAIR_REGISTRY: tuple[PairRegistryEntry, ...] = (
    PairRegistryEntry("cmb", "China Merchants Bank", "600036", "03968"),
    PairRegistryEntry("petrochina", "PetroChina", "601857", "00857"),
    PairRegistryEntry("cnooc", "CNOOC", "600938", "00883"),
    PairRegistryEntry("sinopec", "Sinopec", "600028", "00386"),
    PairRegistryEntry("icbc", "ICBC", "601398", "01398"),
    PairRegistryEntry("abc", "ABC", "601288", "01288"),
    PairRegistryEntry("boc", "Bank of China", "601988", "03988"),
    PairRegistryEntry("ccb", "China Construction Bank", "601939", "00939"),
    PairRegistryEntry("bocom", "Bank of Communications", "601328", "03328"),
    PairRegistryEntry("citic", "CITIC Securities", "600030", "06030"),
    PairRegistryEntry("shenhua", "China Shenhua", "601088", "01088"),
)

_A_SHARE_TO_ENTRY = {entry.a_symbol: entry for entry in _PAIR_REGISTRY}
_H_SHARE_TO_ENTRY = {entry.h_symbol: entry for entry in _PAIR_REGISTRY}


def normalize_a_symbol(symbol: str) -> str:
    """Normalize A-share symbols to 6-digit numeric codes."""

    lowered = str(symbol).strip().lower()
    for prefix in ("sh", "sz", "bj"):
        if lowered.startswith(prefix):
            lowered = lowered[len(prefix) :]
            break
    digits = "".join(character for character in lowered if character.isdigit())
    return digits[-6:] if digits else str(symbol).strip()


def normalize_h_symbol(symbol: str) -> str:
    """Normalize H-share symbols to 5-digit Hong Kong ticker codes."""

    digits = "".join(character for character in str(symbol).strip() if character.isdigit())
    return digits.zfill(5) if digits else str(symbol).strip()


def validate_same_issuer_pair(a_symbol: str, h_symbol: str) -> PairValidationResult:
    """Validate an A/H pair against a curated same-issuer registry."""

    normalized_a = normalize_a_symbol(a_symbol)
    normalized_h = normalize_h_symbol(h_symbol)
    a_entry = _A_SHARE_TO_ENTRY.get(normalized_a)
    h_entry = _H_SHARE_TO_ENTRY.get(normalized_h)

    if a_entry is not None and h_entry is not None:
        if a_entry.issuer_id == h_entry.issuer_id:
            return PairValidationResult(
                status="matched",
                a_symbol=normalized_a,
                h_symbol=normalized_h,
                issuer_name=a_entry.issuer_name,
                message=(
                    f"Validated against built-in registry: {normalized_a}/{normalized_h} "
                    f"maps to {a_entry.issuer_name}."
                ),
            )
        return PairValidationResult(
            status="mismatch",
            a_symbol=normalized_a,
            h_symbol=normalized_h,
            issuer_name=None,
            message=(
                "Built-in A/H registry reports a same-issuer mismatch: "
                f"{normalized_a} maps to {a_entry.issuer_name}, while {normalized_h} maps to {h_entry.issuer_name}. "
                "This project assumes the A and H legs belong to the same issuer unless you intentionally opt out."
            ),
        )

    return PairValidationResult(
        status="unknown",
        a_symbol=normalized_a,
        h_symbol=normalized_h,
        issuer_name=a_entry.issuer_name if a_entry is not None else h_entry.issuer_name if h_entry is not None else None,
        message=(
            "The pair is not fully covered by the built-in A/H registry. "
            "No automatic same-issuer verdict was applied."
        ),
    )
