#!/usr/bin/env python3
"""Backfill ecosystem entity aliases and top-level ticker identities."""
from __future__ import annotations

import argparse
import json
import re
from datetime import date
from pathlib import Path

import yaml


SYSTEM_DIR = Path(__file__).resolve().parent.parent
ECOSYSTEM_PATH = SYSTEM_DIR / "ecosystem_intelligence.json"
EARNINGS_PATH = SYSTEM_DIR / "earnings_calendar.yaml"

# Confirmed private/delisted overrides take precedence over stale monitoring data.
PRIVATE_TICKER_OVERRIDES = {"Olo"}

CURATED_ALIASES: dict[str, list[str]] = {
    "PAR Technology": ["PAR", "ParTech", "Brink POS", "PAR Brink"],
    "PAR Punchh": ["Punchh", "PAR Punchh"],
    "NCR": ["NCR Corporation", "NCR Voyix", "NCR Aloha", "Aloha POS"],
    "Radiant Systems": ["Radiant", "NCR Radiant"],
    "Oracle": ["Oracle MICROS", "MICROS"],
    "HP": ["Hewlett-Packard", "Hewlett Packard"],
    "Qu": ["Qu POS"],
    "Restaurant365": ["R365", "Restaurant 365"],
    "Crunchtime": ["CrunchTime", "CrunchTime!"],
    "McDonald's": ["McDonalds", "McDonald's Corporation"],
    "Freddy's Frozen Custard & Steakburgers": [
        "Freddys Frozen Custard and Steakburgers",
        "Freddy's Frozen Custard",
        "Freddy's",
    ],
}

# Public-parent identity is intentionally limited to unambiguous owned brands.
PARENT_TICKERS: dict[str, str] = {
    "KFC": "YUM",
    "Pizza Hut": "YUM",
    "Taco Bell": "YUM",
    "The Habit Burger Grill": "YUM",
    "Burger King": "QSR",
    "Popeyes Louisiana Kitchen": "QSR",
    "Tim Hortons": "QSR",
    "Firehouse Subs": "QSR",
    "Olive Garden": "DRI",
    "LongHorn Steakhouse": "DRI",
    "Cheddar's Scratch Kitchen": "DRI",
    "Yard House": "DRI",
    "The Capital Grille": "DRI",
    "Seasons 52": "DRI",
    "Eddie V's": "DRI",
    "Bahama Breeze": "DRI",
    "Ruth's Chris Steak House": "DRI",
    "Applebee's": "DIN",
    "IHOP": "DIN",
    "NCR": "VYX",
    "Radiant Systems": "VYX",
    "PAR Punchh": "PAR",
}


def generated_aliases(name: str) -> list[str]:
    """Generate conservative punctuation, spacing, and leading-article variants."""
    candidates: list[str] = []
    ascii_apostrophe = name.replace("’", "'").replace("`", "'")
    if ascii_apostrophe != name:
        candidates.append(ascii_apostrophe)
    if "'" in ascii_apostrophe:
        candidates.append(ascii_apostrophe.replace("'", ""))
    if "&" in name:
        candidates.append(name.replace("&", "and"))
    punctuation_flat = re.sub(r"[^\w\s-]", "", ascii_apostrophe)
    punctuation_flat = re.sub(r"\s+", " ", punctuation_flat).strip()
    if punctuation_flat:
        candidates.append(punctuation_flat)
    if name.endswith(", The"):
        candidates.append(f"The {name[:-5]}")
    if name.startswith("The ") and len(name) > 4:
        candidates.append(name[4:])
    return candidates


def load_configured_tickers() -> dict[str, str]:
    data = yaml.safe_load(EARNINGS_PATH.read_text(encoding="utf-8")) or {}
    return {
        str(company.get("name") or "").strip(): str(company.get("ticker") or "").strip()
        for company in data.get("companies") or []
        if company.get("name") and company.get("ticker")
    }


def backfill(graph: dict) -> dict:
    configured_tickers = load_configured_tickers()
    alias_entities = 0
    alias_count = 0
    ticker_count = 0

    for entity in graph.get("entities") or []:
        name = str(entity.get("name") or "").strip()
        aliases = list(entity.get("aliases") or [])
        seen = {name.casefold(), *(str(alias).casefold() for alias in aliases)}
        for alias in [*generated_aliases(name), *CURATED_ALIASES.get(name, [])]:
            alias = alias.strip()
            if alias and alias.casefold() not in seen:
                aliases.append(alias)
                seen.add(alias.casefold())
        entity["aliases"] = aliases
        if aliases:
            alias_entities += 1
            alias_count += len(aliases)

        ticker = None
        if name not in PRIVATE_TICKER_OVERRIDES:
            ticker = configured_tickers.get(name) or PARENT_TICKERS.get(name)
        entity["ticker"] = ticker
        if ticker:
            ticker_count += 1

    graph["last_updated"] = date.today().isoformat()
    return {
        "entities": len(graph.get("entities") or []),
        "entities_with_aliases": alias_entities,
        "aliases": alias_count,
        "entities_with_ticker": ticker_count,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    graph = json.loads(ECOSYSTEM_PATH.read_text(encoding="utf-8"))
    stats = backfill(graph)
    if args.write:
        ECOSYSTEM_PATH.write_text(
            json.dumps(graph, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    print(json.dumps({**stats, "written": args.write}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
