"""
rb_core.py — shared library for RB scripts.

Reads canonical files. Computes derived state. Exposes pure functions so each
script in this folder stays small and any of them can be re-run from the CLI.

Conventions:
    - All paths anchored on the `system/` directory containing this script.
    - Dates are `datetime.date`. Strings serialize as ISO YYYY-MM-DD.
    - Functions return dicts, never write files. Writers live in the per-script files.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

SYSTEM_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = SYSTEM_DIR.parent

BASELINE_PATH = SYSTEM_DIR / "baseline_index.json"
LOOP_LEDGER_PATH = SYSTEM_DIR / "loop_ledger.md"
# RB-DEFECT-074: structured current-state overlay for legacy L- loops. The ledger
# row only has description/target/status columns, so "what is actually the
# current action / who is it waiting on / when is the next checkpoint" had no
# home other than appended prose. Keyed by L- id; written by loop_state.py only.
LOOP_STATE_PATH = SYSTEM_DIR / "loop_state.json"
CIRCLES_DIR = SYSTEM_DIR / "circles"
CARDS_DIR = SYSTEM_DIR / "cards"
BRIEFS_DIR = SYSTEM_DIR / "briefs"
SETTINGS_PATH = SYSTEM_DIR / "settings.json"
ACTIVE_THREADS_PATH = SYSTEM_DIR / "active_threads.yaml"
STRATEGIC_OPERATORS_PATH = SYSTEM_DIR / "strategic_operators.yaml"
ECOSYSTEM_INTELLIGENCE_PATH = SYSTEM_DIR / "ecosystem_intelligence.json"
DOMAIN_PACKS_DIR = SYSTEM_DIR / "domain_packs"
SNAPSHOTS_DIR = SYSTEM_DIR / "_snapshots"
CACHE_DIR = SYSTEM_DIR / ".cache"
# Sprint E-1: SQLite gathered intelligence database
INTELLIGENCE_DB_PATH = CACHE_DIR / "intelligence.db"
# Sprint E-2b: Configurable industry sources (edit to match your industry)
INDUSTRY_SOURCES_PATH = SYSTEM_DIR / "industry_sources.yaml"
INBOX_DIR = SYSTEM_DIR / "inbox"
CONFLICT_QUEUE_PATH = INBOX_DIR / "ecosystem" / "conflict_queue.jsonl"
INBOX_ACCOUNTS_PATH = INBOX_DIR / "accounts.yaml"
SOCIAL_FEED_PATH = INBOX_DIR / "social.feed.json"
SOCIAL_OWN_POSTS_PATH = INBOX_DIR / "social.own_posts.json"
SOCIAL_ENGAGEMENT_PATH = INBOX_DIR / "social.engagement.json"
MESSAGES_PATH = INBOX_DIR / "messages.json"
CALLS_PATH = INBOX_DIR / "calls.json"
SESSIONS_DIR = SYSTEM_DIR / "_sessions"
SESSIONS_INDEX_PATH = SESSIONS_DIR / "index.json"
# Legacy single-account paths — still read if present, for back-compat.
CALENDAR_PATH = INBOX_DIR / "calendar.json"
EMAIL_PATH = INBOX_DIR / "email.json"
EMAIL_SENT_PATH = INBOX_DIR / "email_sent.json"

# RB-2026-09-01: centralized here after being duplicated independently in
# render_intelligence_brief.py (_GP_OWN_TERMS, for GP/Genius relevance
# scoring) and tech_stack_relationship_promotion.py (_OWN_COMPANY_TERMS,
# to exclude Todd's own employer from being mined as a "competitor"
# vendor) -- a third real need (competitive_landscape.py's Genius-family
# market-share rollup) made a third copy worth avoiding. Global Payments'
# own go-to-market brand names -- a customer-win fact about one of these is
# not competitive intelligence, it's Todd's own account, tracked via Blue
# Sheets/Master Account Plans instead.
GP_OWN_TERMS = [
    "global payments", "genius", "worldpay", "heartland", "evo payments",
    "genius pos", "genius platform",
]


def calendar_path_for(account_id: str) -> Path:
    return INBOX_DIR / f"calendar.{account_id}.json"


def email_path_for(account_id: str) -> Path:
    return INBOX_DIR / f"email.{account_id}.json"


def email_sent_path_for(account_id: str) -> Path:
    """Per-account sent-mailbox capture (Step 2b).

    Lives alongside email_path_for to keep inbox and sent fetches
    independent — useful because refreshing one mailbox should not
    clobber the other. load_email reads both and the recency merge
    decides which version of a shared thread wins.
    """
    return INBOX_DIR / f"email_sent.{account_id}.json"

# Default freshness threshold for inbox overlays. Older than this triggers
# a stale-data flag in the brief but does not suppress the section.
INBOX_FRESH_SECONDS = 6 * 60 * 60  # 6 hours

# Sender-domain patterns that are almost always noise — newsletters, job
# alert blasts, no-reply addresses. The email overlay filters these unless
# their snippet looks like a real signal (rare).
NOISE_DOMAIN_PATTERNS = (
    "no-reply", "noreply", "donotreply",
    "jobalerts-", "jobs@", "notifications@", "notification.",
    "newsletter", "mail.beehiiv", "substack.com",
    "marketing@", "marketo", "mailchimp",
)


# -----------------------------------------------------------------------------
# Cache helpers
# -----------------------------------------------------------------------------

def cache_path(name: str) -> Path:
    """Resolve a cache file path; ensure the directory exists."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if not name.endswith(".json"):
        name = name + ".json"
    return CACHE_DIR / name


def write_cache(name: str, payload: dict | list, *, source: str | None = None) -> Path:
    """Write a JSON payload to `system/.cache/<name>.json` with a freshness
    envelope. Subsequent sessions can read this instead of recomputing.

    Envelope shape:
        {
            "_generated_at": "2026-05-15T18:30:00",
            "_source": "daily_brief.py",
            "_baseline_mtime": <epoch of baseline_index.json>,
            "data": <payload>,
        }
    """
    from datetime import datetime
    path = cache_path(name)
    envelope = {
        "_generated_at": datetime.now().isoformat(timespec="seconds"),
        "_source": source or "unknown",
        "_baseline_mtime": int(BASELINE_PATH.stat().st_mtime) if BASELINE_PATH.exists() else None,
        "data": payload,
    }
    # RB-DEFECT-2026-07-20: a direct write_text() left daily_brief.json
    # truncated mid-string (found live: a 5.5MB write cut off partway
    # through, unparseable by every downstream reader) after an interrupted
    # write -- a killed process, a timeout, or a full disk mid-write. Write
    # to a temp file in the same directory and rename, so a reader never
    # observes a partial file: rename is atomic, the old (complete) file
    # stays valid until the new one is fully written.
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(envelope, default=str, indent=2))
    tmp_path.replace(path)
    return path


def read_cache(name: str, *, max_age_seconds: int | None = None) -> dict | list | None:
    """Read a cached payload if fresh. Returns the `data` body, not the envelope.
    Returns None when the cache file is missing, malformed, or stale.

    Freshness rules (in order):
        1. If max_age_seconds is set, reject caches older than that.
        2. If baseline_index.json has been modified since the cache was written,
           reject the cache.
    """
    path = cache_path(name)
    if not path.exists():
        return None
    try:
        env = json.loads(path.read_text())
    except json.JSONDecodeError:
        return None
    if max_age_seconds is not None:
        from datetime import datetime
        try:
            gen = datetime.fromisoformat(env["_generated_at"])
            if (datetime.now() - gen).total_seconds() > max_age_seconds:
                return None
        except (KeyError, ValueError):
            return None
    if BASELINE_PATH.exists():
        bm = int(BASELINE_PATH.stat().st_mtime)
        if env.get("_baseline_mtime") is not None and bm > env["_baseline_mtime"]:
            return None
    return env.get("data")

# Dormancy thresholds (days) by RC tier. Source: ARCHITECTURE.md.
TIER_THRESHOLD_DAYS = {
    "inner": 30,
    "broader": 90,
    "dormant_valuable": 180,
}


# -----------------------------------------------------------------------------
# Loaders
# -----------------------------------------------------------------------------

def load_baseline(path: Path = BASELINE_PATH) -> list[dict]:
    with open(path) as f:
        return json.load(f)


def load_settings(path: Path = SETTINGS_PATH) -> dict:
    with open(path) as f:
        return json.load(f)


def list_card_ids(path: Path = CARDS_DIR) -> set[str]:
    """Return the set of RC IDs that have a card file (excluding _TEMPLATE)."""
    ids = set()
    for p in path.glob("*.md"):
        if p.stem.startswith("_"):
            continue
        ids.add(p.stem)
    return ids


def list_circle_files(path: Path = CIRCLES_DIR) -> list[Path]:
    return [p for p in sorted(path.glob("*.md")) if not p.stem.startswith("_")]


def parse_circle_frontmatter(circle_path: Path) -> dict[str, Any]:
    text = circle_path.read_text()
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    fm = text[3:end].strip().splitlines()
    out: dict[str, Any] = {}
    for line in fm:
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        out[k.strip()] = v.strip()
    return out


def count_briefs(path: Path = BRIEFS_DIR) -> int:
    if not path.exists():
        return 0
    return sum(1 for p in path.glob("*.md"))


# -----------------------------------------------------------------------------
# Loop ledger parser
# -----------------------------------------------------------------------------

LOOP_ROW_RE = re.compile(
    r"^\|\s*(L-\d{4}-\d{2}-\d{2}-\d{3})\s*"
    r"\|\s*(\d{4}-\d{2}-\d{2})\s*"
    r"\|\s*(.+?)\s*"
    r"\|\s*(.+?)\s*"
    r"\|\s*(\d{4}-\d{2}-\d{2})\s*"
    r"\|\s*(.+?)\s*\|\s*$"
)


@dataclass
class Loop:
    id: str
    opened: date
    party: str
    description: str
    target: date
    status_raw: str
    closed: bool
    # RB-DEFECT-074: structured overlay from loop_state.json (None = prose-only loop).
    state: dict | None = None

    @property
    def status_short(self) -> str:
        return "closed" if self.closed else "open"


def load_loop_state(path: Path | None = None) -> dict[str, dict]:
    """Structured state overlay keyed by L- id. Missing/corrupt file -> {}."""
    path = path or LOOP_STATE_PATH
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    loops = raw.get("loops") if isinstance(raw, dict) else None
    return loops if isinstance(loops, dict) else {}


# States where the ball is in someone else's court (or on a scheduled internal
# step) -- a past ledger target date does not make these "overdue chases".
WAITING_STATES = frozenset({
    "waiting", "awaiting_response", "awaiting_internal_review", "waiting_internal",
    "parked", "monitoring",
    # Explicit in-progress work is state-driven too: its stale ledger target
    # date is not an overdue chase (e.g. engineering work under way).
    "in_progress",
})


def parse_loop_ledger(path: Path | None = None) -> list[Loop]:
    # Resolved at call time (not a bound default) so tests/tools can repoint
    # LOOP_LEDGER_PATH; the state overlay only applies to the real ledger.
    is_live = path is None
    path = path or LOOP_LEDGER_PATH
    loops: list[Loop] = []
    overlay = load_loop_state() if is_live else {}
    for line in path.read_text().splitlines():
        m = LOOP_ROW_RE.match(line)
        if not m:
            continue
        loop_id, opened, party, desc, target, status = m.groups()
        closed = "closed" in status.lower() or "abandoned" in status.lower()
        loops.append(Loop(
            id=loop_id,
            opened=date.fromisoformat(opened),
            party=party,
            description=desc,
            target=date.fromisoformat(target),
            status_raw=status,
            closed=closed,
            state=overlay.get(loop_id),
        ))
    return loops


def loops_by_status(loops: Iterable[Loop], today: date) -> dict[str, list[Loop]]:
    """Bucket loops using Friday close of business as the week boundary."""
    out: dict[str, list[Loop]] = {
        "overdue": [],
        "due_today": [],
        "this_week": [],
        "future": [],
        "waiting": [],
        "closed": [],
    }
    # Todd's operating week ends Friday COB. On Saturday/Sunday, "this week"
    # rolls to the coming Friday rather than using Sunday or a rolling 7 days.
    days_to_friday = (4 - today.weekday()) % 7
    week_end = today + timedelta(days=days_to_friday)
    for L in loops:
        if L.closed:
            out["closed"].append(L)
            continue
        st = L.state or {}
        if st.get("state") in WAITING_STATES:
            # RB-DEFECT-074: an explicitly waiting/parked loop is bucketed by its
            # real next checkpoint, not by the stale ledger target date. No
            # checkpoint at all = unknown date, which stays visible as "waiting"
            # instead of being invented into a chase deadline.
            cp = st.get("next_checkpoint")
            try:
                cp_d = date.fromisoformat(cp) if cp else None
            except ValueError:
                cp_d = None
            if cp_d is None or cp_d > today:
                out["waiting"].append(L)
                continue
            if cp_d == today:
                out["due_today"].append(L)
                continue
            out["overdue"].append(L)
            continue
        if L.target < today:
            out["overdue"].append(L)
        elif L.target == today:
            out["due_today"].append(L)
        elif L.target <= week_end:
            out["this_week"].append(L)
        else:
            out["future"].append(L)
    for k in ("overdue", "due_today", "this_week"):
        out[k].sort(key=lambda L: L.target)
    return out


# -----------------------------------------------------------------------------
# EOLMS — Executive Open Loop Management System
#
# Distinct from the per-contact loop_ledger.md above: ELoop tracks strategic
# initiatives, projects, decisions, and waiting conditions with a richer
# lifecycle than the flat open/closed model. See system/design/EOLMS_SPEC.md.
# Writers (add/update/transition/migrate) live in eolms.py — this module only
# loads and computes, per the module convention.
# -----------------------------------------------------------------------------

EOLMS_DIR = SYSTEM_DIR / "eolms"
EOLMS_PATH = EOLMS_DIR / "loops.json"
EOLMS_SCHEMA_PATH = EOLMS_DIR / "loops.schema.json"
EOLMS_ARCHIVE_DIR = EOLMS_DIR / "archive"

ELOOP_CATEGORIES = (
    "strategic_initiative", "project", "action", "decision",
    "waiting", "relationship", "opportunity", "research",
)

# identified/qualified/active/waiting/deferred/blocked/dormant/monitor/pending_verification
# are all non-terminal; completed/archived accept no further transitions.
# pending_verification: a "complete" signal was matched but the loop was flagged
# (requires_verification=True) as needing independent confirmation before it's truly
# done — sits between active work and completed. See EOLMS_SPEC.md's Fixed -> Verified
# -> Closed discipline (RB-DEFECT-060 follow-up, EL-2026-07-02-021).
ELOOP_STATUSES = (
    "identified", "qualified", "active", "waiting", "deferred",
    "blocked", "dormant", "monitor", "pending_verification", "completed", "archived",
)
ELOOP_TERMINAL_STATUSES = {"completed", "archived"}

ELOOP_PRIORITIES = ("critical", "high", "medium", "low", "monitor")
ELOOP_CONFIDENCE = ("high", "medium", "low")

# Dormancy thresholds (days since last_activity) by category, for loops with
# status=active. Relationship-category loops use their own cadence_days
# instead of this table.
EOLMS_DORMANCY_DAYS = {
    "action": 30,
    "decision": 30,
    "project": 45,
    "opportunity": 45,
    "research": 45,
    "strategic_initiative": 90,
    "waiting": 30,
}

# A strategic_initiative/project active loop with no activity in this many
# days gets a staleness warning (short of the harder dormancy transition).
EOLMS_STALENESS_WARNING_DAYS = 45


def _eloop_parse_date(v: Any) -> date | None:
    if not v:
        return None
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v)[:10])


@dataclass
class ELoop:
    id: str
    title: str
    category: str
    status: str
    priority: str = "medium"
    strategic_value: str | None = None
    owner: str = "Todd Vahlsing"
    created_at: date = field(default_factory=date.today)
    updated_at: date = field(default_factory=date.today)
    last_activity: date = field(default_factory=date.today)
    next_action: str | None = None
    waiting_on: str | None = None
    activation_date: date | None = None
    activation_condition: str | None = None
    due_date: date | None = None
    cadence_days: int | None = None
    related_people: list[str] = field(default_factory=list)
    related_orgs: list[str] = field(default_factory=list)
    related_loop_ids: list[str] = field(default_factory=list)
    blocked_by: list[str] = field(default_factory=list)
    related_documents: list[str] = field(default_factory=list)
    source_ref: str | None = None
    confidence: str = "medium"
    history: list[dict] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    requires_verification: bool = False

    @property
    def is_terminal(self) -> bool:
        return self.status in ELOOP_TERMINAL_STATUSES

    @property
    def days_since_activity(self) -> int:
        return (date.today() - self.last_activity).days

    def dormancy_threshold(self) -> int | None:
        if self.category == "relationship":
            return self.cadence_days
        return EOLMS_DORMANCY_DAYS.get(self.category)

    def to_dict(self) -> dict:
        d = {
            "id": self.id, "title": self.title, "category": self.category,
            "status": self.status, "priority": self.priority,
            "strategic_value": self.strategic_value, "owner": self.owner,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "last_activity": self.last_activity.isoformat(),
            "next_action": self.next_action, "waiting_on": self.waiting_on,
            "activation_date": self.activation_date.isoformat() if self.activation_date else None,
            "activation_condition": self.activation_condition,
            "due_date": self.due_date.isoformat() if self.due_date else None,
            "cadence_days": self.cadence_days,
            "related_people": self.related_people, "related_orgs": self.related_orgs,
            "related_loop_ids": self.related_loop_ids, "blocked_by": self.blocked_by,
            "related_documents": self.related_documents, "source_ref": self.source_ref,
            "confidence": self.confidence, "history": self.history, "tags": self.tags,
            "requires_verification": self.requires_verification,
        }
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "ELoop":
        return cls(
            id=d["id"], title=d["title"], category=d["category"], status=d["status"],
            priority=d.get("priority", "medium"), strategic_value=d.get("strategic_value"),
            owner=d.get("owner", "Todd Vahlsing"),
            created_at=_eloop_parse_date(d.get("created_at")) or date.today(),
            updated_at=_eloop_parse_date(d.get("updated_at")) or date.today(),
            last_activity=_eloop_parse_date(d.get("last_activity")) or date.today(),
            next_action=d.get("next_action"), waiting_on=d.get("waiting_on"),
            activation_date=_eloop_parse_date(d.get("activation_date")),
            activation_condition=d.get("activation_condition"),
            due_date=_eloop_parse_date(d.get("due_date")),
            cadence_days=d.get("cadence_days"),
            related_people=d.get("related_people") or [],
            related_orgs=d.get("related_orgs") or [],
            related_loop_ids=d.get("related_loop_ids") or [],
            blocked_by=d.get("blocked_by") or [],
            related_documents=d.get("related_documents") or [],
            source_ref=d.get("source_ref"), confidence=d.get("confidence", "medium"),
            history=d.get("history") or [], tags=d.get("tags") or [],
            requires_verification=bool(d.get("requires_verification", False)),
        )


def load_eloops(path: Path = EOLMS_PATH) -> list[ELoop]:
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return [ELoop.from_dict(d) for d in raw]


def eloops_by_status(loops: Iterable[ELoop]) -> dict[str, list[ELoop]]:
    out: dict[str, list[ELoop]] = defaultdict(list)
    for lp in loops:
        out[lp.status].append(lp)
    return dict(out)


def eloops_executive_summary(loops: Iterable[ELoop], today: date | None = None) -> dict:
    """Executive roll-up counts + the 1-3 loops needing attention today.

    Returned dict feeds both `eolms status` (CLI) and the Daily Brief
    Executive Status block — kept here so both callers agree on the numbers.
    """
    today = today or date.today()
    loops = list(loops)
    by_status = eloops_by_status(loops)

    active_strategic = [l for l in loops if l.category == "strategic_initiative" and l.status == "active"]
    active_projects = [l for l in loops if l.category == "project" and l.status == "active"]
    decisions_needed = [l for l in loops if l.category == "decision" and l.status in ("active", "identified", "qualified")]
    waiting_items = [l for l in loops if l.status == "waiting"]
    waiting_overdue = [l for l in waiting_items if l.days_since_activity > 7]
    relationship_due = [
        l for l in loops
        if l.category == "relationship" and l.cadence_days and l.days_since_activity >= l.cadence_days
        and l.status not in ELOOP_TERMINAL_STATUSES
    ]
    blocked = [l for l in loops if l.status == "blocked"]
    dormant = [l for l in loops if l.status == "dormant"]
    pending_verification = [l for l in loops if l.status == "pending_verification"]
    # A fix sitting unverified for a week-plus is itself a signal, same reasoning as
    # waiting_overdue above.
    pending_verification_stale = [l for l in pending_verification if l.days_since_activity > 7]
    completed_since_yesterday = [
        l for l in loops
        if l.status == "completed" and l.updated_at >= today - timedelta(days=1)
    ]
    stale_active = [
        l for l in loops
        if l.status == "active" and l.category in ("strategic_initiative", "project")
        and l.days_since_activity > EOLMS_STALENESS_WARNING_DAYS
    ]

    # CoS recommendation candidates: overdue waiting first, then decisions
    # with no next_action, then relationships past cadence.
    recs: list[str] = []
    for l in sorted(waiting_overdue, key=lambda x: -x.days_since_activity)[:2]:
        recs.append(f"{l.title} ({l.days_since_activity}d waiting)")
    for l in decisions_needed:
        if not l.next_action:
            recs.append(l.title)
    for l in relationship_due[:2]:
        recs.append(f"{l.title} ({l.days_since_activity}d since last touch)")

    return {
        "counts": {
            "active_strategic_initiatives": len(active_strategic),
            "active_projects": len(active_projects),
            "decisions_needed": len(decisions_needed),
            "waiting_items": len(waiting_items),
            "waiting_items_overdue": len(waiting_overdue),
            "relationship_followups_due": len(relationship_due),
            "blocked": len(blocked),
            "completed_since_yesterday": len(completed_since_yesterday),
            "dormant": len(dormant),
            "stale_active": len(stale_active),
            "pending_verification": len(pending_verification),
            "pending_verification_stale": len(pending_verification_stale),
        },
        "by_status": {k: len(v) for k, v in by_status.items()},
        "recommendation_candidates": recs[:3],
        "blocked_loops": [l.id for l in blocked],
        "stale_loop_ids": [l.id for l in stale_active],
    }


# -----------------------------------------------------------------------------
# Dormancy computation
# -----------------------------------------------------------------------------

@dataclass
class Crossing:
    name: str
    id: str
    tier: str
    last_touch: date | None
    days_ago: int | None
    overage: int | None  # None = no last_touch on record
    company: str | None
    circles: list[str]


