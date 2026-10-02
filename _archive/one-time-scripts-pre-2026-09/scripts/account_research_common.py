"""
account_research_common.py — storage primitives for Account Research /
Account Background Brief.

2026-08-27, corrected same day: this was originally built as a sub-feature
of blue_sheets/ (account_background_brief.py wrote into
blue_sheets/accounts/<slug>/). Todd corrected that: an Account Background
Brief is a *pre-engagement* document -- digging into a brand from public
information and RBB's own account research, to prepare for discovery. A
Blue Sheet is the plan used *during* an active engagement. The brief is
upstream of the Blue Sheet (can feed facts into one once an engagement
starts) and must never require a Blue Sheet to already exist.

This module is therefore deliberately independent of
blue_sheets/_engine/common.py -- not a re-import, not a shared ROOT. Same
small set of JSON/JSONL primitives (duplicated, not generalized, because
these are two real, separately-governed systems now: Blue Sheets are
created only on Todd's explicit direction, Account Research entries are
meant to be cheap to start -- see account_dir(create=True) below).
"""
from __future__ import annotations

import json
import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "account_research"  # .../system/account_research


def account_dir(slug: str, *, create: bool = False) -> Path:
    d = ROOT / "accounts" / slug
    if not d.is_dir():
        if create:
            d.mkdir(parents=True, exist_ok=True)
            return d
        raise FileNotFoundError(f"No account research folder for slug '{slug}' at {d}")
    return d


def load_json(path: Path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def load_jsonl(path: Path):
    if not path.exists():
        return []
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def append_jsonl(path: Path, record) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False))
        f.write("\n")


def _empty_account_json(slug: str, display_name: str) -> dict:
    return {
        "account_id": f"acct-{slug}", "account_slug": slug,
        "display_name": display_name, "aliases": [],
        "portfolio_status": {
            "value": "pre-engagement research — not yet a confirmed active opportunity",
            "status": "confirmed", "evidence_ids": [],
            "confidence": "high", "as_of": today(), "scope": "account",
            "last_reviewed_by": "system:account_research_common",
        },
        "owners": [], "executive_summary": "", "current_business_situation": "",
        "leadership": {"confirmed": [], "reported_unverified": []},
        "technology_stack": [], "commercial_models": [], "buying_influences": [],
        "opportunities": [], "qualification": {"criteria": []}, "strategic_position": {},
        "bottom_line": "", "latest_review": {},
        "template_version": "account-research-v1", "updated_at": now_iso(),
        "blue_sheet_slug": None,
    }


def create_account_shell(slug: str, display_name: str) -> Path:
    """Creates a brand-new, empty Account Research record. Deliberately
    lightweight compared to Blue Sheet creation -- no explicit-authorization
    gate -- because this is meant to be the cheap, pre-engagement starting
    point ('dig into a brand before discovery'), not a qualified pursuit."""
    d = account_dir(slug, create=True)
    save_json(d / "account.json", _empty_account_json(slug, display_name))
    save_json(d / "brand_profile.json", {"account_id": f"acct-{slug}"})
    save_json(d / "contradictions.json", {"account_id": f"acct-{slug}", "contradictions": []})
    save_json(d / "source_index.json", {"account_id": f"acct-{slug}", "sources": []})
    (d / "evidence.jsonl").touch()
    return d


def load_account(slug: str):
    d = account_dir(slug)
    return {
        "account": load_json(d / "account.json"),
        "brand_profile": load_json(d / "brand_profile.json"),
        "contradictions": load_json(d / "contradictions.json"),
        "evidence": load_jsonl(d / "evidence.jsonl"),
        "source_index": load_json(d / "source_index.json"),
    }


def registry_path() -> Path:
    return ROOT / "_portfolio" / "account_research_registry.json"


def load_registry():
    p = registry_path()
    if not p.exists():
        return {"registry": []}
    return load_json(p)


def save_registry(reg: dict) -> None:
    save_json(registry_path(), reg)


def now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def today() -> str:
    return datetime.date.today().isoformat()


REFRESH_FLAGS_PATH = ROOT.parent / ".cache" / "account_research_refresh_flags.json"


def load_refresh_flags() -> dict:
    """{slug: {first_flagged_at, reason, status: pending|processed,
    processed_at}} -- the 24h-SLA state for intelligence_cascade.py's
    "flag today, auto-process if still open on the next daily run" rule
    (2026-08-27, per Todd's explicit direction). Lives here, not in
    intelligence_cascade.py or account_background_brief.py, specifically
    to avoid a circular import: both of those modules need to read/write
    this state, and account_background_brief.py already imports this
    module for everything else."""
    if not REFRESH_FLAGS_PATH.exists():
        return {}
    try:
        return load_json(REFRESH_FLAGS_PATH)
    except Exception:  # noqa: BLE001
        return {}


def save_refresh_flags(flags: dict) -> None:
    save_json(REFRESH_FLAGS_PATH, flags)


def flag_needs_refresh(slug: str, reason: str) -> bool:
    """Idempotent: only creates a new pending flag if the slug has none
    already pending. Returns True if a new flag was created."""
    flags = load_refresh_flags()
    existing = flags.get(slug)
    if existing and existing.get("status") == "pending":
        return False
    flags[slug] = {"first_flagged_at": today(), "reason": reason, "status": "pending", "processed_at": None}
    save_refresh_flags(flags)
    return True


def clear_refresh_flag(slug: str) -> None:
    """Called whenever a brief is actually (re)generated for any reason —
    a Todd-initiated regeneration must stop the next cascade run from
    redundantly re-processing an already-resolved flag."""
    flags = load_refresh_flags()
    if slug in flags:
        del flags[slug]
        save_refresh_flags(flags)


def next_evidence_id(slug: str, evidence: list) -> str:
    existing = [e["evidence_id"] for e in evidence if e.get("evidence_id", "").startswith(f"ev-{slug}-")]
    n = 0
    for eid in existing:
        try:
            n = max(n, int(eid.rsplit("-", 1)[-1]))
        except ValueError:
            pass
    return f"ev-{slug}-{n+1:04d}"
