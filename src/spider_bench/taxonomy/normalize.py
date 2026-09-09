"""Pure name/authorship/rank normalization. No network, no boto3."""
from __future__ import annotations

import re
import unicodedata

_RANK_ALIASES: dict[str, str] = {
    "sp": "species",
    "sp.": "species",
    "species": "species",
    "spec.": "species",
    "subsp": "subspecies",
    "subsp.": "subspecies",
    "ssp": "subspecies",
    "ssp.": "subspecies",
    "subspecies": "subspecies",
    "var": "variety",
    "var.": "variety",
    "variety": "variety",
    "f": "form",
    "f.": "form",
    "form": "form",
    "gen": "genus",
    "gen.": "genus",
    "genus": "genus",
    "fam": "family",
    "fam.": "family",
    "family": "family",
    "ord": "order",
    "ord.": "order",
    "order": "order",
}

_WS = re.compile(r"\s+")


def normalize_name(name: str) -> str:
    """Canonical matching form: NFKC, lowercase, collapsed whitespace.

    Keeps subgenus parentheses content (e.g. 'Pardosa (Acantholycosa)') since
    dropping it would conflate distinct names; matching is case-insensitive.
    """
    if not isinstance(name, str):
        raise TypeError("name must be str")
    text = unicodedata.normalize("NFKC", name).strip().lower()
    text = _WS.sub(" ", text)
    return text


def normalize_authorship(authorship: str | None) -> str | None:
    """Canonical authorship form: NFKC, collapsed inner whitespace, tidy parens.

    Returns None for empty input. Preserves case (authorship is case-sensitive
    by convention) but normalizes spacing: ' ( Clerck , 1757 ) ' -> '(Clerck, 1757)'.
    """
    if authorship is None:
        return None
    if not isinstance(authorship, str):
        raise TypeError("authorship must be str or None")
    text = unicodedata.normalize("NFKC", authorship).strip()
    if not text:
        return None
    text = _WS.sub(" ", text)
    text = re.sub(r"\(\s*", "(", text)
    text = re.sub(r"\s*\)", ")", text)
    text = re.sub(r"\s*,\s*", ", ", text)
    return text


def canonical_rank(rank: str | None) -> str | None:
    """Map rank abbreviations/aliases to a canonical lowercase rank token.

    Returns None for empty input; passes through unknown tokens lowercased.
    """
    if rank is None:
        return None
    if not isinstance(rank, str):
        raise TypeError("rank must be str or None")
    key = rank.strip().lower()
    if not key:
        return None
    return _RANK_ALIASES.get(key, key)