def compute_crossings(baseline: list[dict], today: date) -> tuple[list[Crossing], list[Crossing]]:
    """Return (crossings, gap_no_last_touch).

    A 'crossing' is an ACTIVE RC whose `today - last_touch` exceeds the tier
    threshold. RCs with no `last_touch` are returned in the second list and
    are NOT included in the crossings list (per P-001 step 2).
    """
    crossings: list[Crossing] = []
    gaps: list[Crossing] = []
    for e in baseline:
        if e.get("signal_class") != "RC":
            continue
        if e.get("rc_state") != "ACTIVE":
            continue
        tier = e.get("rc_tier") or ""
        threshold = TIER_THRESHOLD_DAYS.get(tier)
        if threshold is None:
            continue
        lt = e.get("last_touch")
        circles = e.get("circles") or []
        if not lt:
            gaps.append(Crossing(
                name=e["name"], id=e["id"], tier=tier,
                last_touch=None, days_ago=None, overage=None,
                company=e.get("current_company"), circles=circles,
            ))
            continue
        lt_date = date.fromisoformat(lt)
        days_ago = (today - lt_date).days
        overage = days_ago - threshold
        if overage > 0:
            crossings.append(Crossing(
                name=e["name"], id=e["id"], tier=tier,
                last_touch=lt_date, days_ago=days_ago, overage=overage,
                company=e.get("current_company"), circles=circles,
            ))
    crossings.sort(key=lambda c: c.overage or 0, reverse=True)
    return crossings, gaps


def compute_quiet_zones(baseline: list[dict], today: date) -> list[Crossing]:
    """Inner-tier RCs INSIDE their threshold (good standing)."""
    out: list[Crossing] = []
    for e in baseline:
        if e.get("signal_class") != "RC":
            continue
        if e.get("rc_state") != "ACTIVE":
            continue
        if e.get("rc_tier") != "inner":
            continue
        lt = e.get("last_touch")
        if not lt:
            continue
        lt_date = date.fromisoformat(lt)
        days_ago = (today - lt_date).days
        if days_ago < TIER_THRESHOLD_DAYS["inner"]:
            out.append(Crossing(
                name=e["name"], id=e["id"], tier="inner",
                last_touch=lt_date, days_ago=days_ago, overage=None,
                company=e.get("current_company"), circles=e.get("circles") or [],
            ))
    out.sort(key=lambda c: c.days_ago or 0)
    return out


# -----------------------------------------------------------------------------
# Card gap detection
# -----------------------------------------------------------------------------

def rcs_without_cards(baseline: list[dict]) -> list[dict]:
    have = list_card_ids()
    out = []
    for e in baseline:
        if e.get("signal_class") != "RC":
            continue
        if e["id"] not in have:
            out.append({"id": e["id"], "name": e["name"], "tier": e.get("rc_tier")})
    return out


def contact_field_gaps(baseline: list[dict]) -> list[dict]:
    """Inner/broader RCs missing email or phone."""
    out = []
    for e in baseline:
        if e.get("signal_class") != "RC":
            continue
        if e.get("rc_state") != "ACTIVE":
            continue
        missing = []
        if not e.get("email"):
            missing.append("email")
        if not e.get("phone"):
            missing.append("phone")
        if missing:
            out.append({
                "name": e["name"],
                "id": e["id"],
                "tier": e.get("rc_tier"),
                "missing": missing,
            })
    out.sort(key=lambda r: (0 if r["tier"] == "inner" else 1, r["name"]))
    return out


# -----------------------------------------------------------------------------
# Network cluster / gap scoring
# -----------------------------------------------------------------------------

def cluster_by_company(baseline: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = defaultdict(list)
    for e in baseline:
        company = (e.get("current_company") or "").strip()
        if not company:
            continue
        out[company].append(e)
    return out


def cluster_inner_anchor_score(baseline: list[dict], min_cluster: int = 5) -> list[dict]:
    """For each company-cluster of size >= min_cluster, report whether it has
    an inner-tier RC anchor. Surfaces companies that are large but unanchored —
    the network-gap signal.
    """
    rows = []
    for company, members in cluster_by_company(baseline).items():
        if len(members) < min_cluster:
            continue
        inner_rcs = [m for m in members if m.get("signal_class") == "RC"
                     and m.get("rc_tier") == "inner"
                     and m.get("rc_state") == "ACTIVE"]
        any_rc = [m for m in members if m.get("signal_class") == "RC"]
        lkis = [m for m in members if m.get("signal_class") == "LKI"]
        rows.append({
            "company": company,
            "total": len(members),
            "any_rc": len(any_rc),
            "inner_rcs": [m["name"] for m in inner_rcs],
            "lki_count": len(lkis),
            "lki_names": [m["name"] for m in lkis],
            "anchor_gap": len(inner_rcs) == 0,
            "gap_score": len(lkis) if len(inner_rcs) == 0 else 0,
        })
    rows.sort(key=lambda r: (not r["anchor_gap"], -r["gap_score"], -r["total"]))
    return rows


def warm_path_candidates_for_threads(
    baseline: list[dict], threads: list[dict], min_cluster: int = 2,
) -> list[dict]:
    """RB-9.66-B: cross-reference active-opportunity thread companies against
    network-gap clusters to surface warm-path candidates.

    For each open thread whose `companies` list overlaps an anchor-gap cluster
    (no inner-tier active RC, but >=1 LKI already connected), return the LKI
    names as warm-path / RC-promotion candidates — people RB already knows who
    have a foothold at the target company but haven't been engaged as a
    relationship anchor for this opportunity.
    """
    rows = cluster_inner_anchor_score(baseline, min_cluster=min_cluster)
    by_company = {r["company"].lower(): r for r in rows}

    out: list[dict] = []
    for t in threads:
        if t.get("status") != "open":
            continue
        for company in (t.get("companies") or []):
            row = by_company.get(str(company).lower())
            if not row or not row.get("anchor_gap") or not row.get("lki_names"):
                continue
            out.append({
                "thread_id": t.get("id"),
                "thread_title": t.get("title"),
                "boost_for_brief": t.get("boost_for_brief"),
                "company": row["company"],
                "candidates": row["lki_names"],
                "cluster_size": row["total"],
            })
    # High-boost threads first, then larger candidate pools.
    _boost_rank = {"high": 0, "medium": 1, "low": 2}
    out.sort(key=lambda r: (_boost_rank.get(r.get("boost_for_brief"), 9), -len(r["candidates"])))
    return out


# -----------------------------------------------------------------------------
# Inbox overlays — calendar + email
# -----------------------------------------------------------------------------

def _normalize_email(s: str | None) -> str:
    if not s:
        return ""
    # Accept either "Name <email>" or bare email
    s = s.strip()
    if "<" in s and ">" in s:
        s = s[s.find("<") + 1: s.find(">")]
    return s.lower()


def _build_email_index(baseline: list[dict]) -> dict[str, dict]:
    """Map lowercase email -> baseline entry."""
    idx: dict[str, dict] = {}
    for e in baseline:
        em = _normalize_email(e.get("email"))
        if em:
            idx[em] = e
    return idx


def load_session_index() -> list[dict]:
    """Read system/_sessions/index.json. Returns the list of session entries
    (newest first) or [] if no index exists yet.
    """
    if not SESSIONS_INDEX_PATH.exists():
        return []
    try:
        data = json.loads(SESSIONS_INDEX_PATH.read_text())
    except json.JSONDecodeError:
        return []
    return list(data.get("sessions") or [])


def load_recent_sessions(limit: int = 3) -> list[dict]:
    """Return the last `limit` session entries with full body included.

    Each entry shape:
        {
            "session_id": ...,
            "date": ...,
            "frontmatter": {...},
            "body": str,
            "file": "system/_sessions/...",
        }
    """
    out: list[dict] = []
    for entry in load_session_index()[:limit]:
        rel = entry.get("file")
        if not rel:
            continue
        p = PROJECT_DIR / rel
        if not p.exists():
            continue
        text = p.read_text()
        # Split frontmatter / body
        body = text
        fm: dict = {}
        if text.startswith("---"):
            end = text.find("\n---", 3)
            if end >= 0:
                fm = _yaml_load(text[3:end].strip()) or {}
                body = text[end + 4:].lstrip("\n")
        out.append({
            "session_id": entry.get("session_id"),
            "date": entry.get("date"),
            "frontmatter": fm,
            "body": body,
            "file": rel,
        })
    return out


def load_inbox_accounts() -> list[dict]:
    """Read `system/inbox/accounts.yaml`. Return [] if missing or unreadable."""
    if not INBOX_ACCOUNTS_PATH.exists():
        return []
    try:
        data = _yaml_load(INBOX_ACCOUNTS_PATH.read_text())
    except Exception:
        return []
    return list(data.get("accounts") or [])


def self_emails() -> set[str]:
    """Set of email addresses the operator owns across all enabled accounts.
    Used to skip self-attendees and identify sent threads.
    """
    out: set[str] = set()
    for a in load_inbox_accounts():
        if not a.get("enabled", True):
            continue
        em = _normalize_email(a.get("email"))
        if em:
            out.add(em)
    return out


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}


def load_calendar() -> dict:
    """Aggregate calendar JSON across every enabled account in accounts.yaml,
    plus the legacy `calendar.json` if present. Events are tagged with their
    source `account_id` and deduplicated by event `id` (first writer wins).

    Returns:
        {
            "fetched_at": MIN(per-account fetched_at),
            "per_account_fetched_at": { account_id: ISO, ... },
            "events": [...],
            "accounts_seen": [account_id, ...],
        }
    """
    accounts = [a for a in load_inbox_accounts() if a.get("enabled", True)]
    aggregated: dict[str, dict] = {}
    per_account_ts: dict[str, str] = {}
    seen_accounts: list[str] = []

    # Per-account files
    for a in accounts:
        if "calendar" not in (a.get("feeds") or []):
            continue
        path = calendar_path_for(a["id"])
        payload = _read_json(path)
        if not payload:
            continue
        per_account_ts[a["id"]] = payload.get("fetched_at")
        seen_accounts.append(a["id"])
        for ev in payload.get("events") or []:
            ev = dict(ev)
            ev["account_id"] = a["id"]
            ev["account_label"] = a.get("label") or a["id"]
            eid = ev.get("id")
            if eid and eid not in aggregated:
                aggregated[eid] = ev

    # Legacy single-file fallback (for back-compat with pre-multi-account state)
    legacy = _read_json(CALENDAR_PATH)
    if legacy and legacy.get("events"):
        per_account_ts.setdefault("_legacy", legacy.get("fetched_at"))
        seen_accounts.append("_legacy")
        for ev in legacy["events"]:
            eid = ev.get("id")
            if eid and eid not in aggregated:
                ev = dict(ev)
                ev.setdefault("account_id", "_legacy")
                ev.setdefault("account_label", "Legacy single-account feed")
                aggregated[eid] = ev

    if not aggregated:
        return {}

    fetched_at = None
    valid_ts = [t for t in per_account_ts.values() if t]
    if valid_ts:
        fetched_at = min(valid_ts)
    return {
        "fetched_at": fetched_at,
        "per_account_fetched_at": per_account_ts,
        "events": list(aggregated.values()),
        "accounts_seen": seen_accounts,
    }


def source_readiness(*, feed: str | None = None, seen_accounts: list[str] | None = None) -> dict:
    """Report expected-vs-observed connector/source coverage.

    The overlays should never let "no rows found" masquerade as "nothing
    exists" when expected accounts are missing. This small readiness layer is
    intentionally data-file based: it inspects the manifest and local captures,
    then tells the caller whether a conclusion is well-instrumented.
    """
    accounts = [a for a in load_inbox_accounts() if a.get("enabled", True)]
    observed = set(seen_accounts or [])
    rows: list[dict] = []
    expected_ids: list[str] = []
    observed_ids: list[str] = []
    missing_ids: list[str] = []
    business_missing: list[str] = []

    def _path_for(account_id: str, feed_name: str) -> Path:
        if feed_name == "calendar":
            return calendar_path_for(account_id)
        if feed_name == "email":
            return email_path_for(account_id)
        if feed_name == "email_sent":
            return email_sent_path_for(account_id)
        return INBOX_DIR / f"{feed_name}.{account_id}.json"

    for a in accounts:
        feeds = a.get("feeds") or []
        candidate_feeds = [feed] if feed else feeds
        if feed and feed not in feeds:
            continue
        account_expected = False
        account_present = False
        present_feeds: list[str] = []
        missing_feeds: list[str] = []
        for f in candidate_feeds:
            account_expected = True
            path = _path_for(a["id"], f)
            present = path.exists()
            if present:
                present_feeds.append(f)
                account_present = True
            else:
                missing_feeds.append(f)
        if not account_expected:
            continue
        expected_ids.append(a["id"])
        if account_present or a["id"] in observed:
            observed_ids.append(a["id"])
        else:
            missing_ids.append(a["id"])
            if a.get("role") in {"work", "board", "side-project"}:
                business_missing.append(a["id"])
        rows.append({
            "id": a["id"],
            "label": a.get("label") or a["id"],
            "email": a.get("email"),
            "role": a.get("role") or "other",
            "feeds_expected": candidate_feeds,
            "feeds_present": present_feeds,
            "feeds_missing": missing_feeds,
            "mcp_status": a.get("mcp_status"),
        })

    coverage = 1.0 if not expected_ids else len(set(observed_ids)) / len(set(expected_ids))
    status = (
        "ready" if coverage >= 1.0 else
        "partial_business_calendar_missing" if feed == "calendar" and business_missing else
        "partial"
    )
    return {
        "feed": feed or "all",
        "status": status,
        "coverage": round(coverage, 3),
        "expected_accounts": expected_ids,
        "observed_accounts": sorted(set(observed_ids)),
        "missing_accounts": sorted(set(missing_ids)),
        "business_missing_accounts": sorted(set(business_missing)),
        "accounts": rows,
        "guidance": (
            "Search all observed accounts, but do not conclude there are no meetings until missing business calendars are connected or explicitly skipped."
            if feed == "calendar" and (missing_ids or business_missing) else
            "Source coverage is sufficient for this feed."
        ),
    }


def load_email() -> dict:
    """Aggregate email JSON across every enabled account. Shape mirrors
    `load_calendar` — but threads, not events.
    """
    accounts = [a for a in load_inbox_accounts() if a.get("enabled", True)]
    aggregated: dict[str, dict] = {}
    per_account_ts: dict[str, str] = {}
    seen_accounts: list[str] = []

    for a in accounts:
        if "email" not in (a.get("feeds") or []):
            continue
        # Per-account capture comes from up to two files: inbox-mailbox
        # and sent-mailbox. Both write the same normalized thread shape;
        # the recency merge below picks the most-recent version of any
        # thread that appears in both.
        per_account_payloads = []
        inbox_payload = _read_json(email_path_for(a["id"]))
        if inbox_payload:
            per_account_payloads.append(("inbox", inbox_payload))
        sent_payload = _read_json(email_sent_path_for(a["id"]))
        if sent_payload:
            per_account_payloads.append(("sent", sent_payload))
        if not per_account_payloads:
            continue
        # The earliest fetched_at across mailboxes is the per-account
        # freshness floor — both mailboxes need to be fresh for the
        # account to be considered fresh.
        ts_candidates = [
            p.get("fetched_at") for _, p in per_account_payloads if p.get("fetched_at")
        ]
        per_account_ts[a["id"]] = min(ts_candidates) if ts_candidates else None
        seen_accounts.append(a["id"])
        for _mailbox, payload in per_account_payloads:
            for th in payload.get("threads") or []:
                th = dict(th)
                th["account_id"] = a["id"]
                th["account_label"] = a.get("label") or a["id"]
                tid = th.get("thread_id")
                if not tid:
                    continue
                # When the same thread appears in multiple per-account
                # files (inbox + sent), the version with the most-recent
                # message wins. ISO timestamps sort correctly as strings.
                existing = aggregated.get(tid)
                if existing is None or (th.get("last_message_at") or "") > (existing.get("last_message_at") or ""):
                    aggregated[tid] = th

    # Legacy single-file fallback — read inbox + sent variants if present.
    for legacy_path in (EMAIL_PATH, EMAIL_SENT_PATH):
        legacy = _read_json(legacy_path)
        if not (legacy and legacy.get("threads")):
            continue
        per_account_ts.setdefault("_legacy", legacy.get("fetched_at"))
        if "_legacy" not in seen_accounts:
            seen_accounts.append("_legacy")
        for th in legacy["threads"]:
            tid = th.get("thread_id")
            if not tid:
                continue
            th = dict(th)
            th.setdefault("account_id", "_legacy")
            th.setdefault("account_label", "Legacy single-account feed")
            existing = aggregated.get(tid)
            if existing is None or (th.get("last_message_at") or "") > (existing.get("last_message_at") or ""):
                aggregated[tid] = th

    if not aggregated:
        return {}

    fetched_at = None
    valid_ts = [t for t in per_account_ts.values() if t]
    if valid_ts:
        fetched_at = min(valid_ts)
    return {
        "fetched_at": fetched_at,
        "per_account_fetched_at": per_account_ts,
        "threads": list(aggregated.values()),
        "accounts_seen": seen_accounts,
    }


def _is_noise_sender(email: str) -> bool:
    em = email.lower()
    return any(p in em for p in NOISE_DOMAIN_PATTERNS)


def is_overlay_stale(payload: dict, fresh_seconds: int = INBOX_FRESH_SECONDS) -> bool:
    """True if the overlay's fetched_at is older than `fresh_seconds`."""
    from datetime import datetime, timezone
    ts = payload.get("fetched_at")
    if not ts:
        return True
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return True
    age = (datetime.now(timezone.utc) - dt).total_seconds()
    return age > fresh_seconds


def _domain_of(email: str) -> str:
    return email.split("@", 1)[-1].lower() if "@" in email else ""


def _company_match(text: str, companies: list[str]) -> list[str]:
    """Return the subset of company names that appear in `text` (case-insensitive)."""
    if not text:
        return []
    low = text.lower()
    matched = []
    for c in companies:
        if not c:
            continue
        # match either the full name or the second word + .com pattern
        if c.lower() in low:
            matched.append(c)
    return matched


def calendar_overlay(today: date, baseline: list[dict] | None = None,
                     threads: list[dict] | None = None) -> dict:
    """Match calendar events against baseline. Return:

        {
            "fetched_at": ISO | null,
            "stale": bool,
            "today": [events for today],
            "tomorrow": [events for tomorrow],
            "this_week": [events 2+ days out, within 7 days],
            "with_baseline_match": int,
            "all_with_baseline_match": [every matched event in the fetched window,
                                        past or future — see interaction_capture.py],
            "attendees_not_in_baseline": [{email, count, last_seen_event}, ...],
        }

    Each event gets an `attendees_matched` list with the baseline entry
    (name, signal_class, rc_tier, id) for any attendee whose email matches.
    Each event also carries `active_thread_match` if its title/description hits
    an active-thread company name.
    """
    from datetime import timedelta
    payload = load_calendar()
    if baseline is None:
        baseline = load_baseline()
    if threads is None:
        threads = [t for t in load_active_threads() if t.get("status") == "open"]
    em_index = _build_email_index(baseline)
    all_thread_companies: list[str] = []
    for t in threads:
        all_thread_companies.extend(t.get("companies") or [])
    self_set = self_emails()
    out = {
        "fetched_at": payload.get("fetched_at"),
        "stale": is_overlay_stale(payload) if payload else True,
        "accounts_seen": payload.get("accounts_seen") if payload else [],
        "source_readiness": source_readiness(
            feed="calendar",
            seen_accounts=payload.get("accounts_seen") if payload else [],
        ),
        "today": [],
        "tomorrow": [],
        "this_week": [],
        "with_baseline_match": 0,
        # Every baseline-matched event in the fetched window, regardless of
        # date bucket (past events included) — unlike today/tomorrow/this_week,
        # which are display-oriented for briefs. Used by interaction_capture.py
        # to distill durable interaction facts before the raw source_cache
        # (72h retention, see retention_policy.py) rolls over.
        "all_with_baseline_match": [],
        "attendees_not_in_baseline": [],
        "triage_signals": [],
    }
    if not payload:
        return out

    # RB 9.88 (RB-DEFECT-046 Slice 1): triage title+description for
    # intelligence-bearing content, reusing one registry load.
    import intelligence_triage as _itriage
    _triage_registry = _itriage._load_artifact_registry()

    # RB-2026-08-28: personal vs. business gate, per Todd's explicit
    # direction -- RB is a business/professional relationship-capital tool;
    # personal events/relationships must never surface in calendar
    # intelligence. Same "exclude at the boundary" discipline as the
    # declined-invite exclusion just below.
    import personal_relationship_guard as _prg
    _personal_guard_cfg = _prg.load_config()

    def _triage_event(ev: dict) -> None:
        text = f"{ev.get('title') or ''}\n{ev.get('description') or ''}".strip()
        if not text:
            return
        result = _itriage.triage_overlay_text(
            text, source_type="calendar", source_name=ev.get("id"),
            registry=_triage_registry,
        )
        if result is not None:
            out["triage_signals"].append({
                "event_id": ev.get("id"),
                "title": ev.get("title"),
                "triage": result,
            })

    tomorrow = today + timedelta(days=1)
    week_end = today + timedelta(days=7)
    unmatched_attendees: dict[str, dict] = {}
    for ev in payload.get("events") or []:
        # A recurring invitation that Todd declined is not an upcoming
        # meeting. Exclude it at the calendar boundary so it cannot leak
        # into prep, identity-gap, closeout, or relationship intelligence.
        # Some forwarded events contain more than one of Todd's addresses;
        # only suppress when every self-address response is declined.
        self_responses = [
            (a.get("response") or "").lower()
            for a in (ev.get("attendees") or [])
            if a.get("self") or _normalize_email(a.get("email")) in self_set
        ]
        if self_responses and all(r == "declined" for r in self_responses):
            continue
        # Personal vs. business gate: an exempt-sender attendee or an
        # exempt-topic title/description makes this a personal event --
        # never surfaced in calendar intelligence, matching the same rule
        # already applied to relationship intake for email content.
        _event_text = f"{ev.get('title') or ''}\n{ev.get('description') or ''}"
        _personal_hit = _prg.classify(text=_event_text, config=_personal_guard_cfg)
        if not _personal_hit.is_personal:
            for _a in ev.get("attendees") or []:
                if _a.get("self") or _normalize_email(_a.get("email")) in self_set:
                    continue
                _personal_hit = _prg.classify(
                    sender_name=_a.get("name") or "", sender_email=_a.get("email") or "",
                    text="", config=_personal_guard_cfg,
                )
                if _personal_hit.is_personal:
                    break
        if _personal_hit.is_personal:
            continue
        # Match attendees
        matched: list[dict] = []
        thread_boost_max = 1.0
        thread_ids: set[str] = set()
        self_response: str | None = None
        for a in ev.get("attendees") or []:
            em_for_self = _normalize_email(a.get("email"))
            if a.get("self") or em_for_self in self_set:
                self_response = a.get("response") or self_response
                continue
            em = _normalize_email(a.get("email"))
            entry = em_index.get(em)
            if entry:
                b, tids = thread_boost_for(entry, threads)
                if b > thread_boost_max:
                    thread_boost_max = b
                thread_ids.update(tids)
                matched.append({
                    "email": em,
                    "id": entry["id"],
                    "name": entry["name"],
                    "signal_class": entry.get("signal_class"),
                    "rc_tier": entry.get("rc_tier"),
                    "response": a.get("response"),
                    "thread_boost": round(b, 3),
                    "matched_threads": tids,
                })
            else:
                matched.append({
                    "email": em,
                    "id": None,
                    "name": None,
                    "signal_class": None,
                    "rc_tier": None,
                    "response": a.get("response"),
                    "thread_boost": 1.0,
                    "matched_threads": [],
                })
                slot = unmatched_attendees.setdefault(em, {
                    "email": em, "count": 0, "last_seen_event": None,
                })
                slot["count"] += 1
                if not slot["last_seen_event"] or (ev.get("start") or "") > (slot["last_seen_event"].get("start") or ""):
                    slot["last_seen_event"] = {
                        "title": ev.get("title"),
                        "start": ev.get("start"),
                    }
        unmatched = [a for a in (ev.get("attendees") or [])
                     if not a.get("self")
                     and _normalize_email(a.get("email")) not in self_set
                     and not em_index.get(_normalize_email(a.get("email")))]
        has_match = any(m["id"] for m in matched)
        if has_match:
            out["with_baseline_match"] += 1

        # Active-thread company match by free-text search of title + description + attendee domains
        haystack_text = " ".join([
            ev.get("title") or "",
            ev.get("description") or "",
        ])
        title_company_hits = _company_match(haystack_text, all_thread_companies)
        # Also check attendee domains against thread companies
        domain_hits = []
        for a in (ev.get("attendees") or []):
            if a.get("self"):
                continue
            d = _domain_of(_normalize_email(a.get("email")))
            for c in all_thread_companies:
                core_tok = c.split()[0].lower()
                if core_tok and core_tok in d:
                    domain_hits.append(c)
        active_thread_company_hits = sorted(set(title_company_hits + domain_hits))
        # Map hits back to thread ids
        active_thread_ids_via_company: list[str] = []
        for t in threads:
            for c in t.get("companies") or []:
                if c in active_thread_company_hits:
                    active_thread_ids_via_company.append(t["id"])
                    break

        # Parse start date
        start = ev.get("start") or ""
        try:
            start_date = date.fromisoformat(start[:10])
        except ValueError:
            continue

        row = {
            "id": ev.get("id"),
            "title": ev.get("title"),
            "description": ev.get("description"),
            "start": ev.get("start"),
            "end": ev.get("end"),
            "location": ev.get("location"),
            "html_link": ev.get("html_link"),
            "account_id": ev.get("account_id"),
            "account_label": ev.get("account_label"),
            "attendees_matched": matched,
            "unmatched_count": len(unmatched),
            "thread_boost_max": round(thread_boost_max, 3),
            "matched_threads": sorted(set(thread_ids) | set(active_thread_ids_via_company)),
            "active_thread_company_hits": active_thread_company_hits,
            # RB-2026-07-05: Todd's own RSVP status. Declined events must not
            # generate prep/priority/decision-queue items (found via the SCN
            # Guest Invitation defect — the event was declined but still
            # surfaced as a prep item because nothing downstream read this).
            "self_response": self_response,
        }
        if has_match:
            out["all_with_baseline_match"].append(row)
        if start_date == today:
            out["today"].append(row)
            _triage_event(ev)
        elif start_date == tomorrow:
            out["tomorrow"].append(row)
            _triage_event(ev)
        elif today < start_date <= week_end:
            out["this_week"].append(row)
    # Finalize unmatched-attendee summary
    out["attendees_not_in_baseline"] = sorted(
        unmatched_attendees.values(), key=lambda x: x["count"], reverse=True
    )
    return out


