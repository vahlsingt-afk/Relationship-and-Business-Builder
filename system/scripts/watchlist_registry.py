"""watchlist_registry.py — single source of truth for RB's canonical
"mandatory" watchlist (RB-DEFECT-2026-08-19 + RB-2026-09-05 watchlist
auto-expansion scoping).

Before this, the same ~155-entity list existed as two independent literal
Python lists -- entity_alerts.py's MANDATORY_RESTAURANT_BRANDS/
MANDATORY_RESTAURANT_TECH and daily_brief.py's own copy -- kept in sync by
hand, with nothing enforcing they stayed identical (confirmed still
byte-identical as of 2026-09-05, but that was luck, not a guarantee).
Migrating to one real file matters now specifically because watchlist_
promotion.py needs exactly one place to write a newly-confirmed brand/
vendor into, not two.

system/watchlist_registry.json shape:
  {
    "schema_version": "1.0",
    "last_updated": "YYYY-MM-DD",
    "restaurant_brands": [str, ...],
    "restaurant_tech": {category_key: [str, ...], ...}
  }
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
REGISTRY_PATH = SCRIPTS_DIR.parent / "watchlist_registry.json"


def load_registry() -> dict:
    return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))


def save_registry(registry: dict) -> None:
    registry["last_updated"] = date.today().isoformat()
    REGISTRY_PATH.write_text(
        json.dumps(registry, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def mandatory_restaurant_brands(registry: dict | None = None) -> list[str]:
    registry = registry or load_registry()
    return list(registry["restaurant_brands"])


def restaurant_tech_categories(registry: dict | None = None) -> dict[str, list[str]]:
    registry = registry or load_registry()
    return {k: list(v) for k, v in registry["restaurant_tech"].items()}


def mandatory_restaurant_tech(registry: dict | None = None) -> list[str]:
    """Flattened, de-duplicated (first-category-wins, matching daily_brief.py's
    original build-flat-list logic), preserving category iteration order."""
    registry = registry or load_registry()
    seen: set[str] = set()
    flat: list[str] = []
    for names in registry["restaurant_tech"].values():
        for name in names:
            if name not in seen:
                seen.add(name)
                flat.append(name)
    return flat


def mandatory_all(registry: dict | None = None) -> list[str]:
    registry = registry or load_registry()
    return mandatory_restaurant_brands(registry) + mandatory_restaurant_tech(registry)


def add_restaurant_brand(name: str) -> None:
    """Permanently promotes a brand into the canonical registry. Caller is
    responsible for confirming this is wanted -- this function itself does
    not gate on anything (mirrors every other *_common.py save_json: the
    review-first discipline lives one layer up, in watchlist_promotion.py's
    record_proposal(), not duplicated at every write primitive)."""
    registry = load_registry()
    if name not in registry["restaurant_brands"]:
        registry["restaurant_brands"].append(name)
        save_registry(registry)


def add_restaurant_tech(name: str, *, category: str = "restaurant_tech_other") -> None:
    registry = load_registry()
    if category not in registry["restaurant_tech"]:
        registry["restaurant_tech"][category] = []
    all_tech = mandatory_restaurant_tech(registry)
    if name not in all_tech:
        registry["restaurant_tech"][category].append(name)
        save_registry(registry)
