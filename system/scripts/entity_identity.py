"""Shared entity identity terms for gathering and matching."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable


SYSTEM_DIR = Path(__file__).resolve().parent.parent
ECOSYSTEM_PATH = SYSTEM_DIR / "ecosystem_intelligence.json"

AMBIGUOUS_ALIAS_TERMS = frozenset({
    "ai", "back office", "bar", "burger", "coffee", "digital", "drive thru",
    "food", "hardware", "kitchen", "loyalty", "payments", "pizza", "pos",
    "restaurant", "retail", "software", "technology",
})


def is_safe_identity_term(entity: dict, term: str) -> bool:
    """Return whether *term* is precise enough for automated attribution.

    Canonical names and tickers remain valid. The guard targets imported
    aliases that are generic market/category words; those aliases may help a
    human searcher but cannot independently prove an entity match.
    """
    value = str(term or "").strip()
    if not value:
        return False
    canonical = str(entity.get("name") or "").strip().casefold()
    ticker = str(entity.get("ticker") or "").strip().casefold()
    folded = value.casefold()
    if folded == canonical or (ticker and folded == ticker):
        return True
    return folded not in AMBIGUOUS_ALIAS_TERMS


def identity_terms(entity: dict) -> list[str]:
    """Return canonical name, aliases, and ticker, deduplicated in order."""
    values = [
        entity.get("name"),
        *(entity.get("aliases") or []),
        entity.get("ticker"),
    ]
    terms: list[str] = []
    seen: set[str] = set()
    for value in values:
        term = str(value or "").strip()
        key = term.casefold()
        if term and key not in seen:
            terms.append(term)
            seen.add(key)
    return terms


def attribution_terms(entity: dict) -> list[str]:
    """Identity terms safe for unattended headline/entity attribution."""
    return [term for term in identity_terms(entity) if is_safe_identity_term(entity, term)]


def load_entities(path: Path = ECOSYSTEM_PATH) -> list[dict]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return data.get("entities") or []


def identity_index(entities: Iterable[dict]) -> dict[str, dict]:
    """Index every identity term to its canonical entity."""
    index: dict[str, dict] = {}
    for entity in entities:
        for term in identity_terms(entity):
            index.setdefault(term.casefold(), entity)
    return index


def find_entity(name: str, entities: Iterable[dict]) -> dict | None:
    return identity_index(entities).get(str(name or "").strip().casefold())


def search_queries(entity: dict, *context_terms: str) -> list[str]:
    """Build one quoted query per identity term with optional shared context."""
    context = " ".join(str(term).strip() for term in context_terms if str(term).strip())
    return [
        f'"{term}" {context}'.strip()
        for term in identity_terms(entity)
    ]