# ----------------------------------------------------------------------------
# Sent-followup classification helpers (Step 2 sprint).
#
# When Todd is the most recent sender on a thread, the email overlay should
# stop dropping the thread and instead classify it as a sent_followup
# carrying an expected-response window. The handoff defines three windows:
#
#     active opportunity / recruiting thread:     3-5 business days
#     warm professional relationship (baseline):  5-7 business days
#     low-urgency / network maintenance / unknown: 7-10 business days
#
# A thread is "active" when it matches an open active-thread by company or
# by recipient. It is "warm" when at least one recipient is in baseline but
# no active-thread match. Everything else is "unknown".
# ----------------------------------------------------------------------------

# (low_business_days, high_business_days) by category.
SENT_FOLLOWUP_WINDOWS = {
    "active":  (3, 5),
    "warm":    (5, 7),
    "unknown": (7, 10),
}


def _parse_msg_dt(value: str | None) -> datetime | None:
    """Best-effort parse of an inbox timestamp into a UTC-aware datetime.

    Handles ISO 8601 (fetched/normalized) and RFC 2822 (raw Gmail header)
    formats. Gmail returns last_message_at as RFC 2822 when the thread list
    is fetched without full message expansion, e.g.:
        "Tue, 16 Jun 2026 17:12:04 -0500"
    fromisoformat() silently returns None for that format, causing every
    sent-followup to show response_status="unknown" (no date = no window).
    """
    if not value:
        return None
    s = str(value).strip()
    if not s:
        return None
    if len(s) == 10 and s[4] == "-" and s[7] == "-":
        try:
            return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    # ISO 8601 path
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        pass
    # RFC 2822 path ("Mon, 8 Jun 2026 12:46:24 +0000")
    try:
        from email.utils import parsedate_to_datetime as _rfc2822
        dt = _rfc2822(s)
        return dt.astimezone(timezone.utc)
    except Exception:
        pass
    return None


def parse_message_date(value: str | None) -> str | None:
    """Public wrapper over `_parse_msg_dt`: normalize an inbox timestamp
    (ISO 8601 or RFC 2822 — Gmail thread lists return the latter) to a plain
    `YYYY-MM-DD` date string, or None if unparseable. Use this instead of
    string-slicing `last_message_at`/`start` directly — those fields are not
    guaranteed to be ISO 8601."""
    dt = _parse_msg_dt(value)
    return dt.date().isoformat() if dt else None


def _business_days_between(start: date, end: date) -> int:
    """Inclusive count of Mon-Fri days from `start` to `end` (>= 0).

    Counts the number of business days from start (exclusive) to end
    (inclusive). If end <= start, returns 0. Cheap day-by-day loop — fine
    for the days-since-sent ranges we deal with (always < a year).
    """
    if end <= start:
        return 0
    d = start
    out = 0
    while d < end:
        d = d + timedelta(days=1)
        # Monday=0 ... Friday=4
        if d.weekday() < 5:
            out += 1
    return out


def _add_business_days(start: date, n: int) -> date:
    """Return the date that is `n` business days after `start`."""
    d = start
    added = 0
    while added < n:
        d = d + timedelta(days=1)
        if d.weekday() < 5:
            added += 1
    return d


