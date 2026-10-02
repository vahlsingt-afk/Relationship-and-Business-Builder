"""
customers_prospects_common.py — storage primitives for the unified
Customers & Prospects store (Phase 2 of the 3-store unification plan,
approved 2026-09-06).

Replaces two separately-governed systems that had ~85%-duplicate schemas
and had already diverged for real accounts (system/account_research/ —
pre-engagement, cheap/ungated; blue_sheets/ — active engagement, gated).
One record per brand now; `engagement_tier` on account.json
("pre_engagement" | "active_engagement") replaces "which directory is
this file in" as the lifecycle signal. Creating a pre_engagement record
stays cheap/ungated (Account Research's philosophy); promoting to
active_engagement requires the same explicit-authorization discipline
Blue Sheet activation always had (see promote_to_active_engagement below).

Old account_research_common.py / blue_sheets/_engine/common.py stay in
place, un-migrated, until every real consumer is repointed at this module
(see the approved plan's sequencing) — this module and its migration
script write only into this new customers_prospects/ tree; nothing here
touches either predecessor system's live files.
"""
from __future__ import annotations

import json
import datetime
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import slug_safety  # noqa: E402 -- same defense-in-depth blue_sheets/_engine/common.py applies

ROOT = Path(__file__).resolve().parent.parent.parent / "customers_prospects"  # repo_root/customers_prospects


def account_dir(slug: str, *, create: bool = False) -> Path:
    slug_safety.assert_safe_slug(slug, label="account_slug")
    d = ROOT / "accounts" / slug
    if not d.is_dir():
        if create:
            d.mkdir(parents=True, exist_ok=True)
            return d
        raise FileNotFoundError(f"No customers_prospects account folder for slug '{slug}' at {d}")
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


def now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def today() -> str:
    return datetime.date.today().isoformat()


# Files every record has regardless of engagement_tier (Layer 2's minimum
# shape -- mirrors what both predecessor systems always wrote for every
# real account checked).
_CORE_JSON_FILES = ("account.json", "brand_profile.json", "contradictions.json", "source_index.json")

# Files that exist only once real content has accumulated -- absence is
# normal, not an error (graduated depth is the whole point of Layer 2;
# confirmed against real data that not even every active-engagement
# account has all of these, e.g. mcdonalds' Blue Sheet has no
# discovery_questions.json equivalent).
_OPTIONAL_JSON_FILES = ("actions.json", "opportunity_hypotheses.json", "discovery_questions.json")
_OPTIONAL_JSONL_FILES = ("evidence.jsonl", "customer_facing_artifacts.jsonl")


def load_account(slug: str) -> dict:
    d = account_dir(slug)
    out = {}
    for name in _CORE_JSON_FILES:
        key = name[: -len(".json")]
        p = d / name
        out[key] = load_json(p) if p.exists() else None
    for name in _OPTIONAL_JSON_FILES:
        p = d / name
        if p.exists():
            out[name[: -len(".json")]] = load_json(p)
    for name in _OPTIONAL_JSONL_FILES:
        p = d / name
        out[name[: -len(".jsonl")]] = load_jsonl(p)
    return out


def registry_path() -> Path:
    return ROOT / "_portfolio" / "customers_prospects_registry.json"


def load_registry() -> dict:
    p = registry_path()
    if not p.exists():
        return {"registry": []}
    return load_json(p)


def save_registry(reg: dict) -> None:
    save_json(registry_path(), reg)


def _empty_account_json(slug: str, display_name: str) -> dict:
    return {
        "account_id": f"acct-{slug}", "account_slug": slug,
        "display_name": display_name, "aliases": [],
        "engagement_tier": "pre_engagement",
        "portfolio_status": {
            "value": "pre-engagement research — not yet a confirmed active opportunity",
            "status": "confirmed", "evidence_ids": [],
            "confidence": "high", "as_of": today(), "scope": "account",
            "last_reviewed_by": "system:customers_prospects_common",
        },
        "owners": [], "executive_summary": "", "current_business_situation": "",
        "leadership": {"confirmed": [], "reported_unverified": []},
        "technology_stack": [], "commercial_models": [], "buying_influences": [],
        "opportunities": [], "qualification": {"criteria": []}, "strategic_position": {},
        "bottom_line": "", "latest_review": {},
        "template_version": "customers-prospects-v1", "updated_at": now_iso(),
    }


def create_pre_engagement_shell(slug: str, display_name: str) -> Path:
    """Cheap, ungated creation -- Account Research's philosophy, no
    explicit-authorization gate. Mirrors
    account_research_common.create_account_shell()."""
    d = account_dir(slug, create=True)
    save_json(d / "account.json", _empty_account_json(slug, display_name))
    save_json(d / "brand_profile.json", {"account_id": f"acct-{slug}"})
    save_json(d / "contradictions.json", {"account_id": f"acct-{slug}", "contradictions": []})
    save_json(d / "source_index.json", {"account_id": f"acct-{slug}", "sources": []})
    (d / "evidence.jsonl").touch()
    return d


def promote_to_active_engagement(slug: str, *, authorized_by: str, note: str) -> dict:
    """The gated transition -- requires the same explicit-authorization
    discipline Blue Sheet activation always enforced (a named authorizer
    + a real reason), never silent or automatic. Only flips account.json's
    engagement_tier; does not fabricate xlsx rendering or any new
    content -- that happens through the normal render/evidence pipeline
    afterward."""
    d = account_dir(slug)
    account = load_json(d / "account.json")
    account["engagement_tier"] = "active_engagement"
    account["updated_at"] = now_iso()
    save_json(d / "account.json", account)
    return {
        "slug": slug, "engagement_tier": "active_engagement",
        "authorized_by": authorized_by, "note": note, "promoted_at": now_iso(),
    }


def next_evidence_id(slug: str, evidence: list) -> str:
    existing = [e["evidence_id"] for e in evidence if e.get("evidence_id", "").startswith(f"ev-{slug}-")]
    n = 0
    for eid in existing:
        try:
            n = max(n, int(eid.rsplit("-", 1)[-1]))
        except ValueError:
            pass
    return f"ev-{slug}-{n+1:04d}"


def next_change_id(slug: str, change_log: list) -> str:
    n = 0
    for c in change_log:
        try:
            n = max(n, int(c["change_id"].rsplit("-", 1)[-1]))
        except (ValueError, KeyError):
            pass
    return f"chg-{slug}-{n+1:04d}"


REFRESH_FLAGS_PATH = ROOT.parent / "system" / ".cache" / "customers_prospects_refresh_flags.json"


def load_refresh_flags() -> dict:
    """{slug: {first_flagged_at, reason, status: pending|processed,
    processed_at}} -- carries forward account_research_common.py's 24h-SLA
    refresh-flag state (per Todd's explicit 2026-08-27 direction: "flag
    today, auto-process if still open on the next daily run")."""
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
    flags = load_refresh_flags()
    if slug in flags:
        del flags[slug]
        save_refresh_flags(flags)
