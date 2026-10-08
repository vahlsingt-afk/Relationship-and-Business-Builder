"""
Shared helpers for the Blue Sheet engine (render.py, validate.py, impact_review.py).

RB-2026-08-23: wired into ecosystem_intelligence.py's promote_posture/
promote_confidence -- see _standard/GAP_REPORT_2026-08-21.md item 3 and
CANONICAL_REGISTRY.yaml's blue_sheets.mutation_owner for the intended design.
Initially scoped narrower than planned: a real vendor-name-matching problem
was found during implementation (technology_stack[].vendor is messy free
text, e.g. "ZoneIn and Stream among multiple vendors", that doesn't reliably
match ecosystem_intelligence.json's clean product/vendor_role fields, and
technology_stack[].status uses a different vocabulary than
impact_review.ProposedChange expects). Auto-applying a field-level change on
an unreliable name match risked writing wrong data into a real account
document, so the 2026-08-23 hook only detected and logged coverage.

RB-2026-08-24: added a narrow, high-precision-only field-level sync on top
of that (match_technology_stack_row / CATEGORY_TO_LAYER_KEYWORDS /
POSTURE_TO_STATUS below) -- only fires when a relationship's category maps
to exactly one technology_stack[] row (never guesses between candidates:
e.g. "payments" and "pos" both match multiple rows in Pollo Campero's real
data and correctly resolve to no-match), and only ever writes the row's
`status` field via the same governance-gated impact_review.process_event()
path everything else uses. Everything that doesn't match this narrow case
still falls through to coverage-log-only, exactly as before. See
coverage_log.jsonl and entity_id_to_slug/log_coverage_event below.
"""
from __future__ import annotations

import json
import datetime
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # .../blue_sheets -- still the
# home for everything that deliberately did NOT move in the 3-store
# unification (RB-2026-09-06/07): the xlsx template (_standard/), coverage_
# log.jsonl, review_queue.json, active_accounts.json all stay physically
# parked here per the approved plan ("Blue Sheet's versions carried forward
# as the one copy") -- only account_dir()/registry_path() below move to the
# new unified store, since those are the two things every real account's
# data itself needs to live in one place with Account Research.
CUSTOMERS_PROSPECTS_ROOT = ROOT.parent / "customers_prospects"

sys.path.insert(0, str(ROOT.parent / "system" / "scripts"))
import slug_safety  # noqa: E402


def account_dir(slug: str) -> Path:
    # RB-SECURITY-2026-09-05: defense-in-depth -- create_account.py's own
    # caller-facing entry point validates slug too, but this is the single
    # choke point every OTHER account-path lookup in this engine goes
    # through, so it gets the same guard rather than depending on every
    # future caller remembering to validate first.
    slug_safety.assert_safe_slug(slug, label="account_slug")
    d = CUSTOMERS_PROSPECTS_ROOT / "accounts" / slug
    if not d.is_dir():
        raise FileNotFoundError(f"No account folder for slug '{slug}' at {d}")
    return d


def load_json(path: Path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data) -> None:
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


def load_account(slug: str):
    d = account_dir(slug)
    actions_path = d / "actions.json"
    return {
        "account": load_json(d / "account.json"),
        "brand_profile": load_json(d / "brand_profile.json"),
        # actions.json is graduated-depth data, not part of the minimum
        # account shape.  A researched account with no recorded actions is
        # valid and must remain readable through getAccountStatus.
        "actions": (
            load_json(actions_path)
            if actions_path.exists()
            else {"account_id": f"acct-{slug}", "parent_rbb_loop_id": None, "actions": []}
        ),
        "contradictions": load_json(d / "contradictions.json"),
        "evidence": load_jsonl(d / "evidence.jsonl"),
        "source_index": load_json(d / "source_index.json"),
    }


def registry_path() -> Path:
    return CUSTOMERS_PROSPECTS_ROOT / "_portfolio" / "customers_prospects_registry.json"


def load_registry():
    return load_json(registry_path())


def is_activated(slug_or_account_id: str) -> bool:
    """A Blue Sheet is 'activated' only if the registry names a real workbook_path."""
    reg = load_registry()
    for entry in reg["registry"]:
        if entry["account_id"] in (slug_or_account_id, f"acct-{slug_or_account_id}"):
            return entry.get("workbook_path") is not None
    return False