def _classify_sent_followup(*,
                            sent_at: str | None,
                            matched_thread_ids: list[str],
                            matched_contact_ids: list[str],
                            now: datetime | None = None) -> dict:
    """Return classification fields for a sent thread.

    Output keys: category, expected_response_by, response_status,
    recommended_action, confidence, business_days_since_sent.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    sent_dt = _parse_msg_dt(sent_at)

    if matched_thread_ids:
        category = "active"
    elif matched_contact_ids:
        category = "warm"
    else:
        category = "unknown"

    low, high = SENT_FOLLOWUP_WINDOWS[category]

    # Confidence:
    #   high   when we matched at least one open active thread (we know which
    #          opportunity is waiting);
    #   medium when we matched at least one baseline contact (we know who);
    #   low    otherwise (we have no anchor beyond "Todd sent something").
    if matched_thread_ids:
        confidence = "high"
    elif matched_contact_ids:
        confidence = "medium"
    else:
        confidence = "low"

    business_days = None
    expected_by: date | None = None
    if sent_dt is not None:
        business_days = _business_days_between(sent_dt.date(), now.date())
        expected_by = _add_business_days(sent_dt.date(), high)

    # Status / recommended action.
    if sent_dt is None:
        response_status = "unknown"
        recommended_action = "ask_todd"
    elif business_days is not None and business_days > high:
        response_status = "response_overdue"
        recommended_action = "follow_up"
    else:
        # Still inside the response window (including the low-end grace period).
        response_status = "awaiting_response"
        recommended_action = "monitor"

    return {
        "category": category,
        "response_window_business_days": [low, high],
        "business_days_since_sent": business_days,
        "expected_response_by": expected_by.isoformat() if expected_by else None,
        "response_status": response_status,
        "recommended_action": recommended_action,
        "confidence": confidence,
    }


def _extract_thread_recipients(thread: dict, self_set: set[str]) -> list[dict]:
    """Pull recipient {name,email} dicts off a normalized email thread.

    Reads `last_message_to` first (the field fetch_via_session.normalize_email
    populates once Step 2a lands). Falls back to scanning a `messages[]`
    array if present (newer normalizers may keep per-message detail).
    Excludes Todd's own addresses. Returns [] when no recipient info is
    available — older normalized files won't have it, so callers must
    handle the empty case.
    """
    out: list[dict] = []
    seen: set[str] = set()

    raw_to = thread.get("last_message_to") or []
    if isinstance(raw_to, list):
        for r in raw_to:
            if isinstance(r, dict):
                em = _normalize_email(r.get("email"))
                name = r.get("name")
            else:
                em = _normalize_email(str(r))
                name = None
            if not em or em in self_set or em in seen:
                continue
            seen.add(em)
            out.append({"email": em, "name": name})

    if not out:
        for m in thread.get("messages") or []:
            for r in m.get("toRecipients") or m.get("to") or []:
                if isinstance(r, dict):
                    em = _normalize_email(r.get("email"))
                    name = r.get("name")
                else:
                    em = _normalize_email(str(r))
                    name = None
                if not em or em in self_set or em in seen:
                    continue
                seen.add(em)
                out.append({"email": em, "name": name})

    return out


def _emit_sent_followup(thread: dict, *, baseline: list[dict],
                        em_index: dict, threads: list[dict],
                        all_thread_companies: list[str],
                        self_set: set[str], out: dict,
                        now: datetime) -> None:
    """Build a sent_followup entry on `out['sent_followups']` for a thread
    where Todd is the most recent sender. If we can't anchor the thread to
    any active thread, baseline contact, or recipient, increment
    self_sent_skipped instead — we don't want to flood the overlay with
    untraceable outbound noise.
    """
    try:
        settings = load_settings()
        suppressed_ids = (
            (settings.get("email_overlay") or {})
            .get("suppressed_sent_followup_thread_ids")
            or []
        )
    except Exception:  # noqa: BLE001 - suppression is best-effort, never blocks overlay
        suppressed_ids = []
    if thread.get("thread_id") in suppressed_ids:
        out["self_sent_skipped"] += 1
        return

    recipients = _extract_thread_recipients(thread, self_set)

    # Active-thread company hits: by recipient domain, by subject, by snippet.
    company_hits: list[str] = []
    for r in recipients:
        rd = _domain_of(r["email"])
        for c in all_thread_companies:
            core_tok = c.split()[0].lower()
            if core_tok and core_tok in rd and c not in company_hits:
                company_hits.append(c)
    subject_hits = _company_match(thread.get("subject") or "", all_thread_companies)
    snippet_hits = _company_match(thread.get("snippet") or "", all_thread_companies)
    all_company_hits = sorted(set(company_hits + subject_hits + snippet_hits))

    # Map company hits to thread ids.
    company_thread_ids: list[str] = []
    for th in threads:
        for c in th.get("companies") or []:
            if c in all_company_hits and th["id"] not in company_thread_ids:
                company_thread_ids.append(th["id"])
                break

    # Match recipients against the baseline.
    matched_contacts: list[dict] = []
    for r in recipients:
        entry = em_index.get(r["email"])
        if not entry:
            continue
        matched_contacts.append({
            "id": entry["id"],
            "name": entry["name"],
            "email": r["email"],
            "signal_class": entry.get("signal_class"),
            "rc_tier": entry.get("rc_tier"),
        })

    # If we have absolutely no anchor — no recipients, no company hits, no
    # baseline matches — drop it into the legacy noise counter so we don't
    # flood the overlay with untraceable outbound.
    if not recipients and not all_company_hits and not matched_contacts:
        out["self_sent_skipped"] += 1
        return

    classification = _classify_sent_followup(
        sent_at=thread.get("last_message_at"),
        matched_thread_ids=company_thread_ids,
        matched_contact_ids=[c["id"] for c in matched_contacts],
        now=now,
    )

    out["sent_followups"].append({
        "thread_id": thread.get("thread_id"),
        "subject": thread.get("subject"),
        "sent_at": thread.get("last_message_at"),
        "account_id": thread.get("account_id"),
        "account_label": thread.get("account_label"),
        "to": recipients,
        "matched_threads": company_thread_ids,
        "matched_contacts": matched_contacts,
        "active_thread_company_hits": all_company_hits,
        "snippet": (thread.get("snippet") or "")[:200],
        **classification,
    })


def email_overlay(baseline: list[dict] | None = None,
                  threads: list[dict] | None = None) -> dict:
    """Match email threads against baseline AND against active-thread companies.

    Returns:
        {
            "fetched_at": ISO | null,
            "stale": bool,
            "from_baseline": [...],            # threads where sender is in baseline
            "active_thread_company_hits": [...], # threads where sender's domain or subject hits an active thread company, even if sender isn't in baseline
            "senders_not_in_baseline": [{email, count, last_subject, active_thread_companies}, ...],
            "noise_skipped": int,
            "triage_signals": [{thread_id, subject, triage}, ...],
        }
    """
    payload = load_email()
    if baseline is None:
        baseline = load_baseline()
    if threads is None:
        threads = [t for t in load_active_threads() if t.get("status") == "open"]
    em_index = _build_email_index(baseline)
    all_thread_companies: list[str] = []
    for t in threads:
        all_thread_companies.extend(t.get("companies") or [])
    self_set = self_emails()
    out = {
        "fetched_at": payload.get("fetched_at"),
        "stale": is_overlay_stale(payload) if payload else True,
        "accounts_seen": payload.get("accounts_seen") if payload else [],
        "from_baseline": [],
        "active_thread_company_hits": [],
        "senders_not_in_baseline": [],
        "noise_skipped": 0,
        "triage_signals": [],
        # Threads where Todd was the most recent sender. Previously these
        # were dropped silently; now they become first-class sent-followup
        # signals with a response window and recommended action. See
        # _classify_sent_followup above and the Step 2 section of
        # system/CLAUDE_HANDOFF_2026-05-20_NEXT_SPRINT.md.
        "sent_followups": [],
        # self_sent_skipped is kept for backward compat with downstream
        # consumers (relationship_signals._what_to_ignore). It now counts
        # self-sent threads we could NOT anchor to an active thread, a
        # baseline contact, or a recipient — i.e. truly noisy outbound.
        "self_sent_skipped": 0,
    }
    if not payload:
        return out

    # RB 9.88 (RB-DEFECT-046 Slice 1): triage subject+snippet for
    # intelligence-bearing content, reusing one registry load.
    import intelligence_triage as _itriage
    _triage_registry = _itriage._load_artifact_registry()

    def _triage_thread(t: dict) -> None:
        text = f"{t.get('subject') or ''}\n{t.get('snippet') or ''}".strip()
        if not text:
            return
        result = _itriage.triage_overlay_text(
            text, source_type="email", source_name=t.get("thread_id"),
            registry=_triage_registry,
        )
        if result is not None:
            out["triage_signals"].append({
                "thread_id": t.get("thread_id"),
                "subject": t.get("subject"),
                "triage": result,
            })

    unmatched: dict[str, dict] = {}
    now = datetime.now(timezone.utc)
    for t in payload.get("threads") or []:
        last = t.get("last_message_from") or {}
        sender = _normalize_email(last.get("email"))
        if not sender:
            continue
        if sender in self_set:
            _emit_sent_followup(
                t, baseline=baseline, em_index=em_index, threads=threads,
                all_thread_companies=all_thread_companies, self_set=self_set,
                out=out, now=now,
            )
            continue
        entry = em_index.get(sender)

        # Active-thread company hits: by sender domain OR by subject/snippet
        sender_domain = _domain_of(sender)
        company_hits: list[str] = []
        for c in all_thread_companies:
            core_tok = c.split()[0].lower()
            if core_tok and core_tok in sender_domain:
                company_hits.append(c)
        subject_hits = _company_match(t.get("subject") or "", all_thread_companies)
        snippet_hits = _company_match(t.get("snippet") or "", all_thread_companies)
        all_company_hits = sorted(set(company_hits + subject_hits + snippet_hits))
        # Map company hits to thread ids
        company_thread_ids: list[str] = []
        for th in threads:
            for c in th.get("companies") or []:
                if c in all_company_hits:
                    company_thread_ids.append(th["id"])
                    break

        if entry:
            b, tids = thread_boost_for(entry, threads)
            row = {
                "thread_id": t.get("thread_id"),
                "subject": t.get("subject"),
                "last_message_at": t.get("last_message_at"),
                "unread": t.get("unread", False),
                "account_id": t.get("account_id"),
                "account_label": t.get("account_label"),
                "sender_email": sender,
                "sender_name": last.get("name") or entry.get("name"),
                "match": {
                    "id": entry["id"],
                    "name": entry["name"],
                    "signal_class": entry.get("signal_class"),
                    "rc_tier": entry.get("rc_tier"),
                },
                "thread_boost": round(b, 3),
                "matched_threads": sorted(set(tids) | set(company_thread_ids)),
                "active_thread_company_hits": all_company_hits,
            }
            out["from_baseline"].append(row)
            _triage_thread(t)
        else:
            if _is_noise_sender(sender):
                out["noise_skipped"] += 1
            # Record the unmatched sender, with extra emphasis when it hits an active-thread company
            slot = unmatched.setdefault(sender, {
                "email": sender, "count": 0, "last_subject": None,
                "last_message_at": None, "active_thread_companies": [],
                "active_thread_ids": [],
            })
            slot["count"] += 1
            ts = t.get("last_message_at") or ""
            if ts > (slot["last_message_at"] or ""):
                slot["last_message_at"] = ts
                slot["last_subject"] = t.get("subject")
            for c in all_company_hits:
                if c not in slot["active_thread_companies"]:
                    slot["active_thread_companies"].append(c)
            for tid in company_thread_ids:
                if tid not in slot["active_thread_ids"]:
                    slot["active_thread_ids"].append(tid)
            if all_company_hits:
                out["active_thread_company_hits"].append({
                    "thread_id": t.get("thread_id"),
                    "subject": t.get("subject"),
                    "last_message_at": t.get("last_message_at"),
                    "unread": t.get("unread", False),
                    "account_id": t.get("account_id"),
                    "account_label": t.get("account_label"),
                    "sender_email": sender,
                    "sender_name": last.get("name"),
                    "active_thread_company_hits": all_company_hits,
                    "active_thread_ids": company_thread_ids,
                    "snippet": (t.get("snippet") or "")[:200],
                })
                _triage_thread(t)

    # Sort outputs
    out["from_baseline"].sort(key=lambda r: (not r["unread"], r["last_message_at"] or ""), reverse=True)
    out["active_thread_company_hits"].sort(key=lambda r: (not r["unread"], r["last_message_at"] or ""), reverse=True)
    # Sent followups: overdue first, then by sent_at descending so the most
    # recent outbound surfaces above older monitor-only items. Two-pass
    # stable sort: secondary key first, then primary key.
    _RESPONSE_STATUS_RANK = {"response_overdue": 0, "unknown": 1, "awaiting_response": 2}
    out["sent_followups"].sort(key=lambda r: r.get("sent_at") or "", reverse=True)
    out["sent_followups"].sort(key=lambda r: _RESPONSE_STATUS_RANK.get(r.get("response_status"), 9))
    # Senders not in baseline: rank by (has active-thread hit, count)
    out["senders_not_in_baseline"] = sorted(
        unmatched.values(),
        key=lambda x: (bool(x["active_thread_companies"]), x["count"]),
        reverse=True,
    )
    return out


# -----------------------------------------------------------------------------
# Social feed overlay
# -----------------------------------------------------------------------------

def _normalize_linkedin_url(url: str | None) -> str:
    """Strip query string and trailing slash, lowercase. Returns '' for None."""
    if not url:
        return ""
    u = url.strip().lower()
    # Drop query string + fragment
    for sep in ("?", "#"):
        if sep in u:
            u = u.split(sep, 1)[0]
    return u.rstrip("/")


def _build_linkedin_index(baseline: list[dict]) -> dict[str, dict]:
    """Map normalized linkedin_url -> baseline entry."""
    idx: dict[str, dict] = {}
    for e in baseline:
        url = _normalize_linkedin_url(e.get("linkedin_url"))
        if url:
            idx[url] = e
    return idx


def _build_name_index(baseline: list[dict]) -> dict[str, dict]:
    """Map lowercase name -> baseline entry. Collisions resolved by last writer
    (rare for the 2,670-entry baseline, but worth surfacing if it happens)."""
    idx: dict[str, dict] = {}
    for e in baseline:
        n = (e.get("name") or "").strip().lower()
        if n:
            idx[n] = e
    return idx


def load_social_feed() -> dict:
    """Return the parsed social.feed.json or {} if missing."""
    if not SOCIAL_FEED_PATH.exists():
        return {}
    try:
        return json.loads(SOCIAL_FEED_PATH.read_text())
    except json.JSONDecodeError:
        return {}


def match_post_author(post: dict, url_index: dict[str, dict],
                      name_index: dict[str, dict]) -> dict | None:
    """Try URL match first, then case-insensitive name. Return baseline entry or None."""
    author = post.get("author") or {}
    url = _normalize_linkedin_url(author.get("linkedin_url"))
    if url and url in url_index:
        return url_index[url]
    name = (author.get("name") or "").strip().lower()
    if name and name in name_index:
        return name_index[name]
    return None


def social_overlay(baseline: list[dict] | None = None,
                   threads: list[dict] | None = None,
                   recent_days: int = 14) -> dict:
    """Match social posts against baseline + active-thread companies.

    Returns:
        {
            "fetched_at": ISO | null,
            "stale": bool,
            "source": string | null,            # e.g. "claude_in_chrome"
            "from_baseline": [...],             # posts whose author is in baseline
            "active_thread_company_hits": [...], # posts mentioning an active-thread company in author/text
            "authors_not_in_baseline": [...],   # authors with multiple posts we couldn't match
            "topic_signal": [...],              # very rough topic clustering (V0: company-name hits across posts)
        }
    """
    from datetime import datetime, timezone, timedelta
    payload = load_social_feed()
    if baseline is None:
        baseline = load_baseline()
    if threads is None:
        threads = [t for t in load_active_threads() if t.get("status") == "open"]
    url_index = _build_linkedin_index(baseline)
    name_index = _build_name_index(baseline)
    all_thread_companies: list[str] = []
    for t in threads:
        all_thread_companies.extend(t.get("companies") or [])

    out = {
        "fetched_at": payload.get("fetched_at"),
        "stale": is_overlay_stale(payload) if payload else True,
        "source": payload.get("source") if payload else None,
        "from_baseline": [],
        "active_thread_company_hits": [],
        "authors_not_in_baseline": [],
        "topic_signal": [],
    }
    if not payload:
        return out

    # Recency filter
    recency_cutoff = None
    try:
        recency_cutoff = datetime.now(timezone.utc) - timedelta(days=recent_days)
    except Exception:
        recency_cutoff = None

    unmatched: dict[str, dict] = {}
    company_post_counts: dict[str, int] = {}

    for post in payload.get("posts") or []:
        # Try author match
        entry = match_post_author(post, url_index, name_index)
        author = post.get("author") or {}
        author_name = author.get("name") or "(unknown)"
        author_url = author.get("linkedin_url")

        # Active-thread company hits — by author headline or post text
        text_blob = " ".join([
            (author.get("headline") or ""),
            (post.get("text") or ""),
        ])
        company_hits = _company_match(text_blob, all_thread_companies)
        company_thread_ids: list[str] = []
        for t in threads:
            for c in t.get("companies") or []:
                if c in company_hits:
                    company_thread_ids.append(t["id"])
                    break
        for c in company_hits:
            company_post_counts[c] = company_post_counts.get(c, 0) + 1

        engagement = post.get("engagement") or {}
        row_base = {
            "id": post.get("id"),
            "posted_at": post.get("posted_at"),
            "text": (post.get("text") or "")[:600],
            "post_url": post.get("post_url"),
            "engagement": engagement,
            "captured_at": post.get("captured_at"),
            "active_thread_company_hits": company_hits,
            "active_thread_ids": company_thread_ids,
        }

        if entry:
            b, tids = thread_boost_for(entry, threads)
            out["from_baseline"].append({
                **row_base,
                "author_name": author_name,
                "author_url": author_url,
                "match": {
                    "id": entry["id"],
                    "name": entry["name"],
                    "signal_class": entry.get("signal_class"),
                    "rc_tier": entry.get("rc_tier"),
                },
                "thread_boost": round(b, 3),
                "matched_threads": sorted(set(tids) | set(company_thread_ids)),
            })
        else:
            # Track unmatched authors who appear multiple times
            key = _normalize_linkedin_url(author_url) or (author_name.lower() if author_name else "")
            if key:
                slot = unmatched.setdefault(key, {
                    "name": author_name,
                    "linkedin_url": author_url,
                    "count": 0,
                    "last_post_at": None,
                    "company_hits": [],
                })
                slot["count"] += 1
                if not slot["last_post_at"] or (post.get("posted_at") or "") > (slot["last_post_at"] or ""):
                    slot["last_post_at"] = post.get("posted_at")
                for c in company_hits:
                    if c not in slot["company_hits"]:
                        slot["company_hits"].append(c)
            if company_hits:
                out["active_thread_company_hits"].append({
                    **row_base,
                    "author_name": author_name,
                    "author_url": author_url,
                })

    # Sort outputs by posted_at desc
    out["from_baseline"].sort(key=lambda r: r.get("posted_at") or "", reverse=True)
    out["active_thread_company_hits"].sort(key=lambda r: r.get("posted_at") or "", reverse=True)

    # Authors not in baseline: only show those with 2+ posts OR active-thread-company hits
    out["authors_not_in_baseline"] = sorted(
        (u for u in unmatched.values()
         if u["count"] >= 2 or u["company_hits"]),
        key=lambda u: (bool(u["company_hits"]), u["count"]),
        reverse=True,
    )

    # Topic signal: companies appearing in 2+ posts in the window
    out["topic_signal"] = sorted(
        ({"company": c, "post_count": n} for c, n in company_post_counts.items() if n >= 2),
        key=lambda r: r["post_count"], reverse=True,
    )

    return out


# -----------------------------------------------------------------------------
# Direct interaction overlay — phone calls + text messages
# -----------------------------------------------------------------------------

def _normalize_phone(s: str | None) -> str:
    """Strip everything non-digit. Re-prefix with +1 if it looks US.
    Returns "" for None / unrecognizable."""
    if not s:
        return ""
    s = s.strip()
    if "@" in s:
        # email-handle (iMessage), not a phone — let the caller route to email match
        return ""
    digits = "".join(ch for ch in s if ch.isdigit())
    if not digits:
        return ""
    if len(digits) == 10:
        return "+1" + digits
    if len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    # Already international or short code — return as-is with leading +
    return "+" + digits


def _build_phone_index(baseline: list[dict]) -> dict[str, dict]:
    """Map normalized phone → baseline entry. Skips contacts without phone."""
    idx: dict[str, dict] = {}
    for e in baseline:
        ph = _normalize_phone(e.get("phone"))
        if ph:
            idx[ph] = e
    return idx


SMS_EXEMPT_HANDLES_PATH = SYSTEM_DIR / "sms_trusted_senders.json"


def load_sms_exempt_handles() -> set[str]:
    """Handles a human has explicitly marked personal/private via
    sms_exempt_manager.py (system/sms_trusted_senders.json's exempt_handles).

    Returned in the same normalized shape _match_handle() compares against
    (+1XXXXXXXXXX for phones, lowercased email as-is) so callers can drop
    these from unmatched-handle gap surfacing without re-flagging a contact
    Todd has already told RB is a known, private, non-RC number.
    """
    if not SMS_EXEMPT_HANDLES_PATH.exists():
        return set()
    try:
        data = json.loads(SMS_EXEMPT_HANDLES_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return set()
    raw = data.get("exempt_handles") or []
    out: set[str] = set()
    for h in raw:
        if not isinstance(h, str) or not h.strip():
            continue
        if "@" in h:
            out.add(h.strip().lower())
        else:
            norm = _normalize_phone(h)
            if norm:
                out.add(norm)
    return out


def load_messages() -> dict:
    if not MESSAGES_PATH.exists():
        return {}
    try:
        return json.loads(MESSAGES_PATH.read_text())
    except json.JSONDecodeError:
        return {}


def load_calls() -> dict:
    if not CALLS_PATH.exists():
        return {}
    try:
        return json.loads(CALLS_PATH.read_text())
    except json.JSONDecodeError:
        return {}


def _match_handle(handle: str, phone_index: dict[str, dict],
                  email_index: dict[str, dict]) -> dict | None:
    """Try email match first (cheap, exact), then phone normalization."""
    if not handle:
        return None
    handle_low = handle.strip().lower()
    if "@" in handle_low:
        entry = email_index.get(handle_low)
        if entry:
            return entry
        return None
    normalized = _normalize_phone(handle)
    if not normalized:
        return None
    return phone_index.get(normalized)


def interaction_overlay(baseline: list[dict] | None = None,
                        today: date | None = None,
                        recent_days: int = 30) -> dict:
    """Match phone/text events against baseline. Surface direct-interaction
    summary plus a proposed `last_touch` update set.

    Returns:
        {
            "fetched_at": ISO | null,
            "stale": bool,
            "matched_contacts": [...],          # per-contact summary, ranked by recency
            "proposed_last_touch_updates": [...], # {contact_id, current, proposed, gap_days}
            "unmatched_recurring_handles": [...], # handles with 3+ events not in baseline
            "totals": { messages_in_window, calls_in_window, matched_count, unmatched_count }
        }
    """
    from datetime import timedelta
    msg_payload = load_messages()
    call_payload = load_calls()
    if baseline is None:
        baseline = load_baseline()
    if today is None:
        today = date.today()

    phone_index = _build_phone_index(baseline)
    email_index = _build_email_index(baseline)
    exempt_handles = load_sms_exempt_handles()
    cutoff = today - timedelta(days=recent_days)

    out = {
        "fetched_at": (msg_payload.get("fetched_at") or call_payload.get("fetched_at")),
        "stale": is_overlay_stale(msg_payload or call_payload) if (msg_payload or call_payload) else True,
        "matched_contacts": [],
        "proposed_last_touch_updates": [],
        "unmatched_recurring_handles": [],
        "totals": {"messages_in_window": 0, "calls_in_window": 0,
                   "matched_count": 0, "unmatched_count": 0},
    }
    if not (msg_payload or call_payload):
        return out

    # Per-contact summary structure
    summary: dict[str, dict] = {}
    unmatched: dict[str, dict] = {}

    def _accept_event(handle: str, at_iso: str | None, kind: str, sub: dict):
        if not at_iso:
            return
        try:
            at_date = date.fromisoformat(at_iso[:10])
        except ValueError:
            return
        if at_date < cutoff:
            return
        if kind == "message":
            out["totals"]["messages_in_window"] += 1
        else:
            out["totals"]["calls_in_window"] += 1
        entry = _match_handle(handle, phone_index, email_index)
        if entry:
            slot = summary.setdefault(entry["id"], {
                "id": entry["id"],
                "name": entry["name"],
                "signal_class": entry.get("signal_class"),
                "rc_tier": entry.get("rc_tier"),
                "current_last_touch": entry.get("last_touch"),
                "messages_in": 0, "messages_out": 0,
                "calls_in": 0, "calls_out": 0, "calls_missed": 0,
                "last_interaction_at": None,
                "handles_seen": set(),
            })
            slot["handles_seen"].add(handle)
            if kind == "message":
                if sub.get("direction") == "outbound":
                    slot["messages_out"] += 1
                else:
                    slot["messages_in"] += 1
            else:
                d = sub.get("direction") or ""
                if d == "outbound":
                    slot["calls_out"] += 1
                elif d == "missed":
                    slot["calls_missed"] += 1
                else:
                    slot["calls_in"] += 1
            if at_iso > (slot["last_interaction_at"] or ""):
                slot["last_interaction_at"] = at_iso
        elif _normalize_phone(handle) in exempt_handles or handle.strip().lower() in exempt_handles:
            # Todd has explicitly marked this handle personal/private via
            # sms_exempt_manager.py — don't keep flagging it as an
            # unresolved-identity gap.
            pass
        else:
            u = unmatched.setdefault(handle, {
                "handle": handle, "events": 0, "last_at": None,
                "service_mix": set(),
            })
            u["events"] += 1
            if at_iso > (u["last_at"] or ""):
                u["last_at"] = at_iso
            u["service_mix"].add(sub.get("service") or "")

    for ev in msg_payload.get("events") or []:
        _accept_event(ev.get("handle"), ev.get("at"), "message", ev)
    for ev in call_payload.get("events") or []:
        _accept_event(ev.get("handle"), ev.get("at"), "call", ev)

    # Build proposed last_touch updates
    for slot in summary.values():
        last = slot["last_interaction_at"]
        if not last:
            continue
        last_date = last[:10]
        cur = slot["current_last_touch"]
        if not cur or last_date > cur:
            try:
                gap = (date.fromisoformat(last_date) - date.fromisoformat(cur)).days if cur else None
            except ValueError:
                gap = None
            out["proposed_last_touch_updates"].append({
                "contact_id": slot["id"],
                "name": slot["name"],
                "current_last_touch": cur,
                "proposed_last_touch": last_date,
                "gap_days_since_current": gap,
                "evidence": (
                    f"{slot['messages_in']+slot['messages_out']} messages, "
                    f"{slot['calls_in']+slot['calls_out']+slot['calls_missed']} calls "
                    f"in window"
                ),
            })

    # Finalize matched-contacts list (sort by last interaction desc)
    contacts_list = []
    for slot in summary.values():
        slot["handles_seen"] = sorted(slot["handles_seen"])
        contacts_list.append(slot)
    contacts_list.sort(key=lambda r: r.get("last_interaction_at") or "", reverse=True)
    out["matched_contacts"] = contacts_list
    out["totals"]["matched_count"] = len(contacts_list)

    # Unmatched recurring handles — 3+ events
    unmatched_rows = []
    for h, info in unmatched.items():
        if info["events"] < 3:
            continue
        unmatched_rows.append({
            "handle": h,
            "events": info["events"],
            "last_at": info["last_at"],
            "services": sorted(info["service_mix"]),
        })
    unmatched_rows.sort(key=lambda r: r["events"], reverse=True)
    out["unmatched_recurring_handles"] = unmatched_rows
    out["totals"]["unmatched_count"] = sum(u["events"] for u in unmatched.values())

    # Sort proposed updates: largest gap first (likely most stale)
    out["proposed_last_touch_updates"].sort(
        key=lambda r: r.get("gap_days_since_current") or 99999, reverse=True,
    )

    return out


# -----------------------------------------------------------------------------
# Social outbound — your own posts + engagement from your graph
# -----------------------------------------------------------------------------

def load_own_posts() -> dict:
    if not SOCIAL_OWN_POSTS_PATH.exists():
        return {}
    try:
        return json.loads(SOCIAL_OWN_POSTS_PATH.read_text())
    except json.JSONDecodeError:
        return {}


def load_engagement() -> dict:
    if not SOCIAL_ENGAGEMENT_PATH.exists():
        return {}
    try:
        return json.loads(SOCIAL_ENGAGEMENT_PATH.read_text())
    except json.JSONDecodeError:
        return {}


def social_outbound_overlay(baseline: list[dict] | None = None,
                            threads: list[dict] | None = None,
                            today: date | None = None,
                            recent_days: int = 30) -> dict:
    """Synthesize your-own-posts + engagement into chief-of-staff signal.

    Returns:
        {
            "fetched_at": ISO | null,
            "stale": bool,
            "recent_posts": [...],                # your posts in the window
            "engagement_by_contact": [...],       # per-baseline-contact engagement summary
            "engagement_silence": [...],          # contacts who used to engage but stopped (cooling-by-engagement)
            "topic_engagement_map": [...],        # which topics engage which DRR-weighted contacts
            "active_thread_engagement": [...],    # engagement on posts touching active-thread companies
        }
    """
    from datetime import datetime, timezone, timedelta
    posts_payload = load_own_posts()
    eng_payload = load_engagement()
    if baseline is None:
        baseline = load_baseline()
    if threads is None:
        threads = [t for t in load_active_threads() if t.get("status") == "open"]
    if today is None:
        today = date.today()
    url_index = _build_linkedin_index(baseline)
    name_index = _build_name_index(baseline)
    all_thread_companies: list[str] = []
    for t in threads:
        all_thread_companies.extend(t.get("companies") or [])

    out = {
        "fetched_at": (posts_payload.get("fetched_at") or eng_payload.get("fetched_at")),
        "stale": is_overlay_stale(posts_payload or eng_payload) if (posts_payload or eng_payload) else True,
        "recent_posts": [],
        "engagement_by_contact": [],
        "engagement_silence": [],
        "topic_engagement_map": [],
        "active_thread_engagement": [],
    }
    if not (posts_payload or eng_payload):
        return out

    # Index posts by id for engagement lookup
    posts_by_id: dict[str, dict] = {}
    cutoff = today - timedelta(days=recent_days)
    for p in posts_payload.get("posts") or []:
        posts_by_id[p.get("post_id") or p.get("id")] = p
        # Recent posts list
        try:
            posted = date.fromisoformat((p.get("posted_at") or "")[:10])
        except ValueError:
            continue
        if posted >= cutoff:
            company_hits = _company_match(p.get("text") or "", all_thread_companies)
            thread_ids = []
            for t in threads:
                for c in t.get("companies") or []:
                    if c in company_hits:
                        thread_ids.append(t["id"])
                        break
            out["recent_posts"].append({
                **{k: v for k, v in p.items() if k != "text"},
                "text": (p.get("text") or "")[:240],
                "active_thread_company_hits": company_hits,
                "active_thread_ids": sorted(set(thread_ids)),
            })

    out["recent_posts"].sort(key=lambda r: r.get("posted_at") or "", reverse=True)

    # Walk engagement events, match engagers against baseline
    engager_summary: dict[str, dict] = {}
    topic_engagement: dict[str, dict[str, int]] = {}  # topic -> {contact_id: score}

    for ev in eng_payload.get("events") or []:
        engager = ev.get("engager") or {}
        url = _normalize_linkedin_url(engager.get("linkedin_url"))
        name = (engager.get("name") or "").strip().lower()
        match = url_index.get(url) if url else None
        if not match and name:
            match = name_index.get(name)
        if not match:
            continue  # ignore unmatched engagers in the summary

        et_type = ev.get("type") or "like"
        # "reaction" is the canonical type emitted by linkedin_own_engagement.py (LinkedIn API)
        # and linkedin_session_reader.py browser capture. Treat it as "like" for scoring/counting.
        et_normalized = "like" if et_type == "reaction" else et_type
        weight = {"like": 1, "comment": 5, "share": 3}.get(et_normalized, 1)
        # Recency multiplier
        try:
            when = date.fromisoformat((ev.get("at") or "")[:10])
            age = (today - when).days
            recency_mult = max(0.2, 1.0 - age / (recent_days * 2))
        except ValueError:
            recency_mult = 0.5

        score = weight * recency_mult

        slot = engager_summary.setdefault(match["id"], {
            "id": match["id"],
            "name": match["name"],
            "signal_class": match.get("signal_class"),
            "rc_tier": match.get("rc_tier"),
            "total_likes": 0, "total_comments": 0, "total_shares": 0,
            "score": 0.0,
            "last_engagement_at": None,
            "topics": set(),
            "drr_score": drr_score(match, today, threads)["score"],
        })
        slot["total_likes"] += 1 if et_normalized == "like" else 0
        slot["total_comments"] += 1 if et_normalized == "comment" else 0
        slot["total_shares"] += 1 if et_normalized == "share" else 0
        slot["score"] += score
        when_str = ev.get("at") or ""
        if when_str > (slot["last_engagement_at"] or ""):
            slot["last_engagement_at"] = when_str

        # Topic engagement map
        post = posts_by_id.get(ev.get("post_id"))
        if post:
            for topic in post.get("topics") or []:
                slot["topics"].add(topic)
                topic_engagement.setdefault(topic, {})
                topic_engagement[topic][match["id"]] = topic_engagement[topic].get(match["id"], 0) + score

            # Active-thread engagement
            company_hits = _company_match(post.get("text") or "", all_thread_companies)
            if company_hits:
                thread_ids = []
                for t in threads:
                    for c in t.get("companies") or []:
                        if c in company_hits:
                            thread_ids.append(t["id"])
                            break
                out["active_thread_engagement"].append({
                    "engager_id": match["id"],
                    "engager_name": match["name"],
                    "post_id": ev.get("post_id"),
                    "type": et_type,
                    "at": when_str,
                    "active_thread_ids": sorted(set(thread_ids)),
                    "company_hits": company_hits,
                })

    # Finalize engagement_by_contact (sort by composite score)
    contacts_list = []
    for slot in engager_summary.values():
        slot["topics"] = sorted(slot["topics"])
        slot["composite"] = round(slot["score"] * (slot["drr_score"] / 50.0), 1)
        contacts_list.append(slot)
    contacts_list.sort(key=lambda r: r["composite"], reverse=True)
    out["engagement_by_contact"] = contacts_list

    # Engagement silence — contacts who engaged historically but not in the window
    cutoff_str = cutoff.isoformat()
    out["engagement_silence"] = [
        {
            "id": s["id"], "name": s["name"],
            "signal_class": s["signal_class"], "rc_tier": s["rc_tier"],
            "last_engagement_at": s["last_engagement_at"],
            "drr_score": s["drr_score"],
        }
        for s in engager_summary.values()
        if s["last_engagement_at"] and s["last_engagement_at"][:10] < cutoff_str
    ]
    out["engagement_silence"].sort(key=lambda r: r["drr_score"], reverse=True)

    # Topic engagement map → top 5 topics with their top engagers
    topic_rows = []
    for topic, contact_scores in topic_engagement.items():
        sorted_contacts = sorted(contact_scores.items(), key=lambda kv: kv[1], reverse=True)[:5]
        topic_rows.append({
            "topic": topic,
            "total_engagement_score": round(sum(contact_scores.values()), 1),
            "top_engagers": [
                {"id": cid, "score": round(s, 1)}
                for cid, s in sorted_contacts
            ],
        })
    topic_rows.sort(key=lambda r: r["total_engagement_score"], reverse=True)
    out["topic_engagement_map"] = topic_rows

    out["active_thread_engagement"].sort(key=lambda r: r.get("at") or "", reverse=True)
    return out


def post_recommendations(baseline: list[dict] | None = None,
                         threads: list[dict] | None = None,
                         today: date | None = None) -> list[dict]:
    """Recommend post topics that would warm specific high-value contacts or
    activate active threads.

    Each recommendation:
        - cites which contact(s) it would warm
        - cites which active thread(s) it touches
        - cites which past topic(s) historically engaged those contacts
        - includes an impact estimate
    """
    if baseline is None:
        baseline = load_baseline()
    if threads is None:
        threads = [t for t in load_active_threads() if t.get("status") == "open"]
    if today is None:
        today = date.today()
    overlay = social_outbound_overlay(baseline=baseline, threads=threads, today=today)

    recs: list[dict] = []

    # 1. Engagement silence — contacts who used to engage but went quiet
    for s in overlay["engagement_silence"][:5]:
        recs.append({
            "title": f"Post on topics that historically engaged {s['name']}",
            "rationale": f"{s['name']} ({s['signal_class']}/{s['rc_tier'] or '—'}) "
                         f"hasn't engaged on your posts since {s['last_engagement_at'][:10]}. "
                         f"DRR {s['drr_score']}. Engagement-as-dormancy signal: cooling.",
            "would_warm": [s["id"]],
            "active_thread_ids": [],
            "impact": "medium" if s["signal_class"] == "RC" else "low",
        })

    # 2. Active threads with low or no recent engagement
    threads_engaged_recently = {tid for r in overlay["active_thread_engagement"][:50] for tid in r.get("active_thread_ids") or []}
    for t in threads:
        if t["id"] in threads_engaged_recently:
            continue
        companies = t.get("companies") or []
        if not companies:
            continue
        recs.append({
            "title": f"Post about {companies[0]} or related — activate `{t['id']}`",
            "rationale": f"Active thread `{t['id']}` ({t.get('title')}) has had no "
                         f"engagement on your posts recently. People in this thread or at "
                         f"these companies could re-surface if you post on-topic.",
            "would_warm": t.get("people") or [],
            "active_thread_ids": [t["id"]],
            "impact": "high" if t.get("boost_for_brief") == "high" else "medium",
        })

    # 3. Topics that engage your highest-DRR contacts (do more of what works)
    for topic_row in overlay["topic_engagement_map"][:3]:
        recs.append({
            "title": f"Lean into `{topic_row['topic']}` — your strongest engagement topic",
            "rationale": f"Topic accumulated {topic_row['total_engagement_score']} engagement points "
                         f"across {len(topic_row['top_engagers'])} of your top engagers.",
            "would_warm": [e["id"] for e in topic_row["top_engagers"]],
            "active_thread_ids": [],
            "impact": "medium",
        })

    impact_order = {"high": 0, "medium": 1, "low": 2}
    recs.sort(key=lambda r: impact_order[r["impact"]])
    return recs


# -----------------------------------------------------------------------------
# Active threads
# -----------------------------------------------------------------------------

def _yaml_load(text: str) -> dict:
    """Prefer PyYAML if installed (recommended). Fall back to a tiny parser
    that handles the shape of active_threads.yaml only — narrow but
    dependency-free.
    """
    try:
        import yaml  # type: ignore
        return yaml.safe_load(text) or {}
    except ImportError:
        pass
    return _tiny_yaml_parse(text)


def _tiny_yaml_parse(text: str) -> dict:
    """Fallback minimal YAML reader. Used only if PyYAML isn't installed.
    Supports the limited shape active_threads.yaml uses; does not strip
    inline comments from list items. Install PyYAML to avoid this path."""
    lines = text.splitlines()
    out: dict = {}
    i = 0

    def parse_block(indent: int, idx: int):
        """Return (parsed_value, idx) for a block whose entries start at `indent`."""
        # peek to decide list vs map
        while idx < len(lines) and (not lines[idx].strip() or lines[idx].lstrip().startswith("#")):
            idx += 1
        if idx >= len(lines):
            return None, idx
        if lines[idx][indent:indent+2] == "- ":
            return parse_list(indent, idx)
        return parse_map(indent, idx)

    def parse_list(indent: int, idx: int):
        result = []
        while idx < len(lines):
            line = lines[idx]
            if not line.strip() or line.lstrip().startswith("#"):
                idx += 1
                continue
            stripped = line[indent:]
            if not stripped.startswith("- "):
                break
            # Item begins
            item_first = stripped[2:]
            item: dict | str
            if ":" in item_first and not item_first.startswith("|"):
                # Object item
                key, _, val = item_first.partition(":")
                item = {}
                val = val.strip()
                if val == "|":
                    # multi-line
                    paragraph, idx = read_block_scalar(indent + 4, idx + 1)
                    item[key.strip()] = paragraph
                elif val == "":
                    # nested structure under this field
                    nested, idx = parse_block(indent + 4, idx + 1)
                    item[key.strip()] = nested if nested is not None else []
                else:
                    item[key.strip()] = parse_scalar(val)
                    idx += 1
                # Continue reading further fields at indent+2
                while idx < len(lines):
                    l = lines[idx]
                    if not l.strip() or l.lstrip().startswith("#"):
                        idx += 1
                        continue
                    if not l.startswith(" " * (indent + 2)):
                        break
                    if l[indent + 2:].startswith("- "):
                        # next list item at outer level handled by outer loop
                        break
                    field_line = l[indent + 2:]
                    if ":" not in field_line:
                        break
                    fk, _, fv = field_line.partition(":")
                    fv = fv.strip()
                    if fv == "|":
                        # For a field inside a list item, the block scalar body is
                        # indented two spaces beyond the field line. The list item
                        # itself starts at `indent`; fields are at `indent + 2`,
                        # so scalar body lines are at `indent + 4`.
                        paragraph, idx = read_block_scalar(indent + 4, idx + 1)
                        item[fk.strip()] = paragraph
                    elif fv == "":
                        nested, idx = parse_block(indent + 4, idx + 1)
                        item[fk.strip()] = nested if nested is not None else []
                    else:
                        item[fk.strip()] = parse_scalar(fv)
                        idx += 1
                result.append(item)
            else:
                # Scalar list item
                result.append(parse_scalar(item_first.strip()))
                idx += 1
        return result, idx

    def parse_map(indent: int, idx: int):
        result: dict = {}
        while idx < len(lines):
            line = lines[idx]
            if not line.strip() or line.lstrip().startswith("#"):
                idx += 1
                continue
            if not line.startswith(" " * indent):
                break
            field_line = line[indent:]
            if ":" not in field_line:
                break
            fk, _, fv = field_line.partition(":")
            fv = fv.strip()
            if fv == "|":
                paragraph, idx = read_block_scalar(indent + 2, idx + 1)
                result[fk.strip()] = paragraph
            elif fv == "":
                nested, idx = parse_block(indent + 2, idx + 1)
                result[fk.strip()] = nested if nested is not None else []
            else:
                result[fk.strip()] = parse_scalar(fv)
                idx += 1
        return result, idx

    def read_block_scalar(child_indent: int, idx: int):
        """Read a |-style block scalar."""
        buf: list[str] = []
        while idx < len(lines):
            line = lines[idx]
            if line.startswith(" " * child_indent):
                buf.append(line[child_indent:])
                idx += 1
            elif line.strip() == "":
                buf.append("")
                idx += 1
            else:
                break
        # Trim trailing empties
        while buf and buf[-1] == "":
            buf.pop()
        return "\n".join(buf), idx

    def parse_scalar(s: str):
        s = s.strip()
        if s.startswith("[") and s.endswith("]"):
            inner = s[1:-1].strip()
            return [] if not inner else [p.strip() for p in inner.split(",")]
        if s.lower() in ("true", "false"):
            return s.lower() == "true"
        try:
            if "." in s:
                return float(s)
            return int(s)
        except ValueError:
            return s

    # Top-level
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("#"):
            i += 1
            continue
        if ":" not in line:
            i += 1
            continue
        k, _, v = line.partition(":")
        v = v.strip()
        if v == "|":
            paragraph, i = read_block_scalar(2, i + 1)
            out[k.strip()] = paragraph
        elif v == "":
            nested, i = parse_block(2, i + 1)
            out[k.strip()] = nested if nested is not None else []
        else:
            out[k.strip()] = parse_scalar(v)
            i += 1
    return out


def load_active_threads(path: Path = ACTIVE_THREADS_PATH) -> list[dict]:
    """Return the list of thread dicts as written to disk."""
    if not path.exists():
        return []
    data = _yaml_load(path.read_text())
    return list(data.get("threads") or [])


def load_strategic_operators(path: Path = STRATEGIC_OPERATORS_PATH) -> list[dict]:
    """Return the list of operator dicts from strategic_operators.yaml.

    Empty list when the file doesn't exist (pre-RB-9.1 installs). Dates that
    PyYAML auto-coerces to datetime.date objects are converted back to ISO
    strings so downstream consumers don't have to handle both shapes.
    """
    if not path.exists():
        return []
    data = _yaml_load(path.read_text())
    operators = list(data.get("operators") or [])

    from datetime import date as _date, datetime as _datetime

    def _stringify(o):
        if isinstance(o, _datetime):
            return o.isoformat()
        if isinstance(o, _date):
            return o.isoformat()
        if isinstance(o, dict):
            return {k: _stringify(v) for k, v in o.items()}
        if isinstance(o, list):
            return [_stringify(v) for v in o]
        return o

    return [_stringify(op) for op in operators]


def thread_boost_for(entry: dict, threads: list[dict] | None = None) -> tuple[float, list[str]]:
    """Return (boost_multiplier, [thread_ids_that_apply]).

    A thread applies to an entry if:
      - the entry's id appears in thread['people'], OR
      - the entry's current_company appears in thread['companies'].

    If multiple threads apply, take the MAX of their boost_score (do not stack).
    A boost of 1.0 means no change. Cap at 1.5.
    """
    if threads is None:
        threads = [t for t in load_active_threads() if t.get("status") == "open"]
    applied: list[str] = []
    boost = 1.0
    eid = entry.get("id")
    company = (entry.get("current_company") or "").strip()
    for t in threads:
        people = t.get("people") or []
        companies = t.get("companies") or []
        if eid in people or (company and company in companies):
            applied.append(t.get("id"))
            try:
                b = float(t.get("boost_score", 1.0))
            except (ValueError, TypeError):
                b = 1.0
            if b > boost:
                boost = b
    return min(boost, 1.5), applied


# -----------------------------------------------------------------------------
# DRR scoring prototype
# -----------------------------------------------------------------------------

def drr_score(entry: dict, today: date, threads: list[dict] | None = None) -> dict:
    """Dynamic Relationship Relevance — prototype.

    Component signals (each normalized 0–1 then weighted):
        recency        — how recently touched (last_touch closeness to today).
        tier_weight    — inner > broader > dormant_valuable > LKI > LMI > VC.
        circle_count   — number of Circles this entry sits inside.
        completeness   — fraction of the {email, phone, current_company, last_touch} set populated.
        evidence_depth — number of `sources` entries (proxy for evidence breadth).

    After the weighted sum, the score is multiplied by the active-thread boost
    (1.0 = no active thread touches this entry; up to 1.5 cap when one or more
    open threads name this entry by id or company).

    Output: a 0–100 score and the component breakdown for explainability,
    including the matched_threads list.
    """
    weights = {
        "recency": 0.30,
        "tier": 0.25,
        "circles": 0.15,
        "completeness": 0.15,
        "evidence": 0.15,
    }

    # recency
    lt = entry.get("last_touch")
    if lt:
        days = (today - date.fromisoformat(lt)).days
        # 0 days -> 1.0; 365+ days -> 0.0; linear
        recency = max(0.0, 1.0 - days / 365.0)
    else:
        recency = 0.0

    # tier weight
    sc = entry.get("signal_class")
    tier = entry.get("rc_tier")
    tier_map = {
        ("RC", "inner"): 1.00,
        ("RC", "broader"): 0.85,
        ("RC", "dormant_valuable"): 0.70,
        ("LKI", None): 0.55,
        ("LMI", None): 0.30,
        ("NPR", None): 0.10,
        ("VC", None): 0.05,
    }
    tier_weight = tier_map.get((sc, tier), 0.0)
    if sc == "RC" and tier_weight == 0.0:
        tier_weight = 0.70  # fall-through for any RC

    # circle count: 0 -> 0.0, 1 -> 0.5, 2 -> 0.8, 3+ -> 1.0
    cc = len(entry.get("circles") or [])
    circles = {0: 0.0, 1: 0.5, 2: 0.8}.get(cc, 1.0)

    # completeness
    fields = ["email", "phone", "current_company", "last_touch"]
    filled = sum(1 for f in fields if entry.get(f))
    completeness = filled / len(fields)

    # evidence_depth
    src = len(entry.get("sources") or [])
    evidence = min(1.0, src / 4.0)  # 4+ sources saturates

    raw = (
        weights["recency"] * recency
        + weights["tier"] * tier_weight
        + weights["circles"] * circles
        + weights["completeness"] * completeness
        + weights["evidence"] * evidence
    )

    boost, matched_threads = thread_boost_for(entry, threads)
    scored = raw * boost
    # Cap at 1.0 after boost — keeps the 0–100 ceiling honest.
    scored = min(scored, 1.0)

    return {
        "id": entry.get("id"),
        "name": entry.get("name"),
        "signal_class": sc,
        "rc_tier": tier,
        "score": round(scored * 100, 1),
        "base_score": round(raw * 100, 1),
        "thread_boost": round(boost, 3),
        "matched_threads": matched_threads,
        "components": {
            "recency": round(recency, 3),
            "tier_weight": round(tier_weight, 3),
            "circles": round(circles, 3),
            "completeness": round(completeness, 3),
            "evidence": round(evidence, 3),
        },
    }


# -----------------------------------------------------------------------------
# Intro engine — find broker paths to a target
# -----------------------------------------------------------------------------

# Heuristic suppression rules expressed in code. The narrative lives in
# `system/heuristics.md` — this is the operational encoding. When the markdown
# file gains a new rule, mirror it here.
_HEURISTIC_SUPPRESSED_TARGETS = {
    # If a broker is in any of these circles, they cannot intro to Donnie.
    "donnie-boivin": {"circles": ["success-champions", "hospitality-table"]},
}

# Cluster rules: contacts in any of these circles already know each other,
# so don't propose intros within the cluster.
_HEURISTIC_INTERNAL_CLUSTERS = {
    "hospitality-table",  # community_chapter
    "otp3-leaders",       # community_chapter
    "former-par-employees",  # community_chapter (mostly)
}

# Operator-confirmed already-known pairs (hard-coded fallback; runtime loader
# merges additional pairs parsed from heuristics.md).
_HEURISTIC_KNOWN_PAIRS: set[frozenset] = {
    frozenset(("daran-adair", "jim-taylor")),
    frozenset(("mike-porto", "paul-mccarthy")),
}

HEURISTICS_PATH = SYSTEM_DIR / "heuristics.md"
INTRO_LEDGER_PATH = SYSTEM_DIR / "intro_ledger.yaml"


# ---------------------------------------------------------------------------
# Heuristics loader — parse heuristics.md at runtime
# ---------------------------------------------------------------------------

def _parse_heuristics_known_pairs() -> set[frozenset]:
    """Parse the already-known pairs table in heuristics.md.

    Looks for rows in the markdown table under '## Already-known pairs' and
    converts name pairs to id-style slugs (lower, spaces → hyphens).  Falls
    back gracefully if the file is missing or unparseable.
    """
    import re
    result: set[frozenset] = set()
    if not HEURISTICS_PATH.exists():
        return result
    try:
        text = HEURISTICS_PATH.read_text(encoding="utf-8")
    except OSError:
        return result

    in_table = False
    for line in text.splitlines():
        # Enter the pairs table section
        if re.match(r"^##\s+Already.known pairs", line, re.IGNORECASE):
            in_table = True
            continue
        # Leave on the next ## heading
        if in_table and re.match(r"^##", line):
            break
        if not in_table:
            continue
        # Table row: | Person A | Person B | ... |
        cols = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cols) < 2 or cols[0].lower().startswith("person") or set(cols[0]) <= {"-", " "}:
            continue
        def _to_id(name: str) -> str:
            return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        a, b = _to_id(cols[0]), _to_id(cols[1])
        if a and b and a != b:
            result.add(frozenset((a, b)))
    return result


def _effective_known_pairs() -> set[frozenset]:
    """Merge hard-coded pairs with those parsed from heuristics.md."""
    return _HEURISTIC_KNOWN_PAIRS | _parse_heuristics_known_pairs()


# ---------------------------------------------------------------------------
# Governed intro scorecard helpers
# ---------------------------------------------------------------------------

def _relationship_strength_label(signal_class: str | None,
                                  rc_tier: str | None,
                                  drr: float) -> str:
    """Human-readable relationship strength label for a broker candidate."""
    if signal_class == "RC":
        if rc_tier == "inner":
            return "inner RC — close working relationship"
        return "working RC — active relationship"
    if signal_class == "LKI":
        if drr >= 60:
            return "strong LKI — well-known acquaintance"
        if drr >= 30:
            return "moderate LKI — known acquaintance"
        return "light LKI — professional acquaintance"
    return "acquaintance"


def _trust_maturity(entry: dict, today: date) -> str:
    """Return 'stable' | 'developing' | 'dormant' based on tier + recency."""
    sc = entry.get("signal_class")
    tier = entry.get("rc_tier")
    lt = entry.get("last_touch")
    days_since: int | None = None
    if lt:
        try:
            days_since = (today - date.fromisoformat(lt)).days
        except ValueError:
            pass

    if sc == "RC":
        threshold = TIER_THRESHOLD_DAYS.get(tier or "broader", 90)
        if days_since is None:
            return "developing"
        if days_since <= threshold:
            return "stable"
        if days_since <= threshold * 2:
            return "developing"
        return "dormant"

    if sc == "LKI":
        if days_since is None or days_since > 365:
            return "dormant"
        if days_since <= 180:
            return "developing"
        return "dormant"

    return "dormant"


def _network_equity_risk(entry: dict, drr: float) -> str:
    """Estimate the relational cost of asking this broker for a favour.

    V1: derived from signal_class + rc_tier + DRR.  A future ledger will
    incorporate actual ask history.

    Returns 'low' | 'moderate' | 'high'.
    """
    sc = entry.get("signal_class")
    tier = entry.get("rc_tier")
    if sc == "RC" and tier == "inner":
        return "low"
    if sc == "RC":
        return "low"
    if sc == "LKI":
        if drr >= 60:
            return "moderate"
        if drr >= 30:
            return "moderate"
        return "high"
    return "high"


def _recommended_posture(signal_class: str | None,
                          rc_tier: str | None,
                          has_proximity: bool,
                          trust_maturity: str,
                          network_equity_risk: str) -> str:
    """Recommend one of: 'ask' | 'nurture_first' | 'do_not_ask' | 'direct_outreach'.

    Rules (in priority order):
      1. High equity risk + no proximity → do_not_ask.
      2. Inner RC, stable → ask (most natural).
      3. Working RC, stable, has proximity → ask.
      4. RC, dormant (relationship cooling) → nurture_first.
      5. LKI with proximity, not dormant → nurture_first (warm up first).
      6. LKI with no proximity → do_not_ask.
      7. Target is already inner-tier RC → direct_outreach (caller decides).
    """
    if network_equity_risk == "high" and not has_proximity:
        return "do_not_ask"
    if signal_class == "RC" and rc_tier == "inner" and trust_maturity in ("stable", "developing"):
        return "ask"
    if signal_class == "RC" and trust_maturity == "stable" and has_proximity:
        return "ask"
    if signal_class == "RC" and trust_maturity == "dormant":
        return "nurture_first"
    if signal_class == "RC":
        return "nurture_first"
    if signal_class == "LKI" and has_proximity and trust_maturity != "dormant":
        return "nurture_first"
    if network_equity_risk == "high":
        return "do_not_ask"
    return "nurture_first"


def _draft_ask_message(broker: dict, target_resolved: dict,
                        proximity: dict, trust_maturity: str,
                        domain_explanation: str = "") -> str:
    """Generate a short, voice-appropriate intro ask draft.

    Spec (updated: ROADMAP domain-aware broker scoring + personalized asks):
      - In Todd's voice — direct, human, no sales jargon.
      - States why this broker specifically (domain, history, proximity).
      - References shared context when available (last_touch recency, domain overlap).
      - Easy opt-out.
      - No commission / free-intro-broker framing.
      - 4–6 sentences max.
    """
    from datetime import date as _date
    first = (broker.get("name") or "").split()[0]
    target_name = target_resolved.get("name") or "them"
    broker_company = broker.get("current_company") or ""

    # Relationship recency context
    last_touch_raw = broker.get("last_touch")
    recency_note = ""
    if last_touch_raw:
        try:
            lt = _date.fromisoformat(str(last_touch_raw))
            days_ago = (_date.today() - lt).days
            if days_ago <= 14:
                recency_note = "recent"
            elif days_ago <= 60:
                recency_note = "recent-ish"
        except (ValueError, TypeError):
            pass

    # Warm opener that scales with relationship depth + recency
    if trust_maturity == "stable":
        opener = f"Hey {first} — hope things are going well"
        if broker_company:
            opener += f" at {broker_company}"
        opener += "!"
    elif recency_note == "recent":
        opener = f"Hey {first} — good to talk recently!"
    else:
        opener = f"Hey {first} — hope you're doing well!"

    # Proximity-specific "why you" line — prioritize domain overlap over generic fallback
    prox = proximity
    if prox.get("same_company"):
        company = target_resolved.get("company") or target_resolved.get("name", "")
        why = f"You came to mind because you're at {company} and likely know them."
    elif prox.get("shared_circles"):
        circle = prox["shared_circles"][0].replace("-", " ").title()
        why = f"You came to mind because you're both in the {circle} circle."
    elif prox.get("matches_thread_people"):
        why = "You came to mind because you've been in conversation with them recently."
    elif prox.get("matches_thread_company"):
        company = target_resolved.get("company") or target_resolved.get("name", "")
        why = f"You came to mind because you've been active around {company}."
    elif domain_explanation and "match" in domain_explanation:
        # Domain expertise is the primary reason — make it explicit
        domain_label = domain_explanation.replace("domain match: ", "").replace("_", " ")
        why = f"You came to mind because of your background in {domain_label} — feels like you'd know the right people."
    elif domain_explanation and "adjacent" in domain_explanation:
        why = "You came to mind given your background — feels like there might be a natural connection."
    else:
        why = "You came to mind as someone who might know them or know who does."

    # Context for the target
    if target_resolved.get("type") == "company":
        target_line = f"I'm looking to connect with someone at {target_name}."
    else:
        target_line = f"I'm trying to find a warm path to {target_name}."

    ask = (
        "If you know them and a warm intro feels right, I'd really appreciate it. "
        "No pressure at all if the timing's off or it's not a fit — just wanted to ask."
    )

    closing = "\n\nThanks,\nTodd"

    return f"{opener}\n\n{target_line} {why}\n\n{ask}{closing}"


def _normalize_company(name: str | None) -> str:
    if not name:
        return ""
    return name.strip().lower()


def _resolve_target(target_str: str, baseline: list[dict]) -> dict:
    """Best-effort resolve. Returns a dict:
        { "type": "person"|"company", "id": ..., "name": ..., "company": ..., "person": <entry|None> }
    """
    s = (target_str or "").strip()
    s_low = s.lower()

    # 1. exact id match
    for e in baseline:
        if (e.get("id") or "") == s_low:
            return {
                "type": "person", "id": e["id"], "name": e.get("name"),
                "company": e.get("current_company"), "person": e,
            }
    # 2. exact name match (case-insensitive)
    for e in baseline:
        if (e.get("name") or "").lower() == s_low:
            return {
                "type": "person", "id": e["id"], "name": e.get("name"),
                "company": e.get("current_company"), "person": e,
            }
    # 3. company match — any baseline contact has that current_company.
    # employment_status guard is defense-in-depth alongside the current_company
    # null check: a no_stated_current_role person (RB ended-role cleanup,
    # 2026-08-06 — e.g. Richard Heyman/Scooter's Coffee) must never resolve as
    # an active company insider.
    insiders = [
        e for e in baseline
        if e.get("employment_status") != "no_stated_current_role"
        and _normalize_company(e.get("current_company")) == s_low
    ]
    if insiders:
        return {
            "type": "company", "id": None, "name": insiders[0].get("current_company"),
            "company": insiders[0].get("current_company"), "person": None,
        }
    # 4. loose company token match (first word)
    first_tok = s_low.split()[0] if s_low else ""
    loose = [e for e in baseline
             if e.get("employment_status") != "no_stated_current_role"
             and first_tok and first_tok in _normalize_company(e.get("current_company"))]
    if loose:
        return {
            "type": "company", "id": None, "name": s,
            "company": s, "person": None,
        }
    # 5. fall through — treat as unknown person/company string
    return {"type": "unknown", "id": None, "name": s, "company": None, "person": None}


def _circles_overlap(a: dict, b: dict) -> list[str]:
    """Circles that both a and b are in (membership set intersection)."""
    return sorted(set(a.get("circles") or []) & set(b.get("circles") or []))


# ---------------------------------------------------------------------------
# Domain expertise scoring (ROADMAP: intro engine domain-aware broker scoring)
# ---------------------------------------------------------------------------

# Domain → keyword sets for company names, tags, and circles
_DOMAIN_KEYWORD_MAP: dict[str, set[str]] = {
    "restaurant_tech": {
        "par technology", "par tech", "par ", "toast", "ncr voyix", "ncr ", "aloha",
        "qu pos", "qu ", "xenial", "genius", "revel", "lightspeed", "brink",
        "oracle hospitality", "micros", "digital dining", "positouch", "heartland",
        "revel systems", "shift4", "square", "clover", "harbortouch",
        "restaurant tech", "restaurant technology", "restaurant pos",
        "point of sale", "back office", "kitchen display",
        "mobile insight", "t-roc", "troc ", "revenue optimization",
        "qsrsoft", "qsr soft", "retail data systems", "custom business solutions",
        "mid-america point of sale", "maps", "hopsdrink", "hopskipdrive",
        "espresso ai", "voosh", "ovation", "paytronix", "punchh",
    },
    "mcdonald_ecosystem": {
        "mcdonald", "mcdonalds", "nsn", "national supplier", "otp", "rfm",
        "field office", "co-op", "coop", "franchisee", "mcd-franchise",
        "mcd franchise", "otp3", "otp-level", "golden arches",
    },
    "payments_fintech": {
        "global payments", "gpn", "cayan", "heartland payments", "openedge",
        "payment", "fintech", "merchant services", "acquiring", "issuing",
        "simply goldco", "paya", "nuvei", "worldpay", "fiserv",
    },
    "hospitality_foodservice": {
        "hospitality", "restaurant operator", "foodservice", "hotel", "resort",
        "catering", "franchise operator", "multi-unit", "qsr", "quick service",
        "full service", "fast casual", "hospitality-table", "hospitality table",
        "benchmarksixty", "benchmark sixty",
    },
    "sales_gtm": {
        "sales", "gtm", "go-to-market", "revenue", "crm", "account executive",
        "business development", "bdr", "sdr", "sales leader", "vp sales",
        "chief revenue", "cro", "inc tank", "sales champion", "success champion",
        "connector", "broker",
    },
    "recruiting_hr": {
        "recruiter", "recruiting", "talent", "staffing", "executive search",
        "silver stone", "triton exec", "hr", "human resources", "headhunter",
    },
    "foods_procurement": {
        "foods connected", "foodsconnected", "procurement", "supply chain",
        "food distribution", "food tech", "sysco", "us foods",
    },
}

# Tags and circles that indicate domain membership
_DOMAIN_TAG_MAP: dict[str, set[str]] = {
    "restaurant_tech": {"par-alumni", "former-par-employees", "tech-sales-cs-leaders"},
    "mcdonald_ecosystem": {"mcd-franchise", "otp-level-3", "otp3-leaders"},
    "hospitality_foodservice": {"hospitality-table"},
    "sales_gtm": {"success-champions", "connector"},
}


_ECOSYSTEM_DOMAIN_INDEX_CACHE: dict[str, str] | None = None


def _ecosystem_domain_index() -> dict[str, str]:
    """Lazily build a normalized name/alias -> domain lookup from
    ecosystem_intelligence.json's brand/vendor registry (~1600 entities,
    covering the actual restaurant brands and tech vendors in RB's
    ecosystem graph). Cached at module scope -- the file doesn't change
    within a process run, and reloading it per broker-score call (hundreds
    of calls per intro_engine.py invocation) would be wasteful.

    RB-DEFECT-2026-07-27: _DOMAIN_KEYWORD_MAP only covers vendor/tech
    company names (Toast, PAR Technology, ...) plus a single hardcoded
    "mcdonald" special case -- it has no way to recognize any of the other
    ~1590 restaurant BRAND names in the ecosystem registry (Chipotle,
    Wendy's, Pollo Campero, ...) as belonging to the hospitality/foodservice
    domain at all. Confirmed live: `intro_engine.py "Campero"` -- a real,
    same-day active opportunity (Genius N Lead / Campero) -- returned zero
    domain-aware broker recommendations and fell all the way back to raw
    DRR ranking, because "campero" matches no keyword in any domain list.
    """
    global _ECOSYSTEM_DOMAIN_INDEX_CACHE
    if _ECOSYSTEM_DOMAIN_INDEX_CACHE is not None:
        return _ECOSYSTEM_DOMAIN_INDEX_CACHE
    index: dict[str, str] = {}
    try:
        data = json.loads(ECOSYSTEM_INTELLIGENCE_PATH.read_text(encoding="utf-8"))
        for e in data.get("entities") or []:
            entity_type = e.get("entity_type")
            if entity_type == "brand":
                domain = "hospitality_foodservice"
            elif entity_type == "vendor":
                domain = "restaurant_tech"
            else:
                continue
            for name in [e.get("name", ""), *(e.get("aliases") or [])]:
                key = (name or "").strip().lower()
                if key:
                    index[key] = domain
    except Exception:
        pass
    _ECOSYSTEM_DOMAIN_INDEX_CACHE = index
    return index


def _ecosystem_domain_for_company(company_name: str) -> str | None:
    """Best-effort match of a company name against the ecosystem brand/
    vendor registry. Exact match first, then substring (handles "Campero"
    matching the registry's "Pollo Campero", or a baseline company string
    that includes a suffix like ", Inc.")."""
    needle = (company_name or "").strip().lower()
    if not needle:
        return None
    index = _ecosystem_domain_index()
    if needle in index:
        return index[needle]
    for name, domain in index.items():
        if len(name) >= 4 and (needle in name or name in needle):
            return domain
    return None


def _infer_domain_tags(entry: dict) -> set[str]:
    """Return the domain categories an entry belongs to based on company/tags/circles."""
    domains: set[str] = set()
    company = (entry.get("current_company") or "").lower()
    tags = {t.lower() for t in (entry.get("tags") or [])}
    circles = {c.lower() for c in (entry.get("circles") or [])}
    combined_text = company + " " + " ".join(tags) + " " + " ".join(circles)

    for domain, keywords in _DOMAIN_KEYWORD_MAP.items():
        if any(kw in combined_text for kw in keywords):
            domains.add(domain)

    for domain, marker_tags in _DOMAIN_TAG_MAP.items():
        if tags & marker_tags or circles & marker_tags:
            domains.add(domain)

    eco_domain = _ecosystem_domain_for_company(entry.get("current_company") or "")
    if eco_domain:
        domains.add(eco_domain)

    return domains


def _domain_relevance_bonus(
    broker: dict,
    target_resolved: dict,
    baseline: list[dict],
) -> tuple[int, str]:
    """Return (bonus_points, explanation) for broker domain relevance to target.

    Scoring:
      +12  broker is in the same domain as the target company/entity
      +8   broker is in a domain adjacent to the target (e.g. restaurant_tech → mcdonald_ecosystem)
      +5   broker has a tag or circle matching a target-domain marker
      0    no domain overlap detected
    """
    target_company = (target_resolved.get("company") or "").lower()
    target_person = target_resolved.get("person") or {}
    target_entry_company = (target_person.get("current_company") or target_company or "").lower()

    # Infer target domain from company name
    target_domains: set[str] = set()
    for domain, keywords in _DOMAIN_KEYWORD_MAP.items():
        if any(kw in target_entry_company for kw in keywords):
            target_domains.add(domain)
    eco_domain = _ecosystem_domain_for_company(target_entry_company)
    if eco_domain:
        target_domains.add(eco_domain)
    if not target_domains:
        # Fallback: check target person's tags/circles if available
        target_domains = _infer_domain_tags(target_person) if target_person else set()

    if not target_domains:
        return 0, "target domain unknown"

    broker_domains = _infer_domain_tags(broker)
    if not broker_domains:
        return 0, "broker has no domain signals"

    # Direct overlap
    overlap = target_domains & broker_domains
    if overlap:
        explanation = f"domain match: {', '.join(sorted(overlap))}"
        return 12, explanation

    # Adjacent domain pairs
    _ADJACENT: set[frozenset] = {
        frozenset({"restaurant_tech", "mcdonald_ecosystem"}),
        frozenset({"restaurant_tech", "hospitality_foodservice"}),
        frozenset({"restaurant_tech", "payments_fintech"}),
        frozenset({"mcdonald_ecosystem", "hospitality_foodservice"}),
        frozenset({"sales_gtm", "restaurant_tech"}),
        frozenset({"foods_procurement", "hospitality_foodservice"}),
    }
    for pair in _ADJACENT:
        if target_domains & pair and broker_domains & pair:
            adj_domain = list(broker_domains & pair)[0]
            explanation = f"adjacent domain: {adj_domain}"
            return 8, explanation

    return 0, "no domain overlap"


def _broker_score(
    broker: dict,
    target_resolved: dict,
    baseline: list[dict],
    threads: list[dict],
    today: date,
) -> dict:
    """Score a single candidate broker. Returns a dict with composite score
    plus the component evidence used for explainability.
    """
    drr = drr_score(broker, today, threads)

    # Proximity to target
    proximity = {
        "same_company": False,
        "shared_circles": [],
        "matches_thread_company": False,
        "matches_thread_people": False,
        "is_target_company_insider": False,
    }
    target_person = target_resolved.get("person")
    target_company = _normalize_company(target_resolved.get("company"))
    broker_company = _normalize_company(broker.get("current_company"))

    if target_company and broker_company:
        if target_company == broker_company:
            proximity["same_company"] = True
            proximity["is_target_company_insider"] = (
                target_resolved.get("type") == "company"
            )

    if target_person:
        proximity["shared_circles"] = _circles_overlap(broker, target_person)

    # Active-thread context — broker is more leverageable if they're in a thread
    # whose companies include the target's company
    for t in threads:
        if target_company and any(
            _normalize_company(c) == target_company for c in (t.get("companies") or [])
        ):
            if broker["id"] in (t.get("people") or []):
                proximity["matches_thread_people"] = True
            if broker_company and any(
                _normalize_company(c) == broker_company for c in (t.get("companies") or [])
            ):
                proximity["matches_thread_company"] = True

    # Heuristic checks
    heuristic_flags: list[str] = []

    # Already-known pair suppression (merge hard-coded + heuristics.md)
    if target_person and frozenset((broker["id"], target_person["id"])) in _effective_known_pairs():
        heuristic_flags.append("operator-confirmed already-known pair")

    # Cluster-internal suppression
    if target_person:
        for c in proximity["shared_circles"]:
            if c in _HEURISTIC_INTERNAL_CLUSTERS:
                heuristic_flags.append(f"both in `{c}` Circle — already know each other")

    # Donnie/SCN rule (target-specific)
    if target_person and target_person["id"] in _HEURISTIC_SUPPRESSED_TARGETS:
        rule = _HEURISTIC_SUPPRESSED_TARGETS[target_person["id"]]
        broker_circles = set(broker.get("circles") or [])
        for c in rule.get("circles", []):
            if c in broker_circles:
                heuristic_flags.append(
                    f"target ({target_person['name']}) is already known to everyone in `{c}`"
                )
                break

    # Domain relevance bonus: broker's industry expertise relative to target domain
    domain_bonus, domain_explanation = _domain_relevance_bonus(broker, target_resolved, baseline)

    # Composite score: DRR + proximity + thread context + domain expertise
    composite = drr["score"]
    if proximity["same_company"]:
        composite += 10
    composite += len(proximity["shared_circles"]) * 5
    if proximity["matches_thread_company"]:
        composite += 8
    if proximity["matches_thread_people"]:
        composite += 5
    composite += domain_bonus

    suppressed = bool(heuristic_flags)

    # --- Governed scorecard (added in RB 9.10) ---
    sc = broker.get("signal_class")
    tier = broker.get("rc_tier")
    has_prox = (
        proximity["same_company"]
        or bool(proximity["shared_circles"])
        or proximity["matches_thread_company"]
        or proximity["matches_thread_people"]
    )
    tm = _trust_maturity(broker, today)
    ner = _network_equity_risk(broker, drr["score"])
    posture = _recommended_posture(sc, tier, has_prox, tm, ner)
    strength = _relationship_strength_label(sc, tier, drr["score"])
    draft = _draft_ask_message(broker, target_resolved, proximity, tm,
                               domain_explanation=domain_explanation)

    return {
        "id": broker["id"],
        "name": broker["name"],
        "signal_class": sc,
        "rc_tier": tier,
        "current_company": broker.get("current_company"),
        "drr_score": drr["score"],
        "proximity": proximity,
        "heuristic_flags": heuristic_flags,
        "composite_score": round(composite, 1),
        "suppressed": suppressed,
        # Domain expertise scoring (ROADMAP: domain-aware broker scoring)
        "domain_bonus": domain_bonus,
        "domain_explanation": domain_explanation,
        "broker_domains": sorted(_infer_domain_tags(broker)),
        # Governed scorecard fields
        "scorecard": {
            "relationship_strength": strength,
            "trust_maturity": tm,
            "network_equity_risk": ner,
            "reciprocity_balance": 0,      # V1 placeholder — ledger not yet populated
            "recent_broker_asks": 0,        # V1 placeholder — ledger not yet populated
            "recommended_posture": posture,
        },
        "draft_ask": draft,
    }


def _reason_line(scored: dict, target_resolved: dict) -> str:
    """One-line, voice-appropriate reason for why this broker is on the list."""
    parts = []
    sc = scored.get("signal_class")
    tier = scored.get("rc_tier")
    if sc == "RC":
        parts.append(f"{tier}-tier RC (DRR {scored['drr_score']})")
    else:
        parts.append(f"{sc} (DRR {scored['drr_score']})")
    prox = scored["proximity"]
    if prox["same_company"]:
        parts.append(f"works at {scored.get('current_company')}")
    if prox["shared_circles"]:
        parts.append(f"shares Circles {prox['shared_circles']}")
    if prox["matches_thread_company"]:
        parts.append("their company is named in an active thread")
    if prox["matches_thread_people"]:
        parts.append("named in an active thread alongside target")
    # Domain expertise (ROADMAP: domain-aware broker scoring)
    domain_exp = scored.get("domain_explanation") or ""
    if scored.get("domain_bonus", 0) > 0 and domain_exp:
        parts.append(f"domain expertise: {domain_exp}")
    return "; ".join(parts) + "."


def find_intro_paths(target: str, limit: int = 3,
                     baseline: list[dict] | None = None,
                     threads: list[dict] | None = None,
                     today: date | None = None,
                     include_suppressed: bool = False) -> dict:
    """Find candidate broker paths to reach `target`.

    Args:
        target: person id, person name, or company name.
        limit: max number of broker paths to return.
        baseline / threads / today: optional overrides (default: load from disk + system date).
        include_suppressed: include heuristic-suppressed candidates in output.

    Returns a dict with:
        target            — the input string
        target_resolved   — what we resolved the target to (person/company/unknown)
        insiders          — baseline contacts at the target company
        candidate_brokers — ranked top-N broker candidates (composite-sorted)
        suppressed        — heuristic-blocked candidates (always emitted, info-only)
        notes             — short notes about the result, for the session to surface
    """
    if baseline is None:
        baseline = load_baseline()
    if threads is None:
        threads = [t for t in load_active_threads() if t.get("status") == "open"]
    if today is None:
        today = date.today()

    resolved = _resolve_target(target, baseline)
    notes: list[str] = []
    if resolved["type"] == "unknown":
        notes.append(
            f"Couldn't resolve {target!r} to a person or company in baseline. "
            "Treating as a free-text target — broker scoring will rely on "
            "active threads only."
        )

    # Insiders: baseline contacts at the target company
    target_company = _normalize_company(resolved.get("company"))
    insiders = []
    if target_company:
        for e in baseline:
            if _normalize_company(e.get("current_company")) == target_company:
                # Don't include the target person themselves
                if resolved.get("person") and e["id"] == resolved["person"]["id"]:
                    continue
                insiders.append({
                    "id": e["id"], "name": e["name"],
                    "signal_class": e.get("signal_class"),
                    "rc_tier": e.get("rc_tier"),
                    "drr_score": drr_score(e, today, threads)["score"],
                })
        insiders.sort(key=lambda r: r["drr_score"], reverse=True)

    # Candidate brokers: every non-target baseline entry
    target_id = (resolved.get("person") or {}).get("id")
    candidates = []
    for e in baseline:
        if e["id"] == target_id:
            continue
        # Skip very-low-signal candidates that won't be useful brokers
        if e.get("signal_class") == "VC":
            continue
        scored = _broker_score(e, resolved, baseline, threads, today)
        # Classify: does this broker have ANY proximity signal to the target?
        prox = scored["proximity"]
        scored["has_proximity"] = (
            prox["same_company"]
            or bool(prox["shared_circles"])
            or prox["matches_thread_company"]
            or prox["matches_thread_people"]
        )
        candidates.append(scored)

    # Sort: (1) not suppressed, (2) has_proximity, (3) composite_score.
    # That way relevant brokers always outrank raw-DRR-high-but-unrelated contacts.
    candidates.sort(
        key=lambda r: (not r["suppressed"], r["has_proximity"], r["composite_score"]),
        reverse=True,
    )
    active = [c for c in candidates if not c["suppressed"]]
    suppressed = [c for c in candidates if c["suppressed"]]

    top = active[: limit]
    for c in top:
        c["reason"] = _reason_line(c, resolved)

    # If none of the top brokers have proximity, surface that
    if top and not any(c["has_proximity"] for c in top):
        notes.append(
            "No broker candidate has a direct proximity signal to the target "
            "(no shared company, shared Circle, or active-thread overlap). The "
            "list below is highest-DRR contacts who could plausibly be asked "
            "to ask around — treat as warm-introducer candidates, not subject-matter intros."
        )

    if resolved["type"] == "company" and not insiders:
        notes.append(
            f"No baseline contacts at {resolved.get('company')!r}. This is a "
            "network gap — see `network_gap.py` for cluster anchor scoring."
        )
    if resolved["type"] == "person" and resolved.get("person"):
        p = resolved["person"]
        if p.get("signal_class") == "RC" and p.get("rc_tier") == "inner":
            notes.append(
                f"{p['name']} is already an inner-tier RC. You may not need a "
                "broker — direct outreach is the default for inner-tier."
            )

    # --- Persistence template (RB 9.10) ---
    # One template per top broker — caller picks one and confirms before writing.
    persistence_templates = []
    for c in top:
        if not c.get("suppressed"):
            persistence_templates.append({
                "loop_type": "intro_request_pending",
                "action_type": "intro_outreach",
                "requires_confirmation": True,
                "broker_id": c["id"],
                "broker_name": c["name"],
                "target": target,
                "target_resolved_type": resolved["type"],
                "recommended_posture": c.get("scorecard", {}).get("recommended_posture", "nurture_first"),
                "draft_ask_preview": (c.get("draft_ask") or "")[:120] + "…",
                "note": (
                    "Proposed intro path from intro_engine.py. "
                    "Confirm before writing to loop_ledger."
                ),
            })

    return {
        "target": target,
        "target_resolved": resolved,
        "insiders": insiders,
        "candidate_brokers": top,
        "suppressed": suppressed if include_suppressed else [],
        "suppressed_count": len(suppressed),
        "notes": notes,
        "persistence_templates": persistence_templates,
    }


# -----------------------------------------------------------------------------
# Network analysis — strategic report card
# -----------------------------------------------------------------------------

# Reference targets for composition health, loosely tied to Dunbar layers.
# These are recommended bands, not hard rules. Operator intuition + the
# nature of the work always overrides.
DUNBAR_TARGETS = {
    "inner_rc_band": (12, 25),   # inner-tier RCs — your close working circle
    "total_rc_band": (15, 50),   # all active RCs — the people you really know
    "lki_band": (50, 250),       # named acquaintances — the wider professional layer
}


def strengths_analysis(baseline: list[dict], today: date,
                       threads: list[dict] | None = None,
                       min_cluster_size: int = 3) -> list[dict]:
    """Rank company clusters by relationship depth.

    A cluster is strong when it has:
        - At least one inner-tier RC anchor (binary, large effect)
        - High LKI+ contact count
        - High average DRR across LKI+ contacts
        - Recent activity (members touched recently)
    """
    if threads is None:
        threads = [t for t in load_active_threads() if t.get("status") == "open"]

    clusters: dict[str, list[dict]] = {}
    for e in baseline:
        company = (e.get("current_company") or "").strip()
        if not company:
            continue
        clusters.setdefault(company, []).append(e)

    rows = []
    for company, members in clusters.items():
        lki_plus = [m for m in members if m.get("signal_class") in ("RC", "LKI")]
        if len(lki_plus) < min_cluster_size:
            continue
        inner_rcs = [m for m in members if m.get("signal_class") == "RC"
                     and m.get("rc_tier") == "inner"
                     and m.get("rc_state") == "ACTIVE"]
        any_rcs = [m for m in members if m.get("signal_class") == "RC"]
        # Average DRR across LKI+ contacts (use base score, not boosted, so threads don't inflate strength)
        drr_scores = [drr_score(m, today, threads)["base_score"] for m in lki_plus]
        avg_drr = round(sum(drr_scores) / len(drr_scores), 1) if drr_scores else 0
        # Recency: % of inner RCs inside their tier threshold
        recent_inner = 0
        for m in inner_rcs:
            lt = m.get("last_touch")
            if lt:
                try:
                    if (today - date.fromisoformat(lt)).days <= TIER_THRESHOLD_DAYS["inner"]:
                        recent_inner += 1
                except ValueError:
                    pass
        recent_pct = round(100 * recent_inner / len(inner_rcs), 1) if inner_rcs else 0

        # Composite strength: anchor binary (huge), then count + avg_drr + recency
        strength = (
            (100 if inner_rcs else 0)
            + len(lki_plus) * 2
            + avg_drr * 0.4
            + recent_pct * 0.2
        )

        rows.append({
            "company": company,
            "total_members": len(members),
            "lki_plus_count": len(lki_plus),
            "rc_count": len(any_rcs),
            "inner_rc_count": len(inner_rcs),
            "inner_rcs": [m["name"] for m in inner_rcs],
            "avg_drr_base": avg_drr,
            "recent_inner_pct": recent_pct,
            "strength_score": round(strength, 1),
        })

    rows.sort(key=lambda r: r["strength_score"], reverse=True)
    return rows


def weaknesses_analysis(baseline: list[dict], today: date) -> dict:
    """Surface multiple weakness types:
        - unanchored_clusters: from cluster_inner_anchor_score
        - missing_cards: RCs without card files
        - rcs_without_last_touch: dormancy-engine-invisible RCs
        - contact_field_gaps: RCs missing email/phone
        - cooling_clusters: clusters where most members are past threshold
    """
    anchor_rows = cluster_inner_anchor_score(baseline)
    unanchored = [r for r in anchor_rows if r["anchor_gap"] and r["lki_count"] >= 1]
    unanchored.sort(key=lambda r: r["gap_score"], reverse=True)

    cooling_clusters: list[dict] = []
    # For each cluster with >=2 RCs, compute the % past their threshold
    by_company: dict[str, list[dict]] = {}
    for e in baseline:
        company = (e.get("current_company") or "").strip()
        if not company:
            continue
        by_company.setdefault(company, []).append(e)
    for company, members in by_company.items():
        rcs = [m for m in members if m.get("signal_class") == "RC"
               and m.get("rc_state") == "ACTIVE"]
        if len(rcs) < 2:
            continue
        cooling = 0
        for m in rcs:
            tier = m.get("rc_tier") or ""
            threshold = TIER_THRESHOLD_DAYS.get(tier)
            lt = m.get("last_touch")
            if threshold is None or not lt:
                continue
            try:
                if (today - date.fromisoformat(lt)).days > threshold:
                    cooling += 1
            except ValueError:
                pass
        if cooling >= len(rcs) // 2 + 1:
            cooling_clusters.append({
                "company": company,
                "rcs": len(rcs),
                "cooling": cooling,
                "cooling_pct": round(100 * cooling / len(rcs), 1),
            })
    cooling_clusters.sort(key=lambda r: r["cooling_pct"], reverse=True)

    return {
        "unanchored_clusters": unanchored[:10],
        "missing_cards": rcs_without_cards(baseline),
        "rcs_without_last_touch": [
            {"id": e["id"], "name": e["name"], "tier": e.get("rc_tier")}
            for e in baseline
            if e.get("signal_class") == "RC" and not e.get("last_touch")
        ],
        "contact_field_gaps": contact_field_gaps(baseline),
        "cooling_clusters": cooling_clusters[:8],
    }


def bridges_analysis(baseline: list[dict]) -> list[dict]:
    """Granovetter weak-tie bridges: contacts who span clusters that wouldn't
    otherwise connect.

    V0 definition: contacts in 2+ Circles. Members of multiple Circles bridge
    those Circles by being in both. Inner-tier RCs in multiple Circles are
    the highest-leverage bridges.
    """
    rows = []
    for e in baseline:
        circles = e.get("circles") or []
        if len(circles) < 2:
            continue
        if e.get("signal_class") not in ("RC", "LKI"):
            continue
        rows.append({
            "id": e["id"],
            "name": e["name"],
            "signal_class": e.get("signal_class"),
            "rc_tier": e.get("rc_tier"),
            "current_company": e.get("current_company"),
            "circles": sorted(circles),
            "bridge_span": len(circles),
        })
    # Sort: inner-tier first, then by span
    tier_order = {"inner": 0, "broader": 1, "dormant_valuable": 2}
    rows.sort(key=lambda r: (
        0 if r["signal_class"] == "RC" else 1,
        tier_order.get(r["rc_tier"], 9),
        -r["bridge_span"],
    ))
    return rows


def composition_health(baseline: list[dict], today: date) -> dict:
    """Composition health snapshot. Counts + ideal-band comparisons."""
    inner_rcs = [e for e in baseline if e.get("signal_class") == "RC"
                 and e.get("rc_tier") == "inner"
                 and e.get("rc_state") == "ACTIVE"]
    all_active_rcs = [e for e in baseline if e.get("signal_class") == "RC"
                      and e.get("rc_state") == "ACTIVE"]
    lkis = [e for e in baseline if e.get("signal_class") == "LKI"]

    have_cards_set = list_card_ids()

    # Recency by tier
    in_window = 0
    for e in inner_rcs:
        lt = e.get("last_touch")
        if lt:
            try:
                if (today - date.fromisoformat(lt)).days <= TIER_THRESHOLD_DAYS["inner"]:
                    in_window += 1
            except ValueError:
                pass

    inner_lo, inner_hi = DUNBAR_TARGETS["inner_rc_band"]
    rc_lo, rc_hi = DUNBAR_TARGETS["total_rc_band"]
    lki_lo, lki_hi = DUNBAR_TARGETS["lki_band"]

    def band_state(n, lo, hi):
        if n < lo:
            return "below"
        if n > hi:
            return "above"
        return "within"

    return {
        "inner_rc_count": len(inner_rcs),
        "inner_rc_band": (inner_lo, inner_hi),
        "inner_rc_band_state": band_state(len(inner_rcs), inner_lo, inner_hi),
        "active_rc_count": len(all_active_rcs),
        "active_rc_band": (rc_lo, rc_hi),
        "active_rc_band_state": band_state(len(all_active_rcs), rc_lo, rc_hi),
        "lki_count": len(lkis),
        "lki_band": (lki_lo, lki_hi),
        "lki_band_state": band_state(len(lkis), lki_lo, lki_hi),
        "card_coverage_pct": round(100 * len([e for e in all_active_rcs if e["id"] in have_cards_set]) / len(all_active_rcs), 1) if all_active_rcs else 0,
        "inner_in_window_pct": round(100 * in_window / len(inner_rcs), 1) if inner_rcs else 0,
        "rcs_without_last_touch_count": sum(1 for e in all_active_rcs if not e.get("last_touch")),
    }


def diversity_analysis(baseline: list[dict]) -> dict:
    """Cluster distribution + concentration check."""
    company_counts: dict[str, int] = {}
    for e in baseline:
        if e.get("signal_class") not in ("RC", "LKI"):
            continue
        company = (e.get("current_company") or "").strip()
        if not company:
            continue
        company_counts[company] = company_counts.get(company, 0) + 1
    total = sum(company_counts.values())
    sorted_clusters = sorted(company_counts.items(), key=lambda kv: kv[1], reverse=True)
    top_clusters = [
        {"company": c, "count": n, "pct_of_lki_plus": round(100 * n / total, 1) if total else 0}
        for c, n in sorted_clusters[:10]
    ]
    clusters_3_plus = sum(1 for _, n in sorted_clusters if n >= 3)
    median = sorted_clusters[len(sorted_clusters) // 2][1] if sorted_clusters else 0

    over_concentration = None
    if top_clusters and top_clusters[0]["pct_of_lki_plus"] > 30:
        over_concentration = top_clusters[0]["company"]

    return {
        "total_lki_plus": total,
        "top_clusters": top_clusters,
        "clusters_with_3_plus_members": clusters_3_plus,
        "median_cluster_size": median,
        "over_concentration": over_concentration,
    }


def recommendations(strengths: list[dict], weaknesses: dict,
                    bridges: list[dict], composition: dict,
                    diversity: dict, threads: list[dict]) -> list[dict]:
    """Synthesize all the signal layers into ranked next-move recommendations.

    Each recommendation has:
        title         — one-line action
        impact        — high | medium | low
        effort        — low | medium | high
        reason        — citation back to which signal layer surfaced this
        target        — the named entity the action is on, if any
    """
    recs: list[dict] = []

    # 1. Promotion candidates from unanchored clusters
    for u in weaknesses["unanchored_clusters"][:5]:
        if u["lki_count"] >= 1:
            recs.append({
                "title": f"Promote top-DRR LKI in `{u['company']}` to fill anchor gap",
                "impact": "high",
                "effort": "medium",
                "reason": f"`{u['company']}` cluster size {u['total']}, {u['lki_count']} LKI, 0 inner RC — gap_score {u['gap_score']}.",
                "target": u["company"],
                "source": "weaknesses_analysis.unanchored_clusters",
            })

    # 2. RCs without last_touch — invisible to dormancy engine
    for r in weaknesses["rcs_without_last_touch"]:
        recs.append({
            "title": f"Add `last_touch` for {r['name']}",
            "impact": "high",
            "effort": "low",
            "reason": f"{r['tier']}-tier RC currently invisible to dormancy engine. Even an approximate month closes the gap.",
            "target": r["id"],
            "source": "weaknesses_analysis.rcs_without_last_touch",
        })

    # 3. Missing RC cards
    for c in weaknesses["missing_cards"][:5]:
        recs.append({
            "title": f"Write RC card for {c['name']}",
            "impact": "medium" if c["tier"] == "inner" else "low",
            "effort": "medium",
            "reason": f"{c['tier']}-tier RC has no card. Card anchors narrative arc, leverage, lingering, risks.",
            "target": c["id"],
            "source": "weaknesses_analysis.missing_cards",
        })

    # 4. Cooling clusters
    for cc in weaknesses["cooling_clusters"][:3]:
        recs.append({
            "title": f"Activate cooling cluster `{cc['company']}` ({cc['cooling']}/{cc['rcs']} RCs past threshold)",
            "impact": "medium",
            "effort": "high",
            "reason": f"{cc['cooling_pct']}% of RCs in this cluster are past their dormancy threshold.",
            "target": cc["company"],
            "source": "weaknesses_analysis.cooling_clusters",
        })

    # 5. Strategic gaps from composition
    if composition["inner_rc_band_state"] == "below":
        recs.append({
            "title": f"Cultivate more inner-tier RCs (current: {composition['inner_rc_count']}, target band: {composition['inner_rc_band'][0]}-{composition['inner_rc_band'][1]})",
            "impact": "high",
            "effort": "high",
            "reason": "Inner-tier count below the recommended band. The system's leverage depends on this layer.",
            "target": None,
            "source": "composition_health",
        })

    # 6. Over-concentration warning
    if diversity["over_concentration"]:
        recs.append({
            "title": f"Diversify away from over-concentration in `{diversity['over_concentration']}`",
            "impact": "medium",
            "effort": "high",
            "reason": f"`{diversity['over_concentration']}` represents >30% of your LKI+ network. Strategic risk if that cluster turns cold.",
            "target": diversity["over_concentration"],
            "source": "diversity_analysis",
        })

    # Rank: impact desc, effort asc
    impact_order = {"high": 0, "medium": 1, "low": 2}
    effort_order = {"low": 0, "medium": 1, "high": 2}
    recs.sort(key=lambda r: (impact_order[r["impact"]], effort_order[r["effort"]]))
    return recs


def network_analysis(baseline: list[dict] | None = None,
                     today: date | None = None,
                     threads: list[dict] | None = None) -> dict:
    """Run the full personal network analysis. Returns a structured dict
    suitable for rendering or for downstream consumers."""
    if baseline is None:
        baseline = load_baseline()
    if today is None:
        today = date.today()
    if threads is None:
        threads = [t for t in load_active_threads() if t.get("status") == "open"]

    strengths = strengths_analysis(baseline, today, threads)
    weaknesses = weaknesses_analysis(baseline, today)
    bridges = bridges_analysis(baseline)
    composition = composition_health(baseline, today)
    diversity = diversity_analysis(baseline)
    recs = recommendations(strengths, weaknesses, bridges, composition, diversity, threads)

    return {
        "as_of": today.isoformat(),
        "baseline_total": len(baseline),
        "strengths": strengths,
        "weaknesses": weaknesses,
        "bridges": bridges,
        "composition_health": composition,
        "diversity": diversity,
        "active_threads_count": len(threads),
        "recommendations": recs,
    }


# -----------------------------------------------------------------------------
# Aggregate snapshot
# -----------------------------------------------------------------------------

def baseline_summary(baseline: list[dict]) -> dict:
    sig = Counter(e.get("signal_class") for e in baseline)
    rc_by_tier = Counter(
        e.get("rc_tier") for e in baseline if e.get("signal_class") == "RC"
    )
    rc_by_state = Counter(
        e.get("rc_state") for e in baseline if e.get("signal_class") == "RC"
    )
    return {
        "total": len(baseline),
        "by_signal_class": dict(sig),
        "rc_by_tier": dict(rc_by_tier),
        "rc_by_state": dict(rc_by_state),
    }


def circle_summary() -> list[dict]:
    out = []
    for p in list_circle_files():
        fm = parse_circle_frontmatter(p)
        out.append({
            "id": fm.get("id", p.stem),
            "name": fm.get("name", p.stem),
            "circle_type": fm.get("circle_type", "unknown"),
            "target_size": fm.get("target_size"),
            "status": fm.get("status"),
            "path": str(p.relative_to(PROJECT_DIR)),
        })
    return out


# ---------------------------------------------------------------------------
# World/National headline scope classification — shared by daily_brief.py
# (which caps world_national_headlines before rendering) and
# render_intelligence_brief.py (which splits the same pool into Section A
# World / Section B National at render time). Both must agree on scope or
# the cap can starve one of the two downstream sections.
# ---------------------------------------------------------------------------

# RB-DEFECT-2026-07-20: daily_brief.py and render_intelligence_brief.py each
# independently defined their own _CORPORATE_EVENT_MAX_AGE_DAYS constant and
# _is_fresh*/_is_fresh_headline function implementing the same "7-day base
# window, 21-day ceiling for corporate events" rule -- two copies of the same
# number and the same day-math, already free to drift out of sync with each
# other exactly like the dedup logic did before it was found and merged.
# Corporate events (M&A, funding, earnings, exec moves) get a longer
# freshness window than ordinary headlines since they're "worth knowing even
# if delayed a bit" -- but the exemption is bounded so a month-plus-old event
# that only just reached the pipeline (a lagging feed, or being classified
# "new to RB" today despite the underlying event being weeks old) doesn't
# render identically to a same-day story.
HEADLINE_FRESHNESS_DAYS = 7
CORPORATE_EVENT_MAX_AGE_DAYS = 21


def is_fresh_pub_date(pub_date: date | None, today: date, is_corporate: bool) -> bool:
    """True if `pub_date` passes the shared freshness gate.

    Ordinary items must be within HEADLINE_FRESHNESS_DAYS of today. Corporate
    events get the longer CORPORATE_EVENT_MAX_AGE_DAYS ceiling instead.
    An unknown pub_date passes through (conservative -- can't gate what you
    can't parse)."""
    if pub_date is None:
        return True
    age_days = (today - pub_date).days
    if is_corporate:
        return age_days <= CORPORATE_EVENT_MAX_AGE_DAYS
    return age_days <= HEADLINE_FRESHNESS_DAYS


# ---------------------------------------------------------------------------
# RB-DEFECT-2026-07-20 Phase 2 -- unified story identity
#
# Every previous fix for "the same real-world event reported twice" closed
# exactly one disguise it could wear: exact-URL matching, then anchor-noun
# title matching, then a "leading subject" signature, then republished-URL
# inheritance -- each one a narrow patch on top of the last, in a different
# dedup registry per section (render_intelligence_brief.py's module-level
# _rendered_this_run/_rendered_subjects_this_run, rendered_headlines.json's
# per-URL entries, and daily_brief.py's separate watchlist_new_activity_seen.json).
# Confirmed live: Wonder's $650M Series D was reported by 4 different
# outlets under 4 different URLs and still fragmented into separate
# registry entries even after those patches. This module replaces that
# patchwork with one canonical signature, resolved once, used everywhere.
#
# extras.entities (web_scanner.detect_entities) is watchlist-only and
# returns [] for anything not already on the ~150-name hand-curated list --
# confirmed live: it's empty for every one of the Wonder headlines that
# motivated this fix, since Wonder wasn't tracked yet. entity_key therefore
# falls back to a leading-subject heuristic computed directly from the
# title rather than assuming upstream metadata is reliable.
# ---------------------------------------------------------------------------

StoryKey = tuple  # (entity_key: str, event_type: str, disambiguator: frozenset[str])

_STORY_EVENT_TYPE_BY_BADGE_KEYWORD = [
    ("acquisition", "acquisition"),
    ("m&a", "acquisition"),
    ("funding", "funding_round"),
    ("ipo", "funding_round"),
    ("earnings", "earnings"),
    ("exec hire", "exec_change"),
    ("exec departure", "exec_change"),
]

# Words that open a sentence/headline without being a real subject -- a
# leading "The"/"How"/"Why" means the real subject (if any) is further into
# the title, past where this heuristic looks. Also used to reject a spurious
# capitalized word (mid-sentence Title Case, not a proper noun) from the
# disambiguator fallback.
_STORY_GENERIC_WORDS = {
    "this", "here", "what", "why", "how", "when", "where", "who",
    "the", "these", "those", "with", "from", "behind", "a", "an",
}
# Title-Case headline verbs ("Wonder Raises...", "Wonder Tops...") that would
# otherwise get swept into a multi-word leading-subject match alongside the
# real company name -- distinguishing "Wonder Raises" (wrong, verb included)
# from "Domino's Pizza" (right, both words are the company name) requires
# knowing which capitalized words are common headline verbs.
_STORY_HEADLINE_VERBS = {
    "raises", "reports", "announces", "appoints", "names", "hires",
    "acquires", "launches", "unveils", "tops", "plans", "looks",
    "buys", "sells", "wins", "loses", "opens", "closes", "expands",
    "enters", "exits", "is", "are", "was", "were", "gets", "adds",
}
_STORY_MONEY_RE = re.compile(r"\$\s?[\d,.]+\s?(?:million|billion|m\b|b\b|k\b)?", re.IGNORECASE)
_STORY_ROUND_LABEL_RE = re.compile(r"\bseries\s+[a-e]\b|\bseed\s+round\b|\bpre-ipo\b|\bipos?\b", re.IGNORECASE)
# Event-type acronyms that follow a company name in a headline ("Jersey
# Mike's IPO could raise...") but aren't part of the entity name -- without
# this, "Jersey Mike's IPO..." resolves to entity_key "jersey mike's ipo"
# while "Jersey Mike's launches IPO..." (verb breaks the phrase first)
# resolves to "jersey mike's", so the same IPO story fragments across outlets
# purely based on where the acronym happens to fall relative to a verb.
_STORY_EVENT_ACRONYMS = {"ipo", "ipos", "ceo", "cfo", "coo", "cto", "m&a"}


def _story_tokenize(text: str) -> list[str]:
    out = []
    for tok in text.split():
        cleaned = tok.strip(".,;:!?\"'()[]{}‘’“”")
        if cleaned:
            out.append(cleaned)
    return out


def _story_core_word(token: str) -> str:
    """Strip a trailing possessive ('s / ’s) so "McDonald's"/"McDonald’s"
    match the bare word "mcdonald" for stopword/verb comparisons."""
    return re.sub(r"[’']s$", "", token)


def _normalize_apostrophe(text: str) -> str:
    """Collapse curly/smart apostrophe variants to a plain ' before an
    entity_key is finalized. RB-DEFECT-2026-07-23: Restaurant Dive wrote
    "Jersey Mike’s" (curly) while Fast Casual wrote "Jersey Mike's"
    (straight) for the SAME IPO story -- resolve_story_identity kept
    whichever glyph the title happened to use, so the two outlets' entity
    keys were literally different strings ("jersey mike’s" vs
    "jersey mike's") and story_keys_match's `a[0] != b[0]` check rejected
    them outright, disambiguator overlap notwithstanding. Same whack-a-mole
    shape as the IPO-acronym and same-day-grace bugs: one more encoding a
    real-world entity name can wear."""
    return text.replace("’", "'").replace("‘", "'")


def _story_leading_subject(bare_title: str) -> str | None:
    """Leading capitalized word/phrase (e.g. "Wonder", "Domino's Pizza",
    "Checkers & Rally's") -- extends across multiple capitalized words but
    stops at the first Title-Case headline verb or non-capitalized word, so
    "Wonder Raises $650M" resolves to "Wonder" (not "Wonder Raises") while
    "Domino's Pizza appoints..." resolves to the full "Domino's Pizza"."""
    tokens = _story_tokenize(bare_title)
    if not tokens:
        return None
    first = tokens[0]
    if not first[0].isupper() or _story_core_word(first).lower() in _STORY_GENERIC_WORDS:
        return None
    phrase = [first]
    for tok in tokens[1:4]:
        core_lower = _story_core_word(tok).lower()
        if not tok[0].isupper() or core_lower in _STORY_HEADLINE_VERBS or core_lower in _STORY_EVENT_ACRONYMS:
            break
        phrase.append(tok)
    return " ".join(phrase)


def _story_normalize_money(token: str) -> str:
    t = re.sub(r"[,\s]", "", token).lower()
    t = re.sub(r"million\b", "m", t)
    t = re.sub(r"billion\b", "b", t)
    return t


def _story_extra_capitalized_words(bare_title: str, exclude_tokens: set[str]) -> list[str]:
    out = []
    for tok in _story_tokenize(bare_title):
        core = _story_core_word(tok)
        if not core or not core[0].isupper() or len(core) < 4:
            continue
        cl = core.lower()
        if cl in _STORY_GENERIC_WORDS or cl in _STORY_HEADLINE_VERBS or cl in exclude_tokens:
            continue
        out.append(cl)
    return sorted(set(out))


def resolve_story_identity(title: str, extras: dict | None = None) -> "StoryKey | None":
    """Resolve a corporate-badged headline to a canonical (entity_key,
    event_type, disambiguator) signature -- the same real-world event
    resolves to the same (or an overlapping, see story_keys_match) key
    regardless of which outlet covered it or what URL it's linked from.

    Returns None when no entity subject can be confidently identified (e.g.
    a roundup headline like "The chicken wars heat up... and Wonder's IPO
    plans" that doesn't open with a proper noun) -- callers should fall back
    to exact-URL dedup for those rather than guessing at an identity."""
    extras = extras or {}
    badge_match = re.match(r"^\[([^\]]+)\]\s*", title)
    badge = badge_match.group(1) if badge_match else (extras.get("signal_badge") or "")
    bare_title = title[badge_match.end():] if badge_match else title

    # RB-DEFECT-2026-07-20: extras.entities (web_scanner.detect_entities) is
    # a watchlist-term-mentioned-ANYWHERE-in-the-text match, not necessarily
    # the headline's actual subject -- confirmed live, "Wonder is valued at
    # more than $9B after latest fundraise" (a Wonder funding story) carries
    # entities=["Grubhub"] because the body text mentions Wonder will use
    # the funding for "Grubhub growth," and a QSR Magazine Wonder writeup
    # carried entities=["Burger King"] from an incidental mention elsewhere
    # in the article. The title-derived leading subject is far more
    # reliable when it resolves at all; only fall back to the watchlist
    # entity match for the minority of headlines it can't (e.g. a roundup
    # headline with no clean leading proper noun).
    subject = _story_leading_subject(bare_title)
    if subject:
        entity_key = subject.lower()
    else:
        entities = extras.get("entities") or []
        entity_key = str(entities[0]).strip().lower() if entities else None
    if not entity_key:
        return None
    entity_key = _normalize_apostrophe(entity_key)

    event_type = None
    badge_lower = badge.lower()
    for kw, et in _STORY_EVENT_TYPE_BY_BADGE_KEYWORD:
        if kw in badge_lower:
            event_type = et
            break
    if not event_type:
        event_type = extras.get("signal_type") or "general"

    money = _STORY_MONEY_RE.findall(bare_title)
    round_label = _STORY_ROUND_LABEL_RE.findall(bare_title)
    parts: list[str] = []
    if money:
        parts.extend(sorted({_story_normalize_money(m) for m in money}))
    if round_label:
        parts.extend(sorted({r.lower() for r in round_label}))
    if not parts:
        exclude = {_story_core_word(t).lower() for t in entity_key.split()}
        parts = _story_extra_capitalized_words(bare_title, exclude)

    return (entity_key, event_type, frozenset(parts))


# Event types where the same company essentially never has two *distinct*
# events of this kind within STORY_RETIREMENT_DAYS -- for these, a bare
# entity match is enough to call it the same story, since requiring a shared
# disambiguator token only works when two outlets happen to phrase the same
# detail the same way. Confirmed live 2026-08-25: Wendy's CMO hire got
# disambiguated as {"McDonald's"} by one outlet ("ex-McDonald's CMO") and
# {"Tariq", "Hassan"} by another (named the hire directly) -- same event,
# zero token overlap, rendered as two separate stories in Section C.
# funding_round is deliberately excluded: a company can legitimately raise
# two different rounds ($600M then $650M) within the window, so that type
# still needs a shared disambiguator to stay distinct.
_STORY_TYPES_SINGULAR_PER_WINDOW = {"exec_change", "acquisition", "earnings"}


def story_keys_match(a: "StoryKey", b: "StoryKey") -> bool:
    """True if two story keys represent the same real-world event.

    Strict equality is the common case. The safety net below catches
    real gaps found live: (1) event_type can disagree across near-identical
    wordings of the same event (a badge that got stripped by one render's
    badge-justification check resolves to "general" instead of
    "funding_round"), (2) one outlet's write-up includes an extra detail
    ("pre-IPO") another's omits, giving disambiguators that overlap but
    aren't identical -- both require the SAME entity and at least one shared
    disambiguator token, since a bare entity match with no shared
    distinguishing detail is not enough for most event types (that's how
    $600M and $650M Wonder rounds, or two unrelated same-entity
    announcements, stay distinct) -- and (3) for event types in
    _STORY_TYPES_SINGULAR_PER_WINDOW, a bare entity match alone is enough,
    since these types don't need a disambiguator to safely collapse."""
    if a == b:
        return True
    if a[0] != b[0]:
        return False
    # Must be the SAME singular-per-window type on both sides, not just
    # either side -- an acquisition and a funding round for the same company
    # are still two different stories even though "acquisition" alone is in
    # the singular set (confirmed by test_acquisition_never_matches_either_
    # funding_cluster; a same-side-only check would wrongly collapse them).
    if a[1] == b[1] and a[1] in _STORY_TYPES_SINGULAR_PER_WINDOW:
        return True
    return bool(a[2] & b[2])


# Once a story is told, suppress it for longer than the old 4-day corporate
# grace -- this is what actually matches "the same story is still the same
# story" instead of "ages out and comes back." Starting conservative (not
# the 30 days floated in early planning) since the low-volume restaurant
# sections lean on a shorter window to avoid empty-section days; tune from
# a week of real output rather than locking in a number now.
STORY_RETIREMENT_DAYS = 12
# A story is still worth showing (a follow-up from a different outlet, a new
# detail) for its first few days -- reuses the same window the old grace
# period used, so day-to-day behavior for a genuinely fresh story is
# unchanged. Past this, within the retirement window, it's suppressed even
# though it isn't fully forgotten yet.
STORY_ACTIVE_DISPLAY_DAYS = 1


def _story_id(key: "StoryKey", first_seen: str) -> str:
    """Stable id for a new story. Once created, callers never recompute
    this -- all lookups go through lookup()'s key-content matching -- so
    the suffix only needs to guarantee a fresh cycle after retirement gets
    its own id rather than overwriting the retired story's record."""
    entity_key, event_type, disambiguator = key
    return "|".join([entity_key, event_type, ",".join(sorted(disambiguator)), first_seen])


class StoryLedger:
    """Tracks corporate-event stories by canonical identity (resolve_story_
    identity), not by URL -- the fix for the class of bug where the same
    real-world event, covered by a new outlet or republished under a new
    URL, restarted its own independent dedup countdown. Replaces three
    previously-separate registries: render_intelligence_brief.py's
    rendered_headlines.json (corporate branch), its module-level
    _rendered_this_run/_rendered_subjects_this_run same-run sets, and
    daily_brief.py's watchlist_new_activity_seen.json.

    Persisted shape:
        {
          "stories": {
            "<story_id>": {
              "entity_key": ..., "event_type": ..., "disambiguator": [...],
              "first_seen": "YYYY-MM-DD", "last_seen": "YYYY-MM-DD",
              "urls": [...], "render_count": N, "title_sample": "...",
            },
          },
          "url_index": {"<url>": "<story_id>"},
        }
    """

    def __init__(self, data: dict | None = None):
        data = data or {}
        self.stories: dict[str, dict] = data.get("stories") or {}
        self.url_index: dict[str, str] = data.get("url_index") or {}

    def to_dict(self) -> dict:
        return {"stories": self.stories, "url_index": self.url_index}

    def url_to_story(self, url: str) -> dict | None:
        story_id = self.url_index.get(url)
        return self.stories.get(story_id) if story_id else None

    def lookup(self, key: "StoryKey") -> tuple[str, dict] | None:
        """Find an existing story matching this key (strict or loose via
        story_keys_match). Linear scan -- the corporate-item volume this
        covers is tens to low hundreds of entries, not thousands."""
        for story_id, story in self.stories.items():
            existing_key = (
                story["entity_key"], story["event_type"], frozenset(story["disambiguator"]),
            )
            if story_keys_match(key, existing_key):
                return story_id, story
        return None

    def is_within_retirement(self, story: dict, today: date, retirement_days: int = STORY_RETIREMENT_DAYS) -> bool:
        first_seen = date.fromisoformat(story["first_seen"])
        return (today - first_seen).days < retirement_days

    def should_render(self, key: "StoryKey", today: date,
                       active_display_days: int = STORY_ACTIVE_DISPLAY_DAYS,
                       retirement_days: int = STORY_RETIREMENT_DAYS,
                       url: str | None = None) -> bool:
        """True if a candidate item resolving to `key` should be rendered.

        A brand-new story (or one whose retirement window has fully elapsed
        -- treated as forgotten, not permanent) renders. An already-tracked
        story still within its short active-display window renders too (a
        different outlet's follow-up in the first few days is genuine
        continued coverage, not a repeat). Otherwise -- already told, past
        its display window, still within the longer retirement window --
        it's suppressed.

        `url` (RB-DEFECT-2026-08-17): "fully elapsed -- forgotten" used to
        mean "renders unconditionally," which let the exact same URL that
        already completed a full display cycle come back indefinitely every
        time the upstream feed re-served it (Atoms' $1.7B story: 9 renders
        over 16+ days). Pass the candidate URL so a completed cycle for
        THIS SPECIFIC url stays suppressed even past retirement -- a
        genuinely new url for the same entity/event (no url passed, or a
        url that hasn't completed a cycle) still gets treated as forgotten
        and renders, same as before."""
        found = self.lookup(key)
        if found is None:
            return True
        _, story = found
        if not self.is_within_retirement(story, today, retirement_days):
            if url and self._url_already_told(url):
                return False
            return True  # fully retired -- treat as forgotten, not a repeat
        first_seen = date.fromisoformat(story["first_seen"])
        # Matches the pre-Phase-2 corporate-grace convention exactly: a
        # story is suppressed once age reaches active_display_days (not
        # only once it exceeds it) -- e.g. grace=4 means shown on days 0-3,
        # suppressed from day 4 onward.
        return (today - first_seen).days < active_display_days

    def _url_already_told(self, url: str | None) -> bool:
        """RB-DEFECT-2026-08-17: confirmed live -- Atoms' $1.7B funding
        story (single URL, first published 2026-08-01) rendered 9 times
        across 16+ days (render_count in rendered_headlines.json) instead of
        stopping after its 4-day active-display window. Root cause: once
        STORY_RETIREMENT_DAYS (12) elapses since first_seen, record()'s
        "fully retired -- start a fresh cycle" branch treats the NEXT time
        this exact URL is re-served by the upstream feed as a brand-new
        story with a backdated-to-today first_seen, handing it a fresh
        4-day display window. That's the right call for a genuinely new
        follow-up URL about an old event, but wrong for the literal same
        article resurfacing in the feed (a stable, popular story an RSS/
        aggregator keeps re-including) -- the "12 days passed" retirement
        clock has nothing to do with whether THIS SPECIFIC url still has
        anything new to say. Once a URL has completed a full active-display
        cycle (been shown active_display_days times or more), it never gets
        a second cycle -- checked here across ALL stories, retired or not,
        since retired stories stay in self.stories rather than being
        deleted."""
        if not url:
            return False
        for story in self.stories.values():
            if url in (story.get("urls") or []) and story.get("render_count", 0) >= STORY_ACTIVE_DISPLAY_DAYS:
                return True
        return False

    def record(self, key: "StoryKey", url: str | None, title: str, today: date,
               retirement_days: int = STORY_RETIREMENT_DAYS) -> str:
        """Record this item against its story (creating one if needed, or
        reusing an existing one if its retirement window already elapsed).
        Always updates url_index so a NEW url for an already-known story
        maps straight to it next time, and always updates last_seen/
        render_count regardless of whether should_render() said to show it
        -- suppressed coverage is still evidence the story is ongoing."""
        found = self.lookup(key)
        if found is not None:
            story_id, story = found
            if not self.is_within_retirement(story, today, retirement_days):
                if self._url_already_told(url):
                    # This exact article already had its full display cycle
                    # once -- extend the existing (retired) record instead
                    # of starting a fresh one, so should_render() keeps
                    # measuring age from the ORIGINAL first_seen and this
                    # specific URL never resurrects.
                    pass
                else:
                    # Fully retired, and this is a URL we haven't already
                    # shown to completion -- start a fresh cycle under a new
                    # story_id rather than resurrecting the old one with a
                    # backdated first_seen (a genuine re-emergence weeks
                    # later, via a NEW url, deserves its own display window).
                    found = None
        if found is None:
            story_id = _story_id(key, today.isoformat())
            story = {
                "entity_key": key[0], "event_type": key[1],
                "disambiguator": sorted(key[2]),
                "first_seen": today.isoformat(), "last_seen": today.isoformat(),
                "urls": [], "render_count": 0, "title_sample": title[:120],
            }
            self.stories[story_id] = story
        story["last_seen"] = today.isoformat()
        story["render_count"] = story.get("render_count", 0) + 1
        if url and url not in story["urls"]:
            story["urls"].append(url)
        if url:
            self.url_index[url] = story_id
        return story_id

    def merge_into(self, story_id: str, url: str | None, today: date) -> None:
        """Record this url against an already-identified story_id directly
        -- for callers that found the match some other way than a
        resolve_story_identity() key (e.g. an LLM tie-break confirming an
        otherwise-unresolvable title describes the same event as an
        existing story). Same bookkeeping as record()'s existing-story
        branch, without needing a StoryKey."""
        story = self.stories.get(story_id)
        if story is None:
            return
        story["last_seen"] = today.isoformat()
        story["render_count"] = story.get("render_count", 0) + 1
        if url and url not in story["urls"]:
            story["urls"].append(url)
        if url:
            self.url_index[url] = story_id


def find_tiebreak_candidates(title: str, ledger: StoryLedger, today: date,
                              retirement_days: int = STORY_RETIREMENT_DAYS) -> list[tuple[str, dict]]:
    """For a title resolve_story_identity() couldn't confidently key, find
    active (not yet retired) stories worth an LLM tie-break check -- a
    cheap textual pre-filter (shared significant words with the story's
    title_sample) so an expensive tie-break call only fires for genuine
    candidates, not every active story. Deliberately dependency-free (no
    LLM call here) -- callers in the render layer own the actual
    llm_assist.same_story_tiebreak() call and its result.

    Returns (story_id, story) pairs, most recently seen first."""
    def _proper_noun_words(text: str) -> set[str]:
        # Require capitalization -- a shared common word ("chain", "deal",
        # "acquires") is not a meaningful link between two headlines and
        # would otherwise pre-filter nearly every same-category headline
        # into a candidate, defeating the point of a cheap pre-filter and
        # spending API calls on unrelated stories.
        out = set()
        for w in _story_tokenize(text):
            core = _story_core_word(w)
            if len(core) < 4 or not core[0].isupper():
                continue
            cl = core.lower()
            if cl in _STORY_GENERIC_WORDS or cl in _STORY_HEADLINE_VERBS:
                continue
            out.add(cl)
        return out

    title_words = _proper_noun_words(title)
    if not title_words:
        return []
    candidates = []
    for story_id, story in ledger.stories.items():
        if not ledger.is_within_retirement(story, today, retirement_days):
            continue
        sample_words = _proper_noun_words(story.get("title_sample") or "")
        if title_words & sample_words:
            candidates.append((story_id, story))
    candidates.sort(key=lambda sc: sc[1].get("last_seen", ""), reverse=True)
    return candidates


WORLD_NATIONAL_FOREIGN_SUBJECT_KEYWORDS = [
    "iran", "tehran", "khamenei", "china", "beijing", "russia", "moscow", "putin",
    "ukraine", "kyiv", "kiev", "zelensky", "israel", "gaza", "netanyahu", "tel aviv",
    "north korea", "pyongyang", "south korea", "seoul", "taiwan", "taipei",
    "france", "paris", "le pen", "germany", "berlin", "britain", "london",
    "india", "delhi", "modi", "pakistan", "islamabad", "bangladesh", "dhaka",
    "sri lanka", "nigeria", "south africa", "kenya", "venezuela", "brazil",
    "mexico", "argentina", "australia", "japan", "tokyo", "syria", "lebanon",
    "beirut", "yemen", "saudi arabia", "turkey", "egypt", "afghanistan", "kabul",
]
_WORLD_NATIONAL_FOREIGN_SUBJECT_RE = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in WORLD_NATIONAL_FOREIGN_SUBJECT_KEYWORDS) + r")\b",
    re.IGNORECASE,
)

