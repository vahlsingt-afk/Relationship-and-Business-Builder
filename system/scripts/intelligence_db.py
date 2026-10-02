"""
intelligence_db.py — RB 9.38 / Sprint E-1
Gathered Intelligence Database: persistent SQLite store for all intelligence
items captured by the RB system.

Design intent
─────────────
Every piece of intelligence that enters RB — whether from a web scan, email
harvest, manual note, or LinkedIn signal — is stored here as a first-class
record with:
  • A date-sequenced ID (INT-YYYY-MM-DD-NNN)
  • Full content (title + narrative summary)
  • Source provenance (name, URL, type)
  • Confidence label (high / medium / low / unverified)
  • Lifecycle state (new / active / monitoring / dormant / resolved / suppressed)
  • Rich tags (entity, contact, product, industry, signal_type, thread_ref, domain)
  • Temporal audit trail (gathered, communicated, last_surfaced, updated)

This store is the foundation for:
  connect_the_dots   — cross-entity convergence across time, not just today
  CoS research       — "what do we know about Global Payments in 90 days?"
  source audit       — every fact traced to source + confidence
  lifecycle aging    — per-item suppression/reactivation (not section-level)

Relationship to IntelligenceStore (intelligence_lifecycle.py)
─────────────────────────────────────────────────────────────
IntelligenceStore handles brief-cycle deduplication (have I seen this item?).
IntelligenceDB handles content persistence and research queries. They coexist.
Future migration: IntelligenceStore's state transitions can be fed by IntelligenceDB
lifecycle_state once the DB is the write target for all brief items.

Key exports
───────────
  IntelligenceDB        SQLite-backed intelligence store (main class)
  TAG_TYPES             valid tag type constants
  LIFECYCLE_STATES      valid lifecycle state constants
  CONFIDENCE_LEVELS     valid confidence constants
  SOURCE_TYPES          valid source type constants
  DEFAULT_DB_PATH       resolved path to .cache/intelligence.db
"""
from __future__ import annotations

import json
import re
import sqlite3
from collections import defaultdict
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Generator

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCHEMA_VERSION = 1

TAG_TYPES = frozenset({
    "entity",        # company or org name: "PAR Technology", "McDonald's"
    "contact",       # person name: "Todd Vahlsing", "Chris Raque"
    "product",       # product/platform: "Brink POS", "Kiosk"
    "industry",      # vertical: "restaurant-tech", "franchise", "QSR"
    "signal_type",   # event class: "acquisition", "exec-change", "product-launch"
    "thread_ref",    # active thread ID: "T-2026-05-PAR-Technology"
    "domain",        # macro domain: "payments", "finance", "regulatory"
    "brand",         # restaurant brand: "Chick-fil-A", "Taco Bell"
    "source_entity", # outlet that published: "Restaurant Dive", "NRN"
    "keyword",       # freeform keyword for ad-hoc tagging
})

LIFECYCLE_STATES = frozenset({
    "new",         # first observation; surface immediately
    "active",      # being tracked; surface when relevant
    "monitoring",  # low priority; surface only on change
    "dormant",     # stored; suppress from brief
    "resolved",    # no longer relevant; exclude
    "suppressed",  # explicitly suppressed; never surface
})

CONFIDENCE_LEVELS = frozenset({
    "high",        # verified from primary source or multiple corroborating
    "medium",      # single trade-press source or plausible inference
    "low",         # single indirect source, rumor, or partial data
    "unverified",  # captured but not yet assessed
})

SOURCE_TYPES = frozenset({
    "web_scan",       # Sprint E: external URL fetch (Restaurant Dive, NRN, etc.)
    "email_harvest",  # passive email intelligence pipeline
    "manual",         # user-entered via API POST /intelligence
    "linkedin",       # LinkedIn export or session reader
    "brief_harvest",  # scraped from existing brief section items
    "lifecycle",      # migrated from IntelligenceStore
    "macro",          # macro_intelligence.py output
    "ecosystem",      # ecosystem_intelligence.py output
    "earnings",       # earnings_monitor.py signal
    "unknown",        # fallback
})

RELATION_TYPES = frozenset({
    "related",     # general relationship
    "follow_up",   # this item is a follow-up to the other
    "confirms",    # corroborates the other item
    "contradicts", # contradicts the other item
    "supersedes",  # replaces/updates the other item
})

_SYSTEM_DIR = Path(__file__).resolve().parent.parent
_CACHE_DIR = _SYSTEM_DIR / ".cache"
DEFAULT_DB_PATH: Path = _CACHE_DIR / "intelligence.db"

# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

