#!/usr/bin/env python3
"""
Relationship Builder — MCP server.

Exposes the deterministic scripts in ../scripts/ as MCP tools so any
MCP-capable client (Claude, future ChatGPT, Cursor, Zed, etc.) can call
them without filesystem access to this folder.

Run:
    pip install mcp --break-system-packages
    python3 system/mcp/server.py

Or register in your MCP client config. Example for Claude Desktop:
    {
      "mcpServers": {
        "relationship-builder": {
          "command": "python3",
          "args": ["/absolute/path/to/system/mcp/server.py"]
        }
      }
    }

Tools exposed:
    rb.daily_brief           — current daily brief (cached, returns the report dict)
    rb.validate_baseline     — schema + integrity check
    rb.gap_detection         — RC/card/contact-field gaps
    rb.loop_parser           — bucketed loop view for a given date
    rb.network_gap           — unanchored-cluster scoring
    rb.drr_score             — Dynamic Relationship Relevance score (top-N or by id)
    rb.refresh_all           — rebuild every cache
    rb.read_status           — read STATUS.md
    rb.read_manifest         — read MANIFEST.md
    rb.list_protocols        — return protocols/index.json
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import date
from pathlib import Path

# Ensure the scripts/ directory is importable so we can call the compute
# functions directly without subprocess overhead.
SYSTEM_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = SYSTEM_DIR / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import rb_core as core  # noqa: E402
import daily_brief  # noqa: E402
import gap_detection  # noqa: E402
import validate_baseline as vb  # noqa: E402
import mutations  # noqa: E402
import ri_intake  # noqa: E402
import ri_events  # noqa: E402

try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import Tool, TextContent
except ImportError:
    sys.stderr.write(
        "mcp package not installed. Install with:\n"
        "  pip install mcp --break-system-packages\n"
    )
    sys.exit(2)


app = Server("relationship-builder")


# ---------------------------------------------------------------------------
# Tool implementations — each returns a TextContent payload (JSON string).
# ---------------------------------------------------------------------------

def _ok(payload) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps(payload, default=str, indent=2))]


def _read_file(rel_path: str) -> list[TextContent]:
    p = core.PROJECT_DIR / rel_path
    return [TextContent(type="text", text=p.read_text())]


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOLS = [
    Tool(
        name="rb.daily_brief",
        description=(
            "Return today's Relationship Builder daily-brief report (the report "
            "dict that today.md and MANIFEST.md are rendered from). Includes "
            "baseline summary, dormancy crossings, loops bucketed by date, "
            "quiet zones, gap surface, and Circles. Pass `date` to override 'today'."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "date": {
                    "type": "string",
                    "description": "ISO date 'YYYY-MM-DD' to treat as today.",
                },
                "use_cache": {
                    "type": "boolean",
                    "description": "Prefer the cached report if fresh (default true).",
                },
            },
        },
    ),
    Tool(
        name="rb.validate_baseline",
        description="Run schema + integrity checks on baseline_index.json.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="rb.gap_detection",
        description=(
            "Surface structural gaps: RCs without a card, orphan cards, RCs "
            "without last_touch, contact-field gaps (email/phone)."
        ),
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="rb.loop_parser",
        description=(
            "Parse loop_ledger.md into buckets: overdue, due_today, this_week, "
            "future, closed. Pass `date` to override 'today'."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "ISO date 'YYYY-MM-DD'."}
            },
        },
    ),
    Tool(
        name="rb.network_gap",
        description=(
            "Score company-clusters by whether they have an inner-tier RC "
            "anchor. Use `gaps_only=true` to filter to unanchored clusters."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "min_cluster": {"type": "integer", "default": 5},
                "gaps_only": {"type": "boolean", "default": False},
                "limit": {"type": "integer", "default": 25},
            },
        },
    ),
    Tool(
        name="rb.drr_score",
        description=(
            "Dynamic Relationship Relevance score. Pass `id` to explain a "
            "single contact, otherwise returns top-N by score. Use `class_filter` "
            "to restrict to one signal class."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "ISO date 'YYYY-MM-DD'."},
                "id": {"type": "string", "description": "Single contact id to explain."},
                "class_filter": {
                    "type": "string",
                    "enum": ["VC", "NPR", "LMI", "LKI", "RC"],
                },
                "limit": {"type": "integer", "default": 50},
            },
        },
    ),
    Tool(
        name="rb.refresh_all",
        description="Re-run every script with --cache; rebuilds system/.cache/.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="rb.read_status",
        description="Return system/STATUS.md (live/partial/designed/not built).",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="rb.read_manifest",
        description="Return system/MANIFEST.md (current-state snapshot).",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="rb.list_protocols",
        description="Return system/protocols/index.json (all protocol definitions).",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="rb.calendar_overlay",
        description=(
            "Return calendar.json overlaid with baseline + active-thread matches. "
            "Buckets: today / tomorrow / this_week. Flags attendees not in baseline."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "ISO date for 'today'."},
            },
        },
    ),
    Tool(
        name="rb.email_overlay",
        description=(
            "Return email.json overlaid with baseline + active-thread company matches. "
            "Surfaces unread threads from contacts and threads matching active-thread "
            "companies even when the sender isn't yet in baseline."
        ),
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="rb.interaction_overlay",
        description=(
            "Direct interaction signal — phone calls + text messages from "
            "macOS Messages + Call History. Matches handles against baseline "
            "by phone (normalized) or email. Returns matched contacts, proposed "
            "last_touch updates, and unmatched recurring handles (promotion candidates)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "date": {"type": "string"},
                "recent_days": {"type": "integer", "default": 30},
            },
        },
    ),
    Tool(
        name="rb.social_outbound_overlay",
        description=(
            "Engagement signal on your OWN posts. Returns recent posts, "
            "engagement-by-contact (DRR-weighted), engagement silence "
            "(cooling-by-engagement), topic-engagement map, and active-thread "
            "engagement events."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "date": {"type": "string"},
                "recent_days": {"type": "integer", "default": 30},
            },
        },
    ),
    Tool(
        name="rb.post_recommendations",
        description=(
            "Ranked post recommendations: topics + threads + specific contacts "
            "each post would warm. Grounded in active threads + engagement history."
        ),
        inputSchema={
            "type": "object",
            "properties": {"date": {"type": "string"}},
        },
    ),
    Tool(
        name="rb.my_post_add",
        description="Append a single own-post to system/inbox/social.own_posts.json.",
        inputSchema={
            "type": "object",
            "required": ["text"],
            "properties": {
                "text": {"type": "string"},
                "posted_at": {"type": "string"},
                "topics": {"type": "array", "items": {"type": "string"}},
                "url": {"type": "string"},
                "platform": {"type": "string", "default": "linkedin"},
                "likes": {"type": "integer"},
                "comments": {"type": "integer"},
                "shares": {"type": "integer"},
                "impressions": {"type": "integer"},
                "id": {"type": "string"},
            },
        },
    ),
    Tool(
        name="rb.engagement_add",
        description="Append a single engagement event to system/inbox/social.engagement.json.",
        inputSchema={
            "type": "object",
            "required": ["post_id", "type", "engager_name"],
            "properties": {
                "post_id": {"type": "string"},
                "type": {"type": "string", "enum": ["like", "comment", "share"]},
                "engager_name": {"type": "string"},
                "engager_url": {"type": "string"},
                "at": {"type": "string"},
                "comment_text": {"type": "string"},
            },
        },
    ),
    Tool(
        name="rb.network_analysis",
        description=(
            "Run the personal network analysis. Returns strengths, weaknesses, "
            "bridges, composition health, cluster diversity, and ranked "
            "recommendations. The strategic report card for the operator's network."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "date": {"type": "string"},
            },
        },
    ),
    Tool(
        name="rb.find_intro",
        description=(
            "Find candidate broker paths to a target. Target can be a person "
            "id, person name, or company name. Returns top-N ranked brokers "
            "with reasons grounded in DRR + proximity (shared company, shared "
            "Circles, active-thread overlap), plus heuristic suppressions."
        ),
        inputSchema={
            "type": "object",
            "required": ["target"],
            "properties": {
                "target": {"type": "string"},
                "limit": {"type": "integer", "default": 3},
                "date": {"type": "string"},
                "include_suppressed": {"type": "boolean", "default": False},
            },
        },
    ),
    Tool(
        name="rb.social_overlay",
        description=(
            "Return social.feed.json overlaid with baseline + active-thread matches. "
            "Surfaces recent posts from contacts in your graph, posts touching "
            "active-thread companies, and topic clusters across multiple posts."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "recent_days": {"type": "integer", "default": 14},
            },
        },
    ),
    Tool(
        name="rb.recent_sessions",
        description=(
            "Return the last N session memory entries (frontmatter + body). "
            "Use this at the start of a new session to load context that "
            "wasn't carried by the file system."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 3},
            },
        },
    ),
    Tool(
        name="rb.session_end",
        description=(
            "Write a session memory file. Pass a structured summary of what "
            "happened in this session: focus_areas, threads_touched, "
            "contacts_touched, mutations counts, status, next_session_should, "
            "and a body dict with what_we_worked_on / what_was_decided / "
            "in_flight_at_session_end / notable_findings."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "date": {"type": "string"},
                "end_time": {"type": "string"},
                "duration_estimate": {"type": "string"},
                "focus_areas": {"type": "array", "items": {"type": "string"}},
                "threads_touched": {"type": "array", "items": {"type": "string"}},
                "contacts_touched": {"type": "array", "items": {"type": "string"}},
                "mutations": {"type": "object"},
                "status": {"type": "string", "enum": ["complete", "partial", "aborted"]},
                "next_session_should": {"type": "string"},
                "body": {
                    "type": "object",
                    "properties": {
                        "what_we_worked_on": {"type": "array", "items": {"type": "string"}},
                        "what_was_decided": {"type": "array", "items": {"type": "string"}},
                        "in_flight_at_session_end": {"type": "array", "items": {"type": "string"}},
                        "notable_findings": {"type": "array", "items": {"type": "string"}},
                        "open_questions": {"type": "array", "items": {"type": "string"}},
                        "followups": {"type": "array", "items": {"type": "string"}},
                    },
                },
            },
        },
    ),
    Tool(
        name="rb.social_add",
        description=(
            "Append a single post to system/inbox/social.feed.json — for manual "
            "paste of a high-value post you read. Author matching against baseline "
            "happens at overlay time."
        ),
        inputSchema={
            "type": "object",
            "required": ["author_name", "text"],
            "properties": {
                "author_name": {"type": "string"},
                "text": {"type": "string"},
                "posted_at": {"type": "string", "description": "ISO date."},
                "author_url": {"type": "string"},
                "author_headline": {"type": "string"},
                "url": {"type": "string"},
                "platform": {"type": "string", "default": "linkedin"},
                "likes": {"type": "integer"},
                "comments": {"type": "integer"},
                "shares": {"type": "integer"},
            },
        },
    ),
    Tool(
        name="rb.list_active_threads",
        description="Return active strategic threads from system/active_threads.yaml.",
        inputSchema={
            "type": "object",
            "properties": {
                "open_only": {"type": "boolean", "default": True},
            },
        },
    ),
    # ----- write tools -----
    Tool(
        name="rb.loop_add",
        description=(
            "Append a new loop to loop_ledger.md. Loop id is auto-assigned "
            "as L-<today>-<NNN> unless an explicit id is passed."
        ),
        inputSchema={
            "type": "object",
            "required": ["party", "description", "target"],
            "properties": {
                "party": {"type": "string"},
                "description": {"type": "string"},
                "target": {"type": "string", "description": "ISO date for closure target."},
                "opened": {"type": "string", "description": "ISO date (default: today)."},
                "id": {"type": "string", "description": "Override the loop id."},
            },
        },
    ),
    Tool(
        name="rb.loop_close",
        description="Mark a loop closed with a reason note.",
        inputSchema={
            "type": "object",
            "required": ["id", "reason"],
            "properties": {
                "id": {"type": "string"},
                "reason": {"type": "string"},
            },
        },
    ),
    Tool(
        name="rb.touch",
        description="Update the last_touch date for an existing baseline entry.",
        inputSchema={
            "type": "object",
            "required": ["id"],
            "properties": {
                "id": {"type": "string", "description": "Baseline entry id."},
                "date": {"type": "string", "description": "ISO date (default: today)."},
                "source": {"type": "string", "description": "Optional source tag to append to sources[]."},
            },
        },
    ),
    Tool(
        name="rb.contact_add",
        description="Add a new entry to baseline_index.json. Validates before persisting.",
        inputSchema={
            "type": "object",
            "required": ["id", "name", "signal_class"],
            "properties": {
                "id": {"type": "string"},
                "name": {"type": "string"},
                "signal_class": {"type": "string", "enum": ["VC", "NPR", "LMI", "LKI", "RC"]},
                "company": {"type": "string"},
                "role": {"type": "string"},
                "linkedin": {"type": "string"},
                "email": {"type": "string"},
                "phone": {"type": "string"},
                "last_touch": {"type": "string"},
                "rc_tier": {"type": "string", "enum": ["inner", "broader", "dormant_valuable"]},
                "source": {"type": "string"},
                "notes": {"type": "string"},
            },
        },
    ),
    Tool(
        name="rb.thread_open",
        description="Open a new active strategic thread.",
        inputSchema={
            "type": "object",
            "required": ["id", "title", "type"],
            "properties": {
                "id": {"type": "string"},
                "title": {"type": "string"},
                "type": {
                    "type": "string",
                    "enum": ["job_opportunity", "business_engagement", "partnership",
                             "chapter_activation", "role_search", "account_pursuit"],
                },
                "people": {"type": "array", "items": {"type": "string"}},
                "companies": {"type": "array", "items": {"type": "string"}},
                "context": {"type": "string"},
                "current_state": {"type": "string"},
                "target_close": {"type": "string"},
                "boost_for_brief": {"type": "string", "enum": ["high", "medium", "low"]},
                "boost_score": {"type": "number"},
            },
        },
    ),
    Tool(
        name="rb.thread_close",
        description="Mark an active thread closed.",
        inputSchema={
            "type": "object",
            "required": ["id"],
            "properties": {
                "id": {"type": "string"},
                "reason": {"type": "string"},
            },
        },
    ),
    Tool(
        name="rb.ri_intake_review",
        description=(
            "Review-first RI intake (P-021). Dispatches by source_type, "
            "captures the input as an immutable RI event, and returns the "
            "proposed mutation bundle plus persistence_status. No canonical "
            "state is changed by this call."
        ),
        inputSchema={
            "type": "object",
            "required": ["source_type"],
            "properties": {
                "source_type": {
                    "type": "string",
                    "enum": [
                        "manual_text",
                        "linkedin_screenshot",
                        "fathom_manual_paste",
                        "zoom_manual_paste",
                        "email_paste",
                        "recruiting_update",
                    ],
                },
                "raw_text": {"type": "string"},
                "summary": {"type": "string"},
                "event_at": {"type": "string"},
                "event_at_confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                "event_at_source": {"type": "string"},
                "captured_at": {"type": "string"},
                "contact_id": {"type": "string"},
                "name": {"type": "string"},
                "organization": {"type": "string"},
                "opportunity": {"type": "string"},
                "people": {"type": "array", "items": {"type": "object"}},
                "companies": {"type": "array", "items": {"type": "object"}},
                "signal_type": {"type": "string"},
                "confidence": {"type": "number"},
                "trace_id": {"type": "string"},
                "source_ref": {"type": "object"},
            },
        },
    ),
    Tool(
        name="rb.ri_events_recent",
        description=(
            "Read recent RI events from the JSONL stream, newest first. "
            "Filters by limit, since, source_type, persistence_status."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 20},
                "since": {"type": "string"},
                "source_type": {"type": "string"},
                "persistence_status": {"type": "string"},
            },
        },
    ),
    Tool(
        name="rb.ri_event_confirm",
        description=(
            "Confirm or reject the proposed mutations attached to an RI event. "
            "On real-run (dry_run=false) executes the accepted operations via "
            "mutations.py and writes a follow-up RI event. dry_run=true shapes "
            "the response without touching canonical state."
        ),
        inputSchema={
            "type": "object",
            "required": ["event_id"],
            "properties": {
                "event_id": {"type": "string"},
                "accept": {"type": "array", "items": {"type": "string"}},
                "reject": {"type": "array", "items": {"type": "string"}},
                "dry_run": {"type": "boolean", "default": False},
            },
        },
    ),
]


@app.list_tools()
async def list_tools() -> list[Tool]:
    return TOOLS


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name == "rb.daily_brief":
        d = date.fromisoformat(arguments["date"]) if arguments.get("date") else date.today()
        if arguments.get("use_cache", True):
            cached = core.read_cache("daily_brief")
            if cached and cached.get("today") == d.isoformat():
                return _ok(cached)
        return _ok(daily_brief.build_report(d))

    if name == "rb.validate_baseline":
        return _ok({
            "integrity": vb.integrity_checks(core.load_baseline(), date.today()),
        })

    if name == "rb.gap_detection":
        return _ok(gap_detection.build_report())

    if name == "rb.loop_parser":
        d = date.fromisoformat(arguments["date"]) if arguments.get("date") else date.today()
        loops = core.parse_loop_ledger()
        buckets = core.loops_by_status(loops, d)
        from dataclasses import asdict
        return _ok({
            "today": d.isoformat(),
            "buckets": {
                k: [{**asdict(L), "opened": L.opened.isoformat(), "target": L.target.isoformat()} for L in v]
                for k, v in buckets.items()
            },
            "totals": {k: len(v) for k, v in buckets.items()},
        })

    if name == "rb.network_gap":
        rows = core.cluster_inner_anchor_score(
            core.load_baseline(),
            min_cluster=int(arguments.get("min_cluster", 5)),
        )
        if arguments.get("gaps_only"):
            rows = [r for r in rows if r["anchor_gap"]]
        return _ok(rows[: int(arguments.get("limit", 25))])

    if name == "rb.drr_score":
        d = date.fromisoformat(arguments["date"]) if arguments.get("date") else date.today()
        baseline = core.load_baseline()
        if arguments.get("id"):
            match = [e for e in baseline if e.get("id") == arguments["id"]]
            if not match:
                return _ok({"error": f"no entry with id={arguments['id']!r}"})
            return _ok(core.drr_score(match[0], d))
        rows = [core.drr_score(e, d) for e in baseline]
        cf = arguments.get("class_filter")
        if cf:
            rows = [r for r in rows if r["signal_class"] == cf]
        rows.sort(key=lambda r: r["score"], reverse=True)
        return _ok(rows[: int(arguments.get("limit", 50))])

    if name == "rb.refresh_all":
        import subprocess
        rc = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "refresh_all.py")],
            capture_output=True, text=True,
        )
        return _ok({"returncode": rc.returncode, "stdout": rc.stdout, "stderr": rc.stderr})

    if name == "rb.read_status":
        return _read_file("system/STATUS.md")
    if name == "rb.read_manifest":
        return _read_file("system/MANIFEST.md")
    if name == "rb.list_protocols":
        return _ok(json.loads((SYSTEM_DIR / "protocols" / "index.json").read_text()))

    if name == "rb.calendar_overlay":
        d = date.fromisoformat(arguments["date"]) if arguments.get("date") else date.today()
        return _ok(core.calendar_overlay(d))

    if name == "rb.email_overlay":
        return _ok(core.email_overlay())

    if name == "rb.social_overlay":
        return _ok(core.social_overlay(recent_days=int(arguments.get("recent_days", 14))))

    if name == "rb.interaction_overlay":
        d = date.fromisoformat(arguments["date"]) if arguments.get("date") else date.today()
        return _ok(core.interaction_overlay(today=d, recent_days=int(arguments.get("recent_days", 30))))

    if name == "rb.social_outbound_overlay":
        d = date.fromisoformat(arguments["date"]) if arguments.get("date") else date.today()
        return _ok(core.social_outbound_overlay(today=d, recent_days=int(arguments.get("recent_days", 30))))

    if name == "rb.post_recommendations":
        d = date.fromisoformat(arguments["date"]) if arguments.get("date") else date.today()
        return _ok(core.post_recommendations(today=d))

    if name == "rb.my_post_add":
        defaults = {
            "posted_at": None, "topics": None, "url": None, "platform": "linkedin",
            "likes": None, "comments": None, "shares": None, "impressions": None,
            "id": None, "dry_run": False,
        }
        merged = {**defaults, **arguments}
        ns = type("Ns", (), merged)()
        rc = mutations.cmd_my_post_add(ns)
        return _ok({"ok": rc == 0})

    if name == "rb.engagement_add":
        defaults = {
            "engager_url": None, "at": None, "comment_text": None, "dry_run": False,
        }
        merged = {**defaults, **arguments}
        ns = type("Ns", (), merged)()
        rc = mutations.cmd_engagement_add(ns)
        return _ok({"ok": rc == 0})

    if name == "rb.network_analysis":
        d = date.fromisoformat(arguments["date"]) if arguments.get("date") else date.today()
        return _ok(core.network_analysis(today=d))

    if name == "rb.find_intro":
        d = date.fromisoformat(arguments["date"]) if arguments.get("date") else date.today()
        return _ok(core.find_intro_paths(
            arguments["target"],
            limit=int(arguments.get("limit", 3)),
            today=d,
            include_suppressed=bool(arguments.get("include_suppressed", False)),
        ))

    if name == "rb.recent_sessions":
        return _ok(core.load_recent_sessions(limit=int(arguments.get("limit", 3))))

    if name == "rb.session_end":
        import session_writer, session_index
        target = session_writer.write_session(arguments)
        idx = session_index.build_index()
        session_index.INDEX_PATH.write_text(json.dumps(idx, indent=2) + "\n")
        return _ok({"ok": True, "file": str(target.relative_to(core.PROJECT_DIR)),
                    "indexed_count": idx["count"]})

    if name == "rb.social_add":
        defaults = {
            "posted_at": None, "author_url": None, "author_headline": None,
            "url": None, "platform": "linkedin",
            "likes": None, "comments": None, "shares": None,
            "id": None, "dry_run": False,
        }
        merged = {**defaults, **arguments}
        ns = type("Ns", (), merged)()
        rc = mutations.cmd_social_add(ns)
        return _ok({"ok": rc == 0})

    if name == "rb.list_active_threads":
        threads = core.load_active_threads()
        if arguments.get("open_only", True):
            threads = [t for t in threads if t.get("status") == "open"]
        return _ok(threads)

    # ----- write tools -----
    if name == "rb.loop_add":
        ns = type("Ns", (), dict(arguments, dry_run=False, id=arguments.get("id"),
                                  opened=arguments.get("opened")))()
        rc = mutations.cmd_loop_add(ns)
        return _ok({"ok": rc == 0})

    if name == "rb.loop_close":
        ns = type("Ns", (), dict(arguments, dry_run=False))()
        rc = mutations.cmd_loop_close(ns)
        return _ok({"ok": rc == 0})

    if name == "rb.touch":
        try:
            result = mutations.touch_contact(
                arguments["id"],
                arguments.get("date"),
                arguments.get("source"),
            )
        except (ValueError, RuntimeError) as exc:
            return _ok({"ok": False, "error": str(exc)})
        return _ok(result)

    if name == "rb.contact_add":
        defaults = {
            "company": None, "role": None, "linkedin": None, "email": None,
            "phone": None, "last_touch": None, "rc_tier": None,
            "source": None, "notes": None, "dry_run": False,
        }
        merged = {**defaults, **arguments}
        ns = type("Ns", (), merged)()
        rc = mutations.cmd_contact_add(ns)
        return _ok({"ok": rc == 0, "id": arguments.get("id")})

    if name == "rb.thread_open":
        defaults = {
            "people": [], "companies": [], "context": "", "state": "",
            "target_close": None, "boost_for_brief": "medium", "boost_score": 1.2,
            "dry_run": False,
        }
        # Allow `current_state` in inputs but map to `state` for the CLI handler
        if "current_state" in arguments and "state" not in arguments:
            arguments = dict(arguments)
            arguments["state"] = arguments.pop("current_state")
        merged = {**defaults, **arguments}
        ns = type("Ns", (), merged)()
        rc = mutations.cmd_thread_open(ns)
        return _ok({"ok": rc == 0, "id": arguments.get("id")})

    if name == "rb.thread_close":
        ns = type("Ns", (), dict(arguments, dry_run=False, reason=arguments.get("reason")))()
        rc = mutations.cmd_thread_close(ns)
        return _ok({"ok": rc == 0})

    if name == "rb.ri_intake_review":
        return _ok(ri_intake.review(arguments))

    if name == "rb.ri_events_recent":
        events = ri_events.load_events(
            since=arguments.get("since"),
            source_type=arguments.get("source_type"),
            persistence_status=arguments.get("persistence_status"),
            limit=int(arguments.get("limit", 20)),
        )
        return _ok({"events": events, "count": len(events)})

    if name == "rb.ri_event_confirm":
        try:
            return _ok(ri_intake.confirm(
                arguments["event_id"],
                accept=arguments.get("accept") or [],
                reject=arguments.get("reject") or [],
                dry_run=bool(arguments.get("dry_run", False)),
            ))
        except ValueError as e:
            return _ok({"error": str(e)})

    return [TextContent(type="text", text=json.dumps({"error": f"unknown tool {name!r}"}))]


async def main() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