WORLD_NATIONAL_US_KEYWORDS = [
    "u.s.", "united states", "federal", "senate", "congress", "house democrat",
    "house republican", "white house", "president trump", "president biden",
    "america", "american", "doj", "department of justice", "supreme court",
    "washington", "trump", "epstein", "voter", "election", "nasa", "fbi", "cia",
]


def classify_world_national_scope(item: dict) -> str:
    """Return "us" or "world" for a world_national_headlines item.

    A foreign country/leader named in the title means the story is "world"
    regardless of an incidental US mention in the body (e.g. a story about
    an Iranian funeral that notes strikes "by the U.S. and Israel" is still
    a story about Iran, not domestic US news).
    """
    extras = item.get("extras") or {}
    title_lower = (item.get("title") or "").lower()
    summary_lower = (item.get("summary") or item.get("why_it_matters") or "").lower()
    text = title_lower + " " + summary_lower
    is_foreign_subject = bool(_WORLD_NATIONAL_FOREIGN_SUBJECT_RE.search(title_lower))
    if is_foreign_subject:
        return "world"
    # RB-DEFECT-2026-07-13: a story with neither a foreign-subject match
    # NOR an explicit US keyword hit used to still fall through to "world"
    # by default -- so a purely domestic story with no textual US marker
    # ("The SpaceX IPO made history...", "US Senator Mitch McConnell says
    # absence due to fall and pneumonia" -- "senate" doesn't substring-match
    # "senator", "u.s." with periods doesn't match "US" without them, and
    # neither mentions a keyword like "congress"/"america"/"trump") rendered
    # under "A: World Headlines." "World" should require positive evidence
    # of international relevance; the absence of a US signal isn't evidence
    # of the opposite -- it defaults to national/domestic instead.
    return "us"