_DDL_ITEMS = """
CREATE TABLE IF NOT EXISTS intelligence_items (
    id                 TEXT    NOT NULL PRIMARY KEY,
    title              TEXT    NOT NULL,
    content            TEXT    NOT NULL DEFAULT '',
    source_name        TEXT    NOT NULL,
    source_url         TEXT,
    source_type        TEXT    NOT NULL DEFAULT 'unknown',
    gathered_date      TEXT    NOT NULL,
    communicated_date  TEXT,
    confidence         TEXT    NOT NULL DEFAULT 'medium',
    lifecycle_state    TEXT    NOT NULL DEFAULT 'new',
    suppression_count  INTEGER NOT NULL DEFAULT 0,
    last_surfaced_date TEXT,
    raw_json           TEXT,
    created_at         TEXT    NOT NULL,
    updated_at         TEXT    NOT NULL
)
"""

_DDL_TAGS = """
CREATE TABLE IF NOT EXISTS intelligence_tags (
    item_id    TEXT NOT NULL REFERENCES intelligence_items(id) ON DELETE CASCADE,
    tag_type   TEXT NOT NULL,
    tag_value  TEXT NOT NULL,
    PRIMARY KEY (item_id, tag_type, tag_value)
)
"""

_DDL_RELATIONS = """
CREATE TABLE IF NOT EXISTS intelligence_relations (
    from_id       TEXT NOT NULL REFERENCES intelligence_items(id) ON DELETE CASCADE,
    to_id         TEXT NOT NULL REFERENCES intelligence_items(id) ON DELETE CASCADE,
    relation_type TEXT NOT NULL DEFAULT 'related',
    PRIMARY KEY (from_id, to_id, relation_type)
)
"""