def engagement_tier(slug_or_account_id: str) -> str | None:
    """The registry's engagement_tier for this account, or None if not found.

    RB defect 2026-09-30 (Five Guys): workbook_path alone can't tell apart
    an ordinary pre-engagement brand (no Blue Sheet expected) from an active
    engagement that is missing one (a real gap) -- callers that gate on
    is_activated() need this to tell the two apart."""
    reg = load_registry()
    for entry in reg["registry"]:
        if entry["account_id"] in (slug_or_account_id, f"acct-{slug_or_account_id}"):
            return entry.get("engagement_tier")
    return None


def entity_id_to_slug(entity_id: str) -> str | None:
    """Crosswalk an ecosystem_intelligence.json entity_id (e.g. 'brand-pollo-campero')
    to a Blue Sheet account slug (e.g. 'pollo-campero'). Returns None for entity
    types that were never brand-prefixed (e.g. 'vendor-...') -- Blue Sheets exist
    for restaurant-brand accounts, not vendor entities."""
    if not entity_id or not entity_id.startswith("brand-"):
        return None
    return entity_id[len("brand-"):]


COVERAGE_LOG_PATH_NAME = "coverage_log.jsonl"


def coverage_log_path() -> Path:
    return ROOT / "_portfolio" / COVERAGE_LOG_PATH_NAME


def log_coverage_event(*, entity_id: str, slug: str | None, activated: bool,
                        mutation_type: str, detail: str) -> None:
    """RB-2026-08-23: record every canonical mutation that touched an
    entity a Blue Sheet could exist for -- whether or not one actually
    does, and whether or not this hook attempted a field-level update.
    This is the raw input a future P0-1-style acceptance check would read
    to answer "is any active, qualified opportunity quietly missing Blue
    Sheet discipline" without a human having to go looking."""
    append_jsonl(coverage_log_path(), {
        "logged_at": now_iso(),
        "entity_id": entity_id,
        "slug": slug,
        "activated": activated,
        "mutation_type": mutation_type,
        "detail": detail,
    })


def now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def today() -> str:
    return datetime.date.today().isoformat()


def next_evidence_id(slug: str, evidence: list) -> str:
    existing = [e["evidence_id"] for e in evidence if e["evidence_id"].startswith(f"ev-{slug}-")]
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


# RB-2026-08-24: the narrow, match-gated field-level sync that RB-2026-08-23's
# coverage-log-only hook deferred. Deliberately a small, explicit keyword map,
# not an attempt at full category coverage -- categories with no confident
# single-row match (kiosks, franchise_management, inventory, labor_workforce,
# etc.) are left out on purpose and correctly fall through to "no match"
# (coverage-log only) in the caller. Verified against Pollo Campero's real
# technology_stack: "pos" and "payments" both match MORE than one row there
# (POS software + POS hardware; payments gateway + payment terminal) -- the
# matcher counts actual candidate rows rather than trusting the dict alone,
# so both correctly resolve to "no match" rather than guessing.
CATEGORY_TO_LAYER_KEYWORDS: dict = {
    "pos": ["pos"],
    "online_ordering": ["online ordering"],
    "loyalty": ["loyalty"],
    "digital_menu_boards": ["dmb", "menu board"],
    "payments": ["payment"],
    "bi_analytics": ["analytics"],
    "back_office_accounting": ["back office"],
    "back_office_operations": ["back office"],
}

# Never maps to "Active" or "Reconcile" -- those carry operational meaning
# beyond simple evidence confidence and are left for human judgment, not
# something an ecosystem-graph posture promotion should assert on its own.
POSTURE_TO_STATUS: dict = {
    "provisional": "Verify",
    "partially_substantiated": "Verify",
    "substantiated": "Confirmed",
}


def match_technology_stack_row(account: dict, category: str) -> int | None:
    """Return the index of the ONE technology_stack[] row an ecosystem
    relationship's `category` unambiguously corresponds to, or None if the
    category is unmapped or more than one row plausibly matches. Never
    guesses between candidates -- see the module-level comment above for a
    real example (payments) where this matters."""
    keywords = CATEGORY_TO_LAYER_KEYWORDS.get((category or "").lower())
    if not keywords:
        return None
    rows = account.get("technology_stack") or []
    matches = [
        i for i, row in enumerate(rows)
        if any(kw in (row.get("layer") or "").lower() for kw in keywords)
    ]
    return matches[0] if len(matches) == 1 else None