# Direct competitors to Global Payments / Genius / Worldpay in the enterprise
# restaurant-tech + payments space. Shared by render_daily_brief.py's
# GP/Genius Dot Connections scoring and daily_brief.py's warm-path
# opportunity detection (RB-DEFECT-2026-07-09: a stale pre-employment
# active_threads entry recommended Toast as a "warm path opportunity" with
# no awareness that Toast is now a direct competitor).
GP_COMPETITOR_COMPANIES = frozenset({
    "toast", "par technology", "par tech", "brink pos", "shift4", "lightspeed",
    "ncr voyix", "ncr", "aloha", "oracle hospitality", "micros", "agilysys",
    "revel", "square", "clover", "stripe", "adyen", "fiserv",
})


def is_gp_competitor(name: str) -> bool:
    """True if `name` matches a known Global Payments / Genius / Worldpay
    competitor (case-insensitive substring match against GP_COMPETITOR_COMPANIES)."""
    name_lower = (name or "").lower()
    return any(c in name_lower for c in GP_COMPETITOR_COMPANIES)


# ---------------------------------------------------------------------------
# Watchlist escalation grouping (shared by Intelligence Brief Section F and
# Daily Brief Technology Radar)
# ---------------------------------------------------------------------------

# RB-DEFECT-2026-08-17: confirmed live -- 17 "[ESCALATED]" watchlist entities
# rendered as 17 nearly-identical full blocks, but they trace back to only
# 4-5 actual overdue loops: McDonald's/PAR Technology/PAR Loyalty/Global
# Payments/Harri/STRATACACHE are ALL the same loop (L-2026-07-23-003 --
# Global Payments account research), just tagged to six different watchlist
# entities. A reader has to scan 34 lines to discover "4 loops are overdue,"
# a fact that fits in 4 lines. This is real, not cosmetic: it also
# duplicates near-verbatim between Part 1 Section F and Part 2 Technology
# Radar, which independently render the same per-entity blocks from the
# same underlying items.
_ESCALATION_LOOP_ID_RE = re.compile(r"\bL-\d{4}-\d{2}-\d{2}-\d+\b")