_DDL_META = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT NOT NULL PRIMARY KEY,
    value TEXT NOT NULL
)
"""

_DDL_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_items_gathered    ON intelligence_items(gathered_date)",
    "CREATE INDEX IF NOT EXISTS idx_items_lifecycle   ON intelligence_items(lifecycle_state)",
    "CREATE INDEX IF NOT EXISTS idx_items_source_type ON intelligence_items(source_type)",
    "CREATE INDEX IF NOT EXISTS idx_items_confidence  ON intelligence_items(confidence)",
    "CREATE INDEX IF NOT EXISTS idx_tags_type_value   ON intelligence_tags(tag_type, tag_value)",
    "CREATE INDEX IF NOT EXISTS idx_tags_item         ON intelligence_tags(item_id)",
]


# ---------------------------------------------------------------------------
# Helper: normalize tag inputs
# ---------------------------------------------------------------------------

def _normalize_tag(tag: Any) -> dict[str, str] | None:
    """Normalize a tag to {type, value} dict. Returns None on bad input."""
    if isinstance(tag, dict):
        t = str(tag.get("type") or tag.get("tag_type") or "").strip().lower()
        v = str(tag.get("value") or tag.get("tag_value") or "").strip()
    elif isinstance(tag, (list, tuple)) and len(tag) == 2:
        t, v = str(tag[0]).strip().lower(), str(tag[1]).strip()
    else:
        return None
    if not t or not v:
        return None
    if t not in TAG_TYPES:
        t = "keyword"  # graceful fallback instead of raising
    return {"type": t, "value": v}


def _today_iso() -> str:
    return date.today().isoformat()


def _now_iso() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------

class IntelligenceDB:
    """
    SQLite-backed gathered intelligence store.

    Usage — as a context manager (preferred):

        with IntelligenceDB() as db:
            item_id = db.add_item(
                title="PAR acquires TASK Group",
                content="PAR Technology announced acquisition of TASK Group...",
                source_name="Restaurant Dive",
                source_url="https://restaurantdive.com/...",
                source_type="web_scan",
                confidence="high",
                tags=[
                    {"type": "entity",      "value": "PAR Technology"},
                    {"type": "entity",      "value": "TASK Group"},
                    {"type": "signal_type", "value": "acquisition"},
                    {"type": "industry",    "value": "restaurant-tech"},
                ],
            )

    Usage — explicit open/close:

        db = IntelligenceDB()
        db.open()
        ...
        db.close()
    """

    def __init__(self, db_path: Path | None = None):
        self._path: Path = db_path or DEFAULT_DB_PATH
        self._conn: sqlite3.Connection | None = None

    # ------------------------------------------------------------------ #
    # Connection lifecycle
    # ------------------------------------------------------------------ #

    def open(self) -> "IntelligenceDB":
        """Open (or create) the database and run migrations."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(self._path),
            check_same_thread=False,
            detect_types=sqlite3.PARSE_DECLTYPES,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._migrate()
        return self

    def close(self) -> None:
        """Commit and close the connection."""
        if self._conn:
            self._conn.commit()
            self._conn.close()
            self._conn = None

    def __enter__(self) -> "IntelligenceDB":
        return self.open()

    def __exit__(self, *_: Any) -> None:
        self.close()

    @property
    def _db(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError(
                "IntelligenceDB is not open. Use `with IntelligenceDB() as db:` "
                "or call db.open() first."
            )
        return self._conn

    # ------------------------------------------------------------------ #
    # Schema migration
    # ------------------------------------------------------------------ #

    def _migrate(self) -> None:
        """Create tables and advance schema to current version."""
        cur = self._db
        cur.execute(_DDL_ITEMS)
        cur.execute(_DDL_TAGS)
        cur.execute(_DDL_RELATIONS)
        cur.execute(_DDL_META)
        for idx_sql in _DDL_INDEXES:
            cur.execute(idx_sql)

        # Record schema version
        cur.execute(
            "INSERT OR IGNORE INTO meta(key, value) VALUES('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        cur.execute(
            "INSERT OR IGNORE INTO meta(key, value) VALUES('created_at', ?)",
            (_now_iso(),),
        )
        self._db.commit()

    # ------------------------------------------------------------------ #
    # ID generation
    # ------------------------------------------------------------------ #

    def _generate_id(self, gathered_date: str) -> str:
        """
        Generate a date-sequenced ID: INT-YYYY-MM-DD-NNN
        Sequence restarts each calendar day. Thread-safe within one connection.
        """
        count = self._db.execute(
            "SELECT COUNT(*) FROM intelligence_items WHERE gathered_date = ?",
            (gathered_date,),
        ).fetchone()[0]
        return f"INT-{gathered_date}-{count + 1:03d}"

    # ------------------------------------------------------------------ #
    # Write operations
    # ------------------------------------------------------------------ #

    def add_item(
        self,
        *,
        title: str,
        content: str = "",
        source_name: str,
        source_url: str | None = None,
        source_type: str = "unknown",
        gathered_date: str | None = None,
        confidence: str = "medium",
        lifecycle_state: str = "new",
        tags: list[Any] | None = None,
        raw_json: dict | Any | None = None,
    ) -> str:
        """
        Insert a new intelligence item and optionally tag it.

        Returns the generated item ID (INT-YYYY-MM-DD-NNN).

        Parameters
        ----------
        title           Short headline (required)
        content         Narrative summary (2–5 sentences)
        source_name     Human-readable source name (e.g. "Restaurant Dive")
        source_url      Source URL, optional
        source_type     One of SOURCE_TYPES; defaults to "unknown"
        gathered_date   ISO date; defaults to today
        confidence      One of CONFIDENCE_LEVELS; defaults to "medium"
        lifecycle_state One of LIFECYCLE_STATES; defaults to "new"
        tags            List of tag dicts: [{"type": "entity", "value": "PAR"}]
                        Also accepts (type, value) tuples.
        raw_json        Any JSON-serializable payload; stored verbatim.
        """
        gdate = gathered_date or _today_iso()
        now = _now_iso()

        # RB-DEFECT-2026-07-10c: add_item() had no dedup check, so every
        # source re-scan that still saw a given article (RSS feeds routinely
        # list several days of recent history, not just brand-new items)
        # inserted it again as a fresh row. Confirmed at scale: 8,242 total
        # rows vs. 2,653 distinct source_urls (a 3.1x duplication ratio
        # db-wide), with individual popular articles duplicated 7-10x. This
        # silently inflated every downstream signal-count/convergence
        # computation that reads item counts from this table (connect_the_dots'
        # "N signals across M sources", strategic_industry_signals, etc.) --
        # a "sustained 30-day pattern" claim could just be one article
        # re-inserted repeatedly. If source_url is present and already
        # exists, treat this as a re-sighting of the same item: apply any
        # new tags, bump timestamps, and return the existing id instead of
        # inserting a duplicate row.
        if source_url:
            existing = self._db.execute(
                "SELECT id FROM intelligence_items WHERE source_url = ? "
                "ORDER BY created_at ASC LIMIT 1",
                (source_url,),
            ).fetchone()
            if existing:
                existing_id = existing["id"]
                if tags:
                    self.tag_item_bulk(existing_id, tags)
                self._db.execute(
                    "UPDATE intelligence_items SET updated_at = ?, last_surfaced_date = ? WHERE id = ?",
                    (now, gdate, existing_id),
                )
                self._db.commit()
                return existing_id

        item_id = self._generate_id(gdate)

        # Coerce + validate
        source_type = source_type if source_type in SOURCE_TYPES else "unknown"
        confidence = confidence if confidence in CONFIDENCE_LEVELS else "medium"
        lifecycle_state = lifecycle_state if lifecycle_state in LIFECYCLE_STATES else "new"
        raw_str = json.dumps(raw_json, ensure_ascii=False) if raw_json is not None else None

        self._db.execute(
            """
            INSERT INTO intelligence_items
              (id, title, content, source_name, source_url, source_type,
               gathered_date, confidence, lifecycle_state, suppression_count,
               raw_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
            """,
            (
                item_id, title, content, source_name, source_url,
                source_type, gdate, confidence, lifecycle_state,
                raw_str, now, now,
            ),
        )

        if tags:
            self.tag_item_bulk(item_id, tags)

        self._db.commit()
        return item_id

    def tag_item(self, item_id: str, tag_type: str, tag_value: str) -> None:
        """Add a single tag to an existing item. Duplicate tags are silently ignored."""
        t = tag_type.strip().lower()
        if t not in TAG_TYPES:
            t = "keyword"
        v = str(tag_value).strip()
        if not v:
            return
        self._db.execute(
            "INSERT OR IGNORE INTO intelligence_tags(item_id, tag_type, tag_value) VALUES(?,?,?)",
            (item_id, t, v),
        )
        self._db.commit()

    def tag_item_bulk(self, item_id: str, tags: list[Any]) -> int:
        """
        Add multiple tags to an item in one transaction.

        Accepts:
          - list of dicts: [{"type": "entity", "value": "PAR Technology"}, ...]
          - list of 2-tuples: [("entity", "PAR Technology"), ...]

        Returns count of tags successfully applied.
        """
        applied = 0
        rows: list[tuple[str, str, str]] = []
        for raw in (tags or []):
            norm = _normalize_tag(raw)
            if norm:
                rows.append((item_id, norm["type"], norm["value"]))
                applied += 1
        if rows:
            self._db.executemany(
                "INSERT OR IGNORE INTO intelligence_tags(item_id, tag_type, tag_value) VALUES(?,?,?)",
                rows,
            )
            self._db.commit()
        return applied

    def mark_communicated(self, item_id: str, comm_date: str | None = None) -> bool:
        """
        Mark that this item was surfaced to the user in a brief.
        Sets communicated_date (once — does not overwrite) and last_surfaced_date.
        Returns True if item exists, False if not found.
        """
        now_date = comm_date or _today_iso()
        now = _now_iso()
        result = self._db.execute(
            """
            UPDATE intelligence_items
            SET last_surfaced_date = ?,
                communicated_date = CASE WHEN communicated_date IS NULL THEN ? ELSE communicated_date END,
                updated_at = ?
            WHERE id = ?
            """,
            (now_date, now_date, now, item_id),
        )
        self._db.commit()
        return result.rowcount > 0

    def update_lifecycle(self, item_id: str, state: str) -> bool:
        """
        Update the lifecycle state of an item.
        Returns True if item was found and updated.
        """
        if state not in LIFECYCLE_STATES:
            raise ValueError(f"Invalid lifecycle state '{state}'. Must be one of: {LIFECYCLE_STATES}")
        now = _now_iso()
        result = self._db.execute(
            "UPDATE intelligence_items SET lifecycle_state = ?, updated_at = ? WHERE id = ?",
            (state, now, item_id),
        )
        self._db.commit()
        return result.rowcount > 0

    def suppress_item(self, item_id: str) -> bool:
        """
        Suppress an item and increment its suppression counter.
        Returns True if item was found.
        """
        now = _now_iso()
        result = self._db.execute(
            """
            UPDATE intelligence_items
            SET lifecycle_state = 'suppressed',
                suppression_count = suppression_count + 1,
                updated_at = ?
            WHERE id = ?
            """,
            (now, item_id),
        )
        self._db.commit()
        return result.rowcount > 0

    def relate_items(
        self,
        from_id: str,
        to_id: str,
        relation_type: str = "related",
    ) -> None:
        """
        Create a directional relationship between two items.
        Silently ignored if the relation already exists.
        """
        if relation_type not in RELATION_TYPES:
            relation_type = "related"
        self._db.execute(
            "INSERT OR IGNORE INTO intelligence_relations(from_id, to_id, relation_type) VALUES(?,?,?)",
            (from_id, to_id, relation_type),
        )
        self._db.commit()

    # ------------------------------------------------------------------ #
    # Read operations — single item
    # ------------------------------------------------------------------ #

    def get_item(self, item_id: str) -> dict | None:
        """Fetch a single item by ID. Returns None if not found."""
        row = self._db.execute(
            "SELECT * FROM intelligence_items WHERE id = ?", (item_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_item_with_tags(self, item_id: str) -> dict | None:
        """
        Fetch item by ID with its tags list included.
        Returns None if not found. Tags are [{type, value}, ...].
        """
        item = self.get_item(item_id)
        if item is None:
            return None
        tags = self._db.execute(
            "SELECT tag_type, tag_value FROM intelligence_tags WHERE item_id = ?",
            (item_id,),
        ).fetchall()
        item["tags"] = [{"type": r["tag_type"], "value": r["tag_value"]} for r in tags]
        return item

    def get_relations(self, item_id: str) -> list[dict]:
        """Fetch all relations for an item (both outbound and inbound)."""
        rows = self._db.execute(
            """
            SELECT from_id, to_id, relation_type,
                   CASE WHEN from_id = ? THEN 'outbound' ELSE 'inbound' END AS direction
            FROM intelligence_relations
            WHERE from_id = ? OR to_id = ?
            """,
            (item_id, item_id, item_id),
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ #
    # Read operations — search / query
    # ------------------------------------------------------------------ #

    def search(
        self,
        *,
        entity: str | None = None,
        contact: str | None = None,
        product: str | None = None,
        tag_type: str | None = None,
        tag_value: str | None = None,
        source_type: str | list[str] | None = None,
        lifecycle_state: str | list[str] | None = None,
        confidence: str | list[str] | None = None,
        days: int | None = None,
        since: str | None = None,
        signal_type: str | None = None,
        domain: str | None = None,
        include_suppressed: bool = False,
        include_resolved: bool = False,
        limit: int = 50,
        sort_by: str = "gathered_date",
    ) -> list[dict]:
        """
        Flexible search across the intelligence store.

        All filters use AND logic. Tag filters (entity, contact, etc.) match
        items that have at least one tag with that type+value combination.

        Parameters
        ----------
        entity          Match items tagged entity = <value> (case-insensitive)
        contact         Match items tagged contact = <value>
        product         Match items tagged product = <value>
        tag_type        Generic tag type filter (use with tag_value)
        tag_value       Generic tag value filter (use with tag_type)
        signal_type     Match items tagged signal_type = <value>
        domain          Match items tagged domain = <value>
        source_type     Single value or list; filters source_type column
        lifecycle_state Single value or list; filters lifecycle_state column
        confidence      Single value or list; filters confidence column
        days            Items gathered in the last N days
        since           Items gathered on or after this ISO date
        include_suppressed  Include suppressed items (default False)
        include_resolved    Include resolved items (default False)
        limit           Max results (default 50)
        sort_by         Column name; default "gathered_date" (desc)

        Returns list of item dicts (without tags — call get_item_with_tags for tags).
        """
        clauses: list[str] = []
        params: list[Any] = []

        # Exclude lifecycle states unless explicitly requested
        excluded_states: list[str] = []
        if not include_suppressed:
            excluded_states.append("suppressed")
        if not include_resolved:
            excluded_states.append("resolved")

        # Handle lifecycle_state filter (overrides the default excludes for
        # explicitly-requested states)
        explicit_states: list[str] = []
        if lifecycle_state:
            explicit_states = (
                [lifecycle_state]
                if isinstance(lifecycle_state, str)
                else list(lifecycle_state)
            )
            excluded_states = [s for s in excluded_states if s not in explicit_states]

        if explicit_states:
            placeholders = ",".join("?" * len(explicit_states))
            clauses.append(f"i.lifecycle_state IN ({placeholders})")
            params.extend(explicit_states)
        elif excluded_states:
            placeholders = ",".join("?" * len(excluded_states))
            clauses.append(f"i.lifecycle_state NOT IN ({placeholders})")
            params.extend(excluded_states)

        # source_type filter
        if source_type:
            st_list = [source_type] if isinstance(source_type, str) else list(source_type)
            placeholders = ",".join("?" * len(st_list))
            clauses.append(f"i.source_type IN ({placeholders})")
            params.extend(st_list)

        # confidence filter
        if confidence:
            conf_list = [confidence] if isinstance(confidence, str) else list(confidence)
            placeholders = ",".join("?" * len(conf_list))
            clauses.append(f"i.confidence IN ({placeholders})")
            params.extend(conf_list)

        # Date range filter
        if days is not None:
            cutoff = (date.today() - timedelta(days=days)).isoformat()
            clauses.append("i.gathered_date >= ?")
            params.append(cutoff)
        elif since:
            clauses.append("i.gathered_date >= ?")
            params.append(since)

        # Tag filters — entity, contact, product, signal_type, domain
        tag_conditions: list[tuple[str, str]] = []
        if entity:
            tag_conditions.append(("entity", entity))
        if contact:
            tag_conditions.append(("contact", contact))
        if product:
            tag_conditions.append(("product", product))
        if signal_type:
            tag_conditions.append(("signal_type", signal_type))
        if domain:
            tag_conditions.append(("domain", domain))
        if tag_type and tag_value:
            tag_conditions.append((tag_type, tag_value))

        # Build JOIN-based subquery for each tag condition
        base_query: str
        if tag_conditions:
            # Each tag condition produces an EXISTS subquery
            for tc_type, tc_value in tag_conditions:
                clauses.append(
                    "EXISTS (SELECT 1 FROM intelligence_tags t "
                    "WHERE t.item_id = i.id AND t.tag_type = ? "
                    "AND LOWER(t.tag_value) = LOWER(?))"
                )
                params.extend([tc_type, tc_value])

        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

        # Validate sort_by to prevent injection
        safe_sort_cols = {
            "gathered_date", "created_at", "updated_at",
            "confidence", "lifecycle_state", "source_type", "title",
        }
        sort_col = sort_by if sort_by in safe_sort_cols else "gathered_date"

        sql = f"""
            SELECT i.*
            FROM intelligence_items i
            {where}
            ORDER BY i.{sort_col} DESC
            LIMIT ?
        """
        params.append(limit)

        rows = self._db.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def get_by_entity(self, entity_name: str, days: int = 90) -> list[dict]:
        """
        Retrieve all intelligence items tagged with this entity name.
        Convenience wrapper around search().
        """
        return self.search(entity=entity_name, days=days, limit=100)

    def get_recent(self, days: int = 7, limit: int = 50) -> list[dict]:
        """Retrieve most recent items, newest first."""
        return self.search(days=days, limit=limit, sort_by="gathered_date")

    def get_uncommunicated(self, limit: int = 50) -> list[dict]:
        """
        Items that have not yet been surfaced to the user (communicated_date IS NULL).
        Only returns new/active/monitoring state items.
        """
        rows = self._db.execute(
            """
            SELECT * FROM intelligence_items
            WHERE communicated_date IS NULL
              AND lifecycle_state IN ('new', 'active', 'monitoring')
            ORDER BY gathered_date DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_by_thread(self, thread_id: str) -> list[dict]:
        """Retrieve items tagged with a specific active thread ID."""
        return self.search(tag_type="thread_ref", tag_value=thread_id, limit=50)

    def get_by_signal_type(self, signal_type: str, days: int = 60) -> list[dict]:
        """Retrieve items with a specific signal type (e.g. 'acquisition')."""
        return self.search(signal_type=signal_type, days=days, limit=50)

    # ------------------------------------------------------------------ #
    # Analysis
    # ------------------------------------------------------------------ #

    def entity_summary(self, entity_name: str, days: int = 90) -> dict:
        """
        Build a summary of everything RB knows about an entity.

        Returns:
        {
            entity:          str,
            item_count:      int,
            signal_types:    list[str],
            sources:         list[str],
            date_range:      {first: ISO, latest: ISO},
            lifecycle_states:{state: count, ...},
            items:           list[dict]   (most recent 10),
        }
        """
        items = self.get_by_entity(entity_name, days=days)
        if not items:
            return {
                "entity": entity_name,
                "item_count": 0,
                "signal_types": [],
                "sources": [],
                "date_range": {"first": None, "latest": None},
                "lifecycle_states": {},
                "items": [],
            }

        item_ids = [i["id"] for i in items]
        ph = ",".join("?" * len(item_ids))

        # Signal types for this entity's items
        sig_rows = self._db.execute(
            f"SELECT DISTINCT tag_value FROM intelligence_tags "
            f"WHERE tag_type='signal_type' AND item_id IN ({ph})",
            item_ids,
        ).fetchall()
        signal_types = [r[0] for r in sig_rows]

        # Lifecycle state counts
        state_counts: dict[str, int] = defaultdict(int)
        for i in items:
            state_counts[i["lifecycle_state"]] += 1

        dates = sorted(i["gathered_date"] for i in items)

        return {
            "entity": entity_name,
            "item_count": len(items),
            "signal_types": signal_types,
            "sources": sorted({i["source_name"] for i in items}),
            "date_range": {"first": dates[0], "latest": dates[-1]},
            "lifecycle_states": dict(state_counts),
            "items": items[:10],
        }

    def find_convergences(
        self,
        min_entity_hits: int = 2,
        days: int = 30,
        entity_names: list[str] | None = None,
        min_distinct_days: int = 1,
    ) -> list[dict]:
        """
        Find entities that appear across multiple intelligence items.

        This is the cross-time foundation for connect_the_dots: an entity
        appearing in 3 items across 2 source types is a pattern worth surfacing,
        even if no single item is new.

        Parameters
        ----------
        min_entity_hits    Minimum number of items per entity to qualify (default 2)
        days               Look-back window in days (default 30)
        entity_names       Optional allowlist; if provided, only these entities qualify
        min_distinct_days  Minimum number of distinct gathered_date values the
                            entity's items must span to qualify (default 1 --
                            no filtering, preserving the raw signal every
                            existing caller/test already relies on).
                            RB-DEFECT-2026-09-18: this used to be a filter every
                            "sustained pattern" caller had to remember to apply
                            itself after the fact (daily_brief.py and
                            intelligence_assessment.py both did;
                            system/scripts/query_engine.py's ad-hoc "top
                            converging entities" query path did not, and could
                            surface a same-day, single-source burst -- the
                            exact Burger King/Firehouse Subs false-convergence
                            pattern -- as a "sustained pattern." query_engine.py
                            now passes min_distinct_days=2 explicitly, matching
                            the other two callers' own post-hoc filter, instead
                            of being the one caller with none.

        Returns list of convergence dicts:
        {
            entity:       str,
            item_count:   int,
            signal_types: list[str],
            sources:      list[str],
            items:        list[dict]   (up to 5 most recent),
            distinct_days: int,
            confidence:   str  ("high"/"medium"/"low"), derived from item_count,
                          source diversity, AND distinct_days together -- not
                          item_count alone (handoff requirement: "prevent a
                          high article count from creating a high-confidence
                          convergence when the entity links are weak").
        }
        Sorted by item_count descending.
        """
        since = (date.today() - timedelta(days=days)).isoformat()

        # All entity tags for items in window, excluding suppressed/resolved
        rows = self._db.execute(
            """
            SELECT t.tag_value   AS entity,
                   t.item_id,
                   i.title,
                   i.source_name,
                   i.confidence,
                   i.lifecycle_state,
                   i.gathered_date
            FROM   intelligence_tags t
            JOIN   intelligence_items i ON t.item_id = i.id
            WHERE  t.tag_type = 'entity'
              AND  i.gathered_date >= ?
              AND  i.lifecycle_state NOT IN ('suppressed', 'resolved')
            ORDER  BY t.tag_value, i.gathered_date DESC
            """,
            (since,),
        ).fetchall()

        # Group by entity
        entity_items: dict[str, list[dict]] = defaultdict(list)
        for r in rows:
            entity_items[r["entity"]].append({
                "item_id": r["item_id"],
                "title": r["title"],
                "source_name": r["source_name"],
                "confidence": r["confidence"],
                "lifecycle_state": r["lifecycle_state"],
                "gathered_date": r["gathered_date"],
            })

        convergences: list[dict] = []
        for entity, e_items in entity_items.items():
            if len(e_items) < min_entity_hits:
                continue
            if entity_names and entity not in entity_names:
                continue

            # Signal types for this entity's items
            e_ids = [i["item_id"] for i in e_items]
            ph = ",".join("?" * len(e_ids))
            sig_rows = self._db.execute(
                f"SELECT DISTINCT tag_value FROM intelligence_tags "
                f"WHERE tag_type='signal_type' AND item_id IN ({ph})",
                e_ids,
            ).fetchall()
            signal_types = [r[0] for r in sig_rows]

            # RB-2026-08-25: confirmed live -- the top "sustained pattern"
            # entities on a given day (Burger King, Firehouse Subs, Tim
            # Hortons: 300+ item_count each) had a gathered_date span of
            # exactly one calendar day. item_count and source diversity
            # alone don't distinguish "a real multi-day pattern" from "a
            # same-day burst of coverage from many feeds simultaneously" --
            # only the actual spread of gathered_date values does. Computed
            # from the full e_items set (not the [:5] sample below) so a
            # caller gating on "genuinely sustained" isn't misled by
            # whichever 5 items happened to be most recent.
            distinct_days = len({i["gathered_date"] for i in e_items if i["gathered_date"]})
            if distinct_days < min_distinct_days:
                continue

            sources = sorted({i["source_name"] for i in e_items})
            # Confidence from entity-link STRENGTH, not article count alone:
            # a high item_count with only one source and one day is exactly
            # the false-convergence pattern this defect closed, so it must
            # not outrank a smaller but genuinely multi-source, multi-day
            # pattern. "high" requires real diversity on both axes.
            if len(sources) >= 3 and distinct_days >= 3:
                convergence_confidence = "high"
            elif len(sources) >= 2 and distinct_days >= 2:
                convergence_confidence = "medium"
            else:
                convergence_confidence = "low"

            convergences.append({
                "entity": entity,
                "item_count": len(e_items),
                "signal_types": signal_types,
                "sources": sources,
                "items": e_items[:5],
                "distinct_days": distinct_days,
                "confidence": convergence_confidence,
            })

        convergences.sort(key=lambda x: x["item_count"], reverse=True)
        return convergences

    def cross_entity_convergence(
        self,
        days: int = 30,
        min_shared_items: int = 1,
    ) -> list[dict]:
        """
        Find pairs of entities that co-appear in the same intelligence items.

        Useful for detecting: "PAR Technology and McDonald's both appear
        in 2 items this month" — a convergence the CoS should synthesize.

        Returns list of:
        {
            entity_a:    str,
            entity_b:    str,
            shared_count: int,
            shared_items: list[dict],
        }
        Sorted by shared_count descending.
        """
        since = (date.today() - timedelta(days=days)).isoformat()

        # Map item_id → set of entity tags
        rows = self._db.execute(
            """
            SELECT t.item_id, t.tag_value
            FROM   intelligence_tags t
            JOIN   intelligence_items i ON t.item_id = i.id
            WHERE  t.tag_type = 'entity'
              AND  i.gathered_date >= ?
              AND  i.lifecycle_state NOT IN ('suppressed', 'resolved')
            """,
            (since,),
        ).fetchall()

        item_entities: dict[str, set[str]] = defaultdict(set)
        for r in rows:
            item_entities[r["item_id"]].add(r["tag_value"])

        # Find entity pairs that co-appear
        pair_items: dict[tuple[str, str], list[str]] = defaultdict(list)
        for item_id, entities in item_entities.items():
            entity_list = sorted(entities)
            for i in range(len(entity_list)):
                for j in range(i + 1, len(entity_list)):
                    pair = (entity_list[i], entity_list[j])
                    pair_items[pair].append(item_id)

        result: list[dict] = []
        for (ea, eb), item_ids in pair_items.items():
            if len(item_ids) < min_shared_items:
                continue
            # Fetch titles for shared items
            ph = ",".join("?" * len(item_ids))
            shared = self._db.execute(
                f"SELECT id, title, gathered_date, source_name FROM intelligence_items "
                f"WHERE id IN ({ph}) ORDER BY gathered_date DESC",
                item_ids,
            ).fetchall()
            shared_items = [dict(r) for r in shared]
            result.append({
                "entity_a": ea,
                "entity_b": eb,
                "shared_count": len(item_ids),
                "shared_items": shared_items,
                # RB-2026-08-25: same distinct-day tracking as
                # find_convergences above -- a pair sharing 5 items all
                # gathered the same day is a same-day co-mention, not a
                # "repeatedly co-appear" structural pattern.
                "distinct_days": len({r.get("gathered_date") for r in shared_items if r.get("gathered_date")}),
            })

        result.sort(key=lambda x: x["shared_count"], reverse=True)
        return result

    # ------------------------------------------------------------------ #
    # Format conversion
    # ------------------------------------------------------------------ #

    def to_brief_item(self, item: dict) -> dict:
        """
        Convert a DB item dict to the brief section format expected by daily_brief.py.

        Output format:
        {
            "title":    str,
            "body":     str,
            "source":   str,
            "priority": "act_today" | "monitor" | "ignore",
            "extras": {
                "intel_id":      str,
                "gathered_date": str,
                "confidence":    str,
                "lifecycle":     str,
                "source_url":    str | None,
                "source_type":   str,
            }
        }
        """
        state = item.get("lifecycle_state", "new")
        conf = item.get("confidence", "medium")

        # Map lifecycle + confidence to brief priority
        if state in ("new", "active") and conf in ("high", "medium"):
            priority = "act_today"
        elif state in ("monitoring",) or conf == "low":
            priority = "monitor"
        else:
            priority = "monitor"

        return {
            "title": item.get("title") or "",
            "body": item.get("content") or "",
            "source": item.get("source_name") or "",
            "priority": priority,
            "extras": {
                "intel_id": item.get("id") or "",
                "gathered_date": item.get("gathered_date") or "",
                "confidence": conf,
                "lifecycle": state,
                "source_url": item.get("source_url"),
                "source_type": item.get("source_type") or "unknown",
            },
        }

    # ------------------------------------------------------------------ #
    # Aggregate stats
    # ------------------------------------------------------------------ #

    def stats(self) -> dict:
        """
        Return aggregate statistics about the store.

        {
            total_items:          int,
            items_today:          int,
            items_by_lifecycle:   {state: count},
            items_by_source_type: {type: count},
            items_by_confidence:  {level: count},
            uncommunicated_count: int,
            schema_version:       int,
        }
        """
        total = self._db.execute(
            "SELECT COUNT(*) FROM intelligence_items"
        ).fetchone()[0]

        today = self._db.execute(
            "SELECT COUNT(*) FROM intelligence_items WHERE gathered_date = ?",
            (_today_iso(),),
        ).fetchone()[0]

        lifecycle_rows = self._db.execute(
            "SELECT lifecycle_state, COUNT(*) AS cnt FROM intelligence_items GROUP BY lifecycle_state"
        ).fetchall()
        by_lifecycle = {r["lifecycle_state"]: r["cnt"] for r in lifecycle_rows}

        source_rows = self._db.execute(
            "SELECT source_type, COUNT(*) AS cnt FROM intelligence_items GROUP BY source_type"
        ).fetchall()
        by_source = {r["source_type"]: r["cnt"] for r in source_rows}

        conf_rows = self._db.execute(
            "SELECT confidence, COUNT(*) AS cnt FROM intelligence_items GROUP BY confidence"
        ).fetchall()
        by_conf = {r["confidence"]: r["cnt"] for r in conf_rows}

        uncommunicated = self._db.execute(
            "SELECT COUNT(*) FROM intelligence_items WHERE communicated_date IS NULL "
            "AND lifecycle_state IN ('new','active','monitoring')"
        ).fetchone()[0]

        version_row = self._db.execute(
            "SELECT value FROM meta WHERE key='schema_version'"
        ).fetchone()
        schema_ver = int(version_row["value"]) if version_row else 0

        return {
            "total_items": total,
            "items_today": today,
            "items_by_lifecycle": by_lifecycle,
            "items_by_source_type": by_source,
            "items_by_confidence": by_conf,
            "uncommunicated_count": uncommunicated,
            "schema_version": schema_ver,
        }

    def meta(self) -> dict[str, str]:
        """Return all rows from the meta table as a dict."""
        rows = self._db.execute("SELECT key, value FROM meta").fetchall()
        return {r["key"]: r["value"] for r in rows}


# ---------------------------------------------------------------------------
# Module-level convenience functions (use sparingly — prefer `with` block)
# ---------------------------------------------------------------------------

def open_db(db_path: Path | None = None) -> IntelligenceDB:
    """Open and return an IntelligenceDB. Caller is responsible for close()."""
    return IntelligenceDB(db_path).open()


@contextmanager
def intelligence_db(db_path: Path | None = None) -> Generator[IntelligenceDB, None, None]:
    """
    Context-manager shorthand:

        from intelligence_db import intelligence_db
        with intelligence_db() as db:
            db.add_item(...)
    """
    db = IntelligenceDB(db_path)
    db.open()
    try:
        yield db
    finally:
        db.close()
