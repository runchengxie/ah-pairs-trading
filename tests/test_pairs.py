"""Tests for same-issuer A/H pair validation."""

from __future__ import annotations

from ah_pairs_trading.pairs import normalize_a_symbol, normalize_h_symbol, validate_same_issuer_pair


def test_pair_registry_accepts_known_same_issuer_pair() -> None:
    """Known same-issuer A/H mappings should validate cleanly."""

    validation = validate_same_issuer_pair("sh601857", "857")

    assert validation.status == "matched"
    assert validation.a_symbol == "601857"
    assert validation.h_symbol == "00857"
    assert validation.issuer_name == "PetroChina"


def test_pair_registry_rejects_known_cross_issuer_pair() -> None:
    """Known cross-company mixes should be flagged as mismatches."""

    validation = validate_same_issuer_pair("601857", "00883")

    assert validation.status == "mismatch"
    assert "PetroChina" in validation.message
    assert "CNOOC" in validation.message


def test_symbol_normalizers_strip_market_prefixes() -> None:
    """Normalization should remove market prefixes and zero-pad H-share tickers."""

    assert normalize_a_symbol("sh600036") == "600036"
    assert normalize_h_symbol("3968") == "03968"