def group_watchlist_escalations(items: list[dict]) -> list[dict]:
    """Group escalated watchlist items that share the same underlying
    overdue-loop id into one entry per loop (with every touched entity
    listed), instead of one full block per entity. An escalation whose
    reason isn't loop-shaped (an earnings event, a standalone market
    signal) has nothing to group with and stays its own entry.

    Returns entries in first-seen order, loop groups before standalone
    items, each shaped:
        {"loop_id": str | None, "why": str, "entities": [name, ...],
         "items": [original item dicts...], "status_changed": bool}
    `why` is the freshest (status_changed=True) text seen for that loop if
    any item reported one, else the first item's why-text -- callers render
    ONE why-line per group regardless of how many entities share it.
    """
    groups: dict[str, dict] = {}
    order: list[str] = []
    # RB-2026-09-03: confirmed live -- "Salad and Go" and "Pizza Hut" both
    # escalated off the exact same Nation's Restaurant News article (a
    # 7 Brew/Salad and Go real-estate story that happens to also namecheck
    # Pizza Hut), and since neither has a loop_id, each rendered its own
    # full [ESCALATED] block with the identical why-text verbatim -- reading
    # as a confusing repeat, not two distinct findings. Same fix shape as
    # the loop_id grouping above, keyed on shared source_url instead: an
    # article naming multiple entities groups once, not once per entity.
    url_groups: dict[str, dict] = {}
    url_order: list[str] = []
    standalone: list[dict] = []

    for item in items:
        extras = item.get("extras") or {}
        name = extras.get("entity_name") or (item.get("title") or "").split(" — ")[0].strip()
        why = (item.get("why_it_matters") or item.get("summary") or "").strip()
        status_changed = bool(extras.get("status_changed"))
        match = _ESCALATION_LOOP_ID_RE.search(why)
        if match:
            loop_id = match.group(0)
            if loop_id not in groups:
                groups[loop_id] = {
                    "loop_id": loop_id, "why": why, "entities": [],
                    "items": [], "status_changed": False,
                }
                order.append(loop_id)
            g = groups[loop_id]
            g["entities"].append(name)
            g["items"].append(item)
            if status_changed and not g["status_changed"]:
                g["why"] = why  # prefer the fresh (status-changed) wording
                g["status_changed"] = True
            continue

        source_url = (extras.get("source_url") or "").strip()
        if source_url:
            if source_url not in url_groups:
                url_groups[source_url] = {
                    "loop_id": None, "why": why, "entities": [],
                    "items": [], "status_changed": False,
                }
                url_order.append(source_url)
            g = url_groups[source_url]
            g["entities"].append(name)
            g["items"].append(item)
            if status_changed and not g["status_changed"]:
                g["why"] = why
                g["status_changed"] = True
        else:
            standalone.append({
                "loop_id": None, "why": why, "entities": [name],
                "items": [item], "status_changed": status_changed,
            })

    result = [groups[lid] for lid in order]
    result.extend(url_groups[u] for u in url_order)
    result.extend(standalone)
    return result


# ---------------------------------------------------------------------------
# Weekly-plan render invalidation (RB-DEFECT-067)
# ---------------------------------------------------------------------------

def weekly_plan_fingerprint() -> str:
    """A short string that changes whenever the weekly-plan state that
    render_daily_brief.py / render_intelligence_brief.py display would
    itself change -- confirming a draft, generating a new draft, or
    rejecting one. Callers compare this against the fingerprint stamped
    into a previously-rendered brief's companion .json to decide whether
    that cached markdown is still valid or needs a real re-render.

    RB-DEFECT-067: confirmed live -- Todd ran `weekly_plan_generator.py
    --confirm` mid-day, which correctly updated weekly_plan.json, but
    `render_daily_brief.py`'s cache gate (`out_md.exists() and not force`)
    has no way to know upstream state changed, so it kept serving the
    already-rendered file showing the OLD plan and the stale "awaiting
    confirmation" warning until someone manually passed --force. This
    fingerprint is intentionally built from content that actually matters
    for rendering (week_of/status/confirmed_at), not a raw mtime -- a plan
    file touched without a real state change (e.g. re-saved unchanged by
    another tool) must not force a needless re-render.
    """
    import hashlib as _hashlib
    plan_path = SYSTEM_DIR / "weekly_plan.json"
    draft_path = SYSTEM_DIR / "weekly_plan_draft.json"
    parts: list[str] = []
    for path in (plan_path, draft_path):
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
            parts.append("|".join([
                str(d.get("week_of") or ""),
                str(d.get("status") or ""),
                str(d.get("confirmed_at") or d.get("rejected_at") or d.get("generated_at") or ""),
            ]))
        except (OSError, json.JSONDecodeError):
            parts.append("missing")
    return _hashlib.sha256("::".join(parts).encode("utf-8")).hexdigest()[:16]


def is_weekly_plan_render_current(meta_path: Path) -> bool:
    """True if a previously-rendered brief's companion .json (the small
    metadata sidecar render_daily_brief.py/render_intelligence_brief.py
    write next to the .md, e.g. system/briefs/2026-08-19-daily-brief.json)
    was rendered against the weekly-plan state that's live right now.

    Factored out of both renderers' cache gates specifically so this
    decision is unit-testable on its own -- the gate itself
    (`out_md.exists() and not force and is_weekly_plan_render_current(...)`)
    lives inline in each render() because it also needs that function's
    other locals, but the actual "is it still valid" logic doesn't need any
    of that and shouldn't require exercising a full render() call (which
    pulls in the entire canonical_brief cache, every sub-renderer, etc.)
    just to test one boolean decision.

    False (needs re-render) whenever: the metadata file is missing/
    unreadable, it predates this fingerprint field existing at all, or its
    stamped fingerprint no longer matches weekly_plan_fingerprint().
    """
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    stamped = meta.get("weekly_plan_fingerprint")
    if stamped is None:
        return False
    return stamped == weekly_plan_fingerprint()


def source_health_fingerprint() -> str:
    """A short string that changes whenever system/.cache/source_health.json's
    per-source status/last_refreshed_at changes. Same purpose and pattern as
    weekly_plan_fingerprint() above, for the same reason: render_daily_
    brief.py / render_intelligence_brief.py's cache gate only checked the
    weekly-plan fingerprint, so a source going from Stale to Refreshed (or
    vice versa) mid-day -- exactly what a manual refresh_sources.py repair
    run does -- left the cache gate seeing an unchanged weekly plan and
    serving the already-rendered file, showing the OLD source health, until
    someone passed --force. Confirmed live 2026-09-08: a rerun immediately
    after fixing and re-capturing calendar/email:personal recomputed
    system/.cache/daily_brief.json correctly, but render_daily_brief.py and
    render_intelligence_brief.py both served that morning's already-
    rendered .md files unchanged -- the ones showing calendar/email:
    personal as Stale -- and THOSE went out in the email that was supposed
    to be the fix."""
    import hashlib as _hashlib
    path = SYSTEM_DIR / ".cache" / "source_health.json"
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        sources = d.get("sources") or {}
        parts = [
            f"{name}:{(row or {}).get('status')}:{(row or {}).get('last_refreshed_at')}"
            for name, row in sorted(sources.items())
        ]
    except (OSError, json.JSONDecodeError):
        parts = ["missing"]
    return _hashlib.sha256("::".join(parts).encode("utf-8")).hexdigest()[:16]


def is_source_health_render_current(meta_path: Path) -> bool:
    """Same contract as is_weekly_plan_render_current, for source_health_
    fingerprint() instead -- see that function's docstring."""
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    stamped = meta.get("source_health_fingerprint")
    if stamped is None:
        return False
    return stamped == source_health_fingerprint()
