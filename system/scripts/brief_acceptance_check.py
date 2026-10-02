#!/usr/bin/env python3
"""
brief_acceptance_check.py — deterministic quality gate for the Daily Brief
and Intelligence/Team Brief.

RB-2026-08-23: the only prior readiness check (_check_brief_readiness in
morning_pipeline.py, RB-DEFECT-017) computes a real status but runs AFTER
render and send steps have already executed, and its result only lands in
an advisory JSON field nothing gates on -- a brief with stale/missing
required sources gets built, rendered, and emailed exactly as if it were
healthy, with the only trace of trouble sitting in a field nobody has to
open. This module is the actual gate: run it BEFORE the send steps, fold
its result into the pipeline's existing required_ok computation, and
deliver a distinct failure alert instead of the normal brief when it fails.

Two severity tiers, deliberately not conflated:
  FAIL -- a defect in report generation itself (stale/missing required
    source, a core section missing/empty, no evidence trail). Blocks send.
  WARN -- an accurately-reported fact about the business that deserves
    prominent surfacing but is not a reporting defect (e.g. overdue loops
    are real content the brief is supposed to carry, not a malfunction).
    Never blocks send by itself.

Usage:
    python3 brief_acceptance_check.py [--date YYYY-MM-DD] [--json]
    python3 brief_acceptance_check.py --date 2026-08-23 --json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import rb_core as core  # noqa: E402
import task_delivery_check as _tdc  # noqa: E402

SYSTEM_DIR = core.SYSTEM_DIR
CACHE_DIR = SYSTEM_DIR / ".cache"
BRIEFS_DIR = SYSTEM_DIR / "briefs"
RESULT_PATH = CACHE_DIR / "brief_acceptance_result.json"

REQUIRED_SOURCES = {"email:personal", "email:bridgepoint", "calendar:personal",
                    "calendar:bridgepoint", "messages", "calls"}
# RB-2026-09-08: email:bridgepoint and calendar:bridgepoint have had no
# working MCP connector at all since at least 2026-09-06 (confirmed live --
# the connector previously believed to reach BridgePoint's Gmail actually
# points at the personal account instead, and there is no fetch_google.py
# OAuth token set up as a fallback). Treating them as hard-required meant
# the brief was blocked from sending three days running over a gap that
# has no fix available today, not a transient defect. Split them into
# their own set so their staleness surfaces as a prominent WARN -- real,
# visible, not blocking -- while the sources that DO have a working path
# (email/calendar:personal, messages, calls) keep gating for real.
HARD_REQUIRED_SOURCES = {"email:personal", "calendar:personal", "messages", "calls"}
SOFT_REQUIRED_SOURCES = {"email:bridgepoint", "calendar:bridgepoint"}
FRESHNESS_THRESHOLD_HOURS = 6


def check_source_freshness(
    source_health: dict,
    *,
    required_sources: set[str] = REQUIRED_SOURCES,
    threshold_hours: float = FRESHNESS_THRESHOLD_HOURS,
    now: datetime | None = None,
) -> dict:
    """Pure function extracted from morning_pipeline.py's
    _check_brief_readiness so both files check the same threshold against
    the same source_health.json shape instead of carrying two copies of the
    freshness rule that could silently drift apart.

    Returns {"failed": [...], "stale": [...]} -- source names, not booleans,
    so callers can report exactly what's wrong, not just that something is.
    """
    now = now or datetime.now(timezone.utc)
    sources = source_health.get("sources") or {}
    failed: list[str] = []
    stale: list[str] = []

    for src_name in required_sources:
        src_data = sources.get(src_name)
        if src_data is None:
            failed.append(src_name)
            continue
        src_status = src_data.get("status") or ""
        if src_status not in ("refreshed", "ok", "fresh"):
            failed.append(src_name)
            continue
        last_at = src_data.get("last_refreshed_at") or ""
        if last_at:
            try:
                ts = datetime.fromisoformat(last_at)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                age_h = (now - ts).total_seconds() / 3600
                if age_h > threshold_hours:
                    stale.append(src_name)
            except (ValueError, TypeError):
                pass

    return {"failed": failed, "stale": stale}


def _finding(
    check: str, severity: str, passed: bool, detail: str,
    *,
    artifact_scope: str | None = None,
    failure_class: str | None = None,
    repair_action: str | None = None,
    affected_item_ids: list | None = None,
    safe_to_auto_repair: bool | None = None,
    retry_budget: int | None = None,
) -> dict:
    """RB-DEFECT-072: findings are optionally structured so a caller (the
    morning_pipeline.py repair loop) can dispatch a repair without parsing
    `detail` prose. Keyword-only, all default None and omitted from the
    returned dict when unset, so every pre-existing call site (unstructured,
    still the large majority of checks) produces the exact same 4-key dict
    it always did -- structure is additive, not a breaking schema change.

    artifact_scope: "intelligence" | "daily" | "shared" | "delivery"
    failure_class: "content_repairable" | "state_refreshable" |
        "infrastructure_retryable" | "unrecoverable"
    repair_action: a key morning_pipeline.py's repair dispatcher recognizes
        (e.g. "dedup_story_clusters"), not free text.
    affected_item_ids: stable identifiers (URLs, fingerprints) the repair
        action needs -- never require re-deriving them from `detail`.
    """
    f = {"check": check, "severity": severity, "passed": passed, "detail": detail}
    if artifact_scope is not None:
        f["artifact_scope"] = artifact_scope
    if failure_class is not None:
        f["failure_class"] = failure_class
    if repair_action is not None:
        f["repair_action"] = repair_action
    if affected_item_ids is not None:
        f["affected_item_ids"] = affected_item_ids
    if safe_to_auto_repair is not None:
        f["safe_to_auto_repair"] = safe_to_auto_repair
    if retry_budget is not None:
        f["retry_budget"] = retry_budget
    return f


# --- Content-quality checks (RB-2026-09-01) -------------------------------
# Mechanical checks against the real rendered intelligence-brief markdown,
# grounded in a catalog of real past defects in render_intelligence_brief.py
# (duplicate headlines, DOM-artifact leaks, banned headers, etc). Deliberately
# excludes judgment calls (is this synthesis specific enough, is this
# actually restaurant-relevant) -- those aren't mechanically decidable and
# stay out of an automated gate per the agreed detect-don't-auto-fix scope.

# Kept in sync with render_intelligence_brief.py's _GARBLED_TEXT_RE
# (RB-DEFECT-2026-08-11) -- duplicated rather than imported so this gate has
# no dependency on the render module's import-time behavior.
_GARBLED_TEXT_RE = re.compile(
    r"pointer-events|scroll-m[bt]-|threadScrollVars|calc\(var\(--|var\(--[a-z-]+[,)]|"
    r"data-(turn-id|testid)=|dir=\"auto\"",
    re.IGNORECASE,
)

_UNFILLED_PLACEHOLDER_RE = re.compile(
    r"\{\{.*?\}\}|\{[a-zA-Z_][a-zA-Z0-9_]*\}|\[title\]|\[url\]",
    re.IGNORECASE,
)
# NOTE: TBD/TODO were deliberately excluded after live-testing against
# today's real daily brief -- "exact date/time TBD, confirm against FSTEC
# schedule" is genuine, accurate reporting of a real unknown, not a leaked
# template token. Too common in legitimate prose to be a safe mechanical
# signal; a FAIL-severity check must not fire on real good output.

# Top-level "## Section Name" headers only -- excludes "### " sub-headers
# (D+'s per-article entries) via the negative lookahead.
_SECTION_HEADER_RE = re.compile(r"^## (?!#)(.+)$", re.MULTILINE)

# A headline/article link line: optionally prefixed by up to 3 '#'s (D+
# entries render as "### [title](url)"; A-I entries render as a bare
# "[title](url)" line) followed by nothing else on the line -- this
# deliberately does NOT match the separate "**[Read more →](url)**" line
# every item also carries, so each real article is counted once, not twice.
#
# RB-2026-09-24: the title-capture group was `[^\]]+` (stop at the first
# `]`), which silently fails to match ANY badge-prefixed headline -- e.g.
# `[[💰 FUNDING] Firebirds Raises Over $5 Million...](url)` has its own
# `]` closing the `[💰 FUNDING]` badge partway through the real title, so
# `[^\]]+` stops there and the required `\(` right after never matches a
# space. Confirmed live: this made every check built on this regex
# (no_duplicate_story_clusters, no_cross_section_duplicate_headlines,
# no_cross_day_repeat_without_grace, headlines_have_direct_article_links,
# section_headline_counts) blind to every [FUNDING]/[ACQUISITION]/
# [EXEC HIRE]/[BANKRUPTCY]-badged item -- exactly the category showing the
# worst real repeat behavior (the Firebirds funding item rendered
# identically, word-for-word, on both 2026-09-23 and 09-24, with zero
# check ever seeing it to flag it). `.+` is still bounded to one line
# (MULTILINE mode, `.` excludes newline) and anchored to `$`, so it
# correctly captures the FULL title up to the last `](url)` on the line
# regardless of how many brackets the title itself contains -- verified
# against the real "Read more" line format, which still correctly does
# NOT match (it starts with `**`, not `[`).
_TITLE_LINK_RE = re.compile(
    r"^#{0,3}\s*\[(.+)\]\((https?://[^)\s]+)\)\s*$", re.MULTILINE
)

_HEADLINE_COUNT_SECTIONS = (
    "A: World Headlines", "B: National Headlines",
    "C: Restaurant Industry", "D: Restaurant Technology",
)
_DIRECT_LINK_SECTIONS = _HEADLINE_COUNT_SECTIONS + ("D+: Curated Trade Reads",)

_BANNED_PART1_HEADERS = (
    "Chief of Staff Observation:", "RB Take:", "Executive Read",
    "Intelligence Observations", "Strategic Intelligence Bottom Line",
    "Intelligence Assessment",
)


def _split_top_sections(md: str) -> dict[str, str]:
    """Map top-level '## Name' header text to that section's body text."""
    matches = list(_SECTION_HEADER_RE.finditer(md))
    sections: dict[str, str] = {}
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(md)
        sections[m.group(1).strip()] = md[start:end]
    return sections


def _normalize_url(url: str) -> str:
    """Strip query string/fragment so the same article under different
    tracking params still compares as the same URL."""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}{parts.path}"


def check_no_cross_section_duplicate_headlines(intel_md: str) -> dict:
    """RB-DEFECT class: the same story rendered under two different lettered
    sections in one day's document (test_daily_brief_negative_space.py,
    test_world_national_no_duplicate_story.py, test_gp_dot_connections_dedup.py)."""
    seen: dict[str, str] = {}
    dupes: list[str] = []
    for name, body in _split_top_sections(intel_md).items():
        for title, url in _TITLE_LINK_RE.findall(body):
            key = _normalize_url(url)
            prior_section = seen.get(key)
            if prior_section and prior_section != name:
                dupes.append(f"{title!r} appears in both {prior_section!r} and {name!r}")
            else:
                seen.setdefault(key, name)
    return _finding(
        "no_cross_section_duplicate_headlines", "fail", not dupes,
        "; ".join(dupes) if dupes else "no headline repeated across sections",
    )


_STORY_STOPWORDS = {
    "the", "and", "for", "with", "from", "after", "near", "says", "said",
    "calls", "call", "could", "would", "what", "why", "how", "this", "that",
    "into", "over", "amid", "watch", "news", "new", "report", "reports", "current",
    # Repeated market-template vocabulary is not story identity. Company
    # names/tickers remain, so two stories about the same issuer still match.
    "52w", "low", "high", "price", "move", "volume", "spike",
}


def _story_words(title: str) -> set[str]:
    """Small deterministic same-event fingerprint for the release gate.

    This is deliberately broader than URL identity: separate outlets almost
    always use different URLs, which is the defect this backstop must catch.
    """
    words = []
    for raw in re.findall(r"[a-z0-9]+", title.lower()):
        word = {"russian": "russia", "ukrainian": "ukraine"}.get(raw, raw)
        for suffix in ("ing", "ed", "s"):
            if len(word) > len(suffix) + 3 and word.endswith(suffix):
                word = word[:-len(suffix)]
                break
        if len(word) >= 3 and word not in _STORY_STOPWORDS:
            words.append(word)
    return set(words)


def _same_story(words_a: set, words_b: set) -> bool:
    """The one pairwise same-event rule -- extracted so the gate check and
    brief_repair.py's cluster-then-fix logic can never silently diverge
    (RB-DEFECT-072: repair must fix exactly what the gate flagged, nothing
    more/less, or a repair could 'pass' its own rerun while leaving a real
    duplicate the gate would still object to under a different code path)."""
    if not words_a or not words_b:
        return False
    shared = words_a & words_b
    overlap = len(shared) / min(len(words_a), len(words_b))
    return (len(shared) >= 3 and overlap >= 0.30) or (len(shared) >= 2 and overlap >= 0.50)


def cluster_duplicate_headlines(headlines: list[tuple[str, str]]) -> list[list[int]]:
    """Group headline indices (title, url) into same-real-world-event
    clusters via greedy union on _same_story -- a headline joins the first
    existing cluster any of its members match, else starts a new singleton
    cluster. Singletons (len 1) are not duplicates; callers filter for
    len > 1. Same rule as the old pairwise report, just clustered instead of
    reported pair-by-pair, so a 3+-way duplicate resolves to one group with
    one canonical survivor rather than a mesh of overlapping pairs."""
    words_cache = [_story_words(t) for t, _ in headlines]
    clusters: list[list[int]] = []
    for idx, words in enumerate(words_cache):
        joined = False
        if words:
            for cluster in clusters:
                if any(_same_story(words, words_cache[j]) for j in cluster):
                    cluster.append(idx)
                    joined = True
                    break
        if not joined:
            clusters.append([idx])
    return clusters


def check_no_duplicate_story_clusters(intel_md: str) -> dict:
    """Fail when differently-worded headlines describe the same event.

    RB-DEFECT-072: now carries structured repair data (affected_item_ids
    per cluster) instead of only a human-readable pair list, so
    morning_pipeline.py's repair loop can dispatch brief_repair.py's
    dedup_story_clusters action without re-parsing `detail`.
    """
    headlines = _TITLE_LINK_RE.findall(intel_md)
    clusters = cluster_duplicate_headlines(headlines)
    dupe_clusters = [c for c in clusters if len(c) > 1]
    if not dupe_clusters:
        return _finding("no_duplicate_story_clusters", "fail", True,
                         "one headline per real-world story")
    detail_parts = [" / ".join(repr(headlines[i][0]) for i in c) for c in dupe_clusters]
    affected = [
        {"urls": [headlines[i][1] for i in c], "titles": [headlines[i][0] for i in c]}
        for c in dupe_clusters
    ]
    return _finding(
        "no_duplicate_story_clusters", "fail", False,
        "; ".join(detail_parts[:5]),
        artifact_scope="intelligence",
        failure_class="content_repairable",
        repair_action="dedup_story_clusters",
        affected_item_ids=affected,
        safe_to_auto_repair=True,
        retry_budget=1,
    )


def check_daily_policy_alignment(daily_md: str) -> dict:
    """Block recommendations that contradict the current operating policy."""
    prohibited = (
        "BridgePoint business development",
        "Advance one consulting pipeline conversation",
        "while job search progresses",
    )
    hits = [phrase for phrase in prohibited if phrase.lower() in daily_md.lower()]
    return _finding(
        "daily_policy_alignment", "fail", not hits,
        f"prohibited stale-policy recommendation(s): {hits}" if hits
        else "no stale job-search or BridgePoint-growth recommendations",
    )


def check_no_cross_day_repeat_without_grace(intel_md: str, previous_intel_md: str | None) -> dict:
    """Independent backstop on render_intelligence_brief.py's own persistent
    dedup registry (rendered_headlines.json / _is_duplicate / _mark_rendered)
    -- checking the registry itself doesn't work here since it's already
    bumped to today's date by the time this gate runs after render. Instead
    diffs the two real delivered artifacts directly: any URL in both today's
    and yesterday's actual rendered brief is a genuine repeat. WARN-only
    since some repeats (multi-day corporate grace) are intentional -- this
    check doesn't know the per-section grace period, just flags for review."""
    if previous_intel_md is None:
        return _finding("no_cross_day_repeat_without_grace", "warn", None,
                         "not_checked: no prior day's rendered brief available")
    today_urls = {_normalize_url(u) for _, u in _TITLE_LINK_RE.findall(intel_md)}
    yest_urls = {_normalize_url(u) for _, u in _TITLE_LINK_RE.findall(previous_intel_md)}
    overlap = today_urls & yest_urls
    # RB-DEFECT (2026-09-15): this passed the literal string "fail" as the
    # severity, contradicting this function's own docstring ("WARN-only
    # since some repeats (multi-day corporate grace) are intentional") and
    # check_brief()'s own contract ("passed reflects only FAIL-severity
    # findings"). Any legitimate cross-day repeat -- the exact multi-day
    # grace case this check exists to tolerate -- incorrectly blocked the
    # whole brief's acceptance gate instead of just flagging for review.
    return _finding(
        "no_cross_day_repeat_without_grace", "warn", not overlap,
        f"{len(overlap)} URL(s) also appeared in yesterday's rendered brief" if overlap
        else "no URL overlap with yesterday's rendered brief",
    )


def check_no_dom_html_artifacts(rendered_md: dict[str, str]) -> dict:
    """RB-DEFECT-2026-08-11 class: a broken page fetch leaking raw CSS/JS
    class names into rendered text. Independent backstop on
    render_intelligence_brief.py's own inline _looks_garbled() guard, in
    case some other code path bypasses it."""
    hits = []
    for doc_name, text in rendered_md.items():
        m = _GARBLED_TEXT_RE.search(text or "")
        if m:
            hits.append(f"{doc_name}: {m.group(0)!r}")
    return _finding("no_dom_html_artifacts", "fail", not hits,
                     "; ".join(hits) if hits else "no DOM/CSS artifact markers found")


def check_no_unfilled_template_placeholders(rendered_md: dict[str, str]) -> dict:
    """Literal unfilled template tokens (unformatted {var}/{{var}}, literal
    [title]/[url] brackets) surviving into delivered output."""
    hits = []
    for doc_name, text in rendered_md.items():
        m = _UNFILLED_PLACEHOLDER_RE.search(text or "")
        if m:
            hits.append(f"{doc_name}: {m.group(0)!r}")
    return _finding("no_unfilled_template_placeholders", "fail", not hits,
                     "; ".join(hits) if hits else "no unfilled placeholder tokens found")


def check_headlines_have_direct_article_links(intel_md: str) -> dict:
    """Canonical doc FAIL criterion: a headline link that's a bare
    publication homepage instead of a direct article link."""
    sections = _split_top_sections(intel_md)
    bad = []
    for name in _DIRECT_LINK_SECTIONS:
        body = sections.get(name)
        if body is None:
            continue
        for title, url in _TITLE_LINK_RE.findall(body):
            if urlsplit(url).path in ("", "/"):
                bad.append(f"{title!r} in {name!r} links to a bare homepage: {url}")
    return _finding("headlines_have_direct_article_links", "fail", not bad,
                     "; ".join(bad) if bad else "all headline links point to direct articles")


def check_section_headline_counts(intel_md: str) -> dict:
    """Canonical doc: 5-7 headlines per section A-D. WARN, not FAIL -- real
    news volume varies by cycle; start soft, promote later if it proves
    reliable rather than assuming it upfront."""
    sections = _split_top_sections(intel_md)
    off = []
    for name in _HEADLINE_COUNT_SECTIONS:
        body = sections.get(name)
        if body is None:
            continue
        count = len(_TITLE_LINK_RE.findall(body))
        if not (5 <= count <= 7):
            off.append(f"{name!r}: {count} headline(s) (expected 5-7)")
    return _finding("section_headline_counts", "warn", not off,
                     "; ".join(off) if off else "all counted sections have 5-7 headlines")


def check_prohibited_literal_headers_absent(intel_md: str) -> dict:
    """Canonical doc's explicit banned-header list for Part 1 (the
    intelligence brief) -- literal strings that should never appear."""
    hits = [h for h in _BANNED_PART1_HEADERS if h in intel_md]
    return _finding("prohibited_literal_headers_absent", "fail", not hits,
                     f"found banned header(s): {hits}" if hits else "no banned headers present")


def check_proof_dashboard_is_last_section(intel_md: str) -> dict:
    """Canonical doc: Proof Dashboard leading instead of trailing is an
    explicit named failure mode."""
    headers = _SECTION_HEADER_RE.findall(intel_md)
    if not any("Proof Dashboard" in h for h in headers):
        return _finding("proof_dashboard_is_last_section", "fail", None,
                         "not_checked: no Proof Dashboard section present")
    last = headers[-1]
    ok = "Proof Dashboard" in last
    return _finding("proof_dashboard_is_last_section", "fail", ok,
                     f"last section is {last!r}" if ok
                     else f"Proof Dashboard must be the last section; last section is {last!r}")


def check_newsletter_section_has_min_links(intel_md: str) -> dict:
    """Canonical doc: D+ should carry >=3 curated links. Checked at the
    section level, not per-source-newsletter, since D+'s real rendered shape
    is a flat list of '### [title](url)' entries -- confirmed against live
    production output -- not per-newsletter subheadings the per-source
    reading of the canonical doc's wording would assume."""
    body = _split_top_sections(intel_md).get("D+: Curated Trade Reads")
    if body is None:
        return _finding("newsletter_section_has_min_links", "warn", None,
                         "not_checked: D+ section not present")
    count = len(_TITLE_LINK_RE.findall(body))
    return _finding("newsletter_section_has_min_links", "warn", count >= 3,
                     f"{count} curated link(s) in D+")


def check_delivery_readiness(target_date: date) -> dict:
    """RB-DEFECT-072: live, current-run delivery-prerequisite check -- NOT
    trusted from report["delivery_check"], which is embedded once when
    daily_brief.py builds its report and then never refreshed by anything
    (task_delivery_check.py, the source of that cached field, is not a
    pipeline step at all -- confirmed 2026-09-23 by its absence from
    morning_pipeline.py's step list). That snapshot can be from any prior
    run, arbitrarily stale relative to today's actual publication.

    Computed instead directly off live, current state at gate-check time
    (after publish_canonical_artifacts has already run earlier in the same
    pipeline invocation, so by the time this runs the artifact really is
    dated today if publication genuinely succeeded): reuses
    task_delivery_check.py's own `_artifact_check`/`_launchagent_status`
    rather than a second hand-maintained copy of the same logic. Only the
    two checks that are genuine PRE-SEND prerequisites gate here
    (per the required-fix's pre-send/post-send split) -- the rest of
    task_delivery_check.py's broader diagnostic (GPT builder deployment,
    LinkedIn source health, custom_gpt_instructions committed, etc.) is a
    real, useful, separate manual/operational check, not a brief-delivery
    blocker, and deliberately not folded in here.
    """
    today_str = target_date.isoformat()
    art = _tdc._artifact_check()
    la = _tdc._launchagent_status()

    fail_reasons = []
    if not art["exists"]:
        fail_reasons.append("latest_brief.json missing — publish step has not run")
    elif art["artifact_date"] != today_str:
        fail_reasons.append(f"latest artifact dated {art['artifact_date']} (expected {today_str})")

    warn_reasons = []
    if not la["loaded"]:
        warn_reasons.append(f"launch agent not loaded: {la['detail']}")

    if fail_reasons:
        return _finding(
            "delivery_check", "fail", False, "; ".join(fail_reasons),
            artifact_scope="delivery",
            failure_class="state_refreshable",
            repair_action="recompute_delivery_readiness",
            safe_to_auto_repair=True,
            retry_budget=1,
        )
    detail = f"latest artifact dated {today_str}, {art['size_kb']} KB; launch agent loaded={la['loaded']}"
    if warn_reasons:
        detail += "; " + "; ".join(warn_reasons)
    return _finding("delivery_check", "fail", True, detail)


def check_brief(
    report: dict,
    *,
    rendered_md: dict[str, str] | None = None,
    previous_intel_md: str | None = None,
    target_date: date | None = None,
    delivery_readiness: dict | None = None,
) -> dict:
    """report: the `data` payload from system/.cache/daily_brief.json (the
    canonical report dict build_report() produces, unwrapped from its
    _generated_at/_source envelope).

    rendered_md: optional {"intelligence": text, "daily": text} of the
    actual rendered markdown files, for checks that need to verify content
    survived into the real delivered artifact rather than just existing in
    the intermediate report dict. When omitted, those checks are skipped
    with an explicit "not_checked" note rather than silently passing.

    previous_intel_md: optional text of yesterday's real rendered
    intelligence-brief.md, used only by the cross-day-repeat backstop check.

    target_date: the date this check is being run for -- defaults to today.
    Used only by check_delivery_readiness (RB-DEFECT-072) to compute a live
    delivery-prerequisite check instead of trusting a possibly-stale
    embedded report field. Passing an explicit date (as tests do, to check
    a past date's artifacts) means that date's own delivery state is
    checked, not "today"'s.

    delivery_readiness: optional pre-computed delivery_check finding
    (RB-DEFECT-072) -- when omitted, computed live via
    check_delivery_readiness(target_date) exactly as the real pipeline run
    does. Tests pass this explicitly instead of relying on real on-disk
    publish state / launchd, the same reason rendered_md/previous_intel_md
    are injectable rather than always read from disk internally.

    Returns {"passed": bool, "findings": [...], "checked_at": iso}. `passed`
    reflects only FAIL-severity findings -- WARN findings never block it.
    """
    findings: list[dict] = []
    rendered_md = rendered_md or {}
    target_date = target_date or date.today()

    # 1. Freshness -- hard-required sources block send; soft-required
    # sources (RB-2026-09-08: BridgePoint email/calendar, no working
    # connector as of this date) only warn. See SOFT_REQUIRED_SOURCES.
    sh = report.get("source_health") or {}
    fresh = check_source_freshness(sh, required_sources=HARD_REQUIRED_SOURCES)
    if fresh["failed"] or fresh["stale"]:
        findings.append(_finding(
            "freshness", "fail", False,
            f"failed sources: {fresh['failed']}; stale sources: {fresh['stale']}",
        ))
    else:
        findings.append(_finding("freshness", "fail", True, "all hard-required sources fresh"))

    soft_fresh = check_source_freshness(sh, required_sources=SOFT_REQUIRED_SOURCES)
    if soft_fresh["failed"] or soft_fresh["stale"]:
        findings.append(_finding(
            "freshness_soft", "warn", False,
            f"failed sources: {soft_fresh['failed']}; stale sources: {soft_fresh['stale']}",
        ))
    else:
        findings.append(_finding("freshness_soft", "warn", True, "all soft-required sources fresh"))

    # 2. Source/proof counts
    if not sh.get("sources"):
        findings.append(_finding("source_counts", "fail", False,
                                  "source_health.sources missing or empty"))
    else:
        findings.append(_finding("source_counts", "fail", True,
                                  f"{len(sh['sources'])} sources reported"))

    # Priority earnings coverage is self-healing and publication-gated.
    # A failed IR endpoint is not a reader-facing conclusion; the earnings
    # monitor must recover through its alternate sources before the brief is
    # allowed to claim the scan is complete.
    earnings_health = next(
        (item for item in (report.get("earnings_intelligence") or [])
         if isinstance(item, dict)
         and (item.get("extras") or {}).get("earnings_type") == "scan_health"),
        None,
    )
    if earnings_health is not None:
        earnings_extras = earnings_health.get("extras") or {}
        unresolved = earnings_extras.get("unresolved_ir_failures") or []
        complete = bool(earnings_extras.get("scan_complete"))
        if not complete or unresolved:
            findings.append(_finding(
                "earnings_coverage_recovery", "fail", False,
                f"earnings recovery incomplete; unresolved companies: {unresolved}",
            ))
        else:
            findings.append(_finding(
                "earnings_coverage_recovery", "fail", True,
                "priority earnings sources verified or recovered through alternate sources",
            ))

    # RB-DEFECT-072: delivery_check is no longer read from report["delivery_check"]
    # (a snapshot embedded once at daily_brief.py's build time, never refreshed
    # by anything in the pipeline -- confirmed live 2026-09-23 stale by a full
    # day). check_delivery_readiness computes it fresh, live, at gate-check
    # time instead. See check_delivery_readiness's docstring for the full
    # incident. delivery_readiness lets tests and callers supply their own
    # instead of touching real on-disk/launchd state.
    findings.append(delivery_readiness if delivery_readiness is not None
                     else check_delivery_readiness(target_date))

    # 3. Material-change filtering — ecosystem_intelligence section present and
    # internally consistent with the shared is_material gate, not re-scanning
    # raw signals here (that's ecosystem_brief.py's job, already covered by
    # test_ecosystem_brief_materiality.py). This just asserts the section
    # made it into the report at all.
    eco = report.get("ecosystem_intelligence")
    if eco is None:
        findings.append(_finding("material_change_filtering", "fail", False,
                                  "ecosystem_intelligence section missing from report"))
    else:
        findings.append(_finding("material_change_filtering", "fail", True,
                                  "ecosystem_intelligence section present"))

    # 4. Correct opportunity/loop state
    loops = report.get("loops")
    if loops is None:
        findings.append(_finding("loop_state", "fail", False, "loops section missing from report"))
    else:
        overdue = loops.get("overdue") or []
        findings.append(_finding("loop_state", "fail", True,
                                  f"loops section present ({len(overdue)} overdue)"))
        if overdue:
            findings.append(_finding(
                "overdue_loops", "warn", True,
                f"{len(overdue)} overdue loop(s): {[l.get('id') for l in overdue]} "
                "-- accurately reported business state, not a generation defect; "
                "surfaced here so it can't be missed, not blocking delivery",
            ))
    active_threads = report.get("active_threads")
    if active_threads is None:
        findings.append(_finding("active_threads_state", "fail", False,
                                  "active_threads section missing from report"))
    else:
        findings.append(_finding("active_threads_state", "fail", True,
                                  f"{len(active_threads)} active thread(s) reported"))

    # 5. Relationship intelligence presence
    rel = report.get("relationship_signals")
    if rel is None:
        findings.append(_finding("relationship_intelligence", "fail", False,
                                  "relationship_signals section missing from report"))
    else:
        findings.append(_finding("relationship_intelligence", "fail", True,
                                  "relationship_signals section present"))

    # 6. Actionable CoS observations — assert it survived into the built
    # report at all (the sub-functions producing its content are already
    # unit-tested via test_daily_brief_cos_judgment.py; this is the
    # integration-level check that the section didn't get dropped).
    cos = report.get("cos_judgment") or {}
    cos_content_fields = ("hard_truths", "focus_leaks", "unsupported_assumptions",
                           "opportunity_costs", "prioritization_tradeoffs")
    if not cos or not any(cos.get(f) for f in cos_content_fields):
        findings.append(_finding(
            "cos_observations", "fail", False,
            "cos_judgment section missing or empty across all observation fields",
        ))
    else:
        findings.append(_finding("cos_observations", "fail", True,
                                  "cos_judgment carries at least one populated observation field"))

    # 7. Links/provenance — every recent_material_intelligence-shaped item
    # should carry a resolvable source reference. Report-level check only
    # (does the field exist and is it non-empty when signals exist), not a
    # full evidence-id resolution pass -- that's ecosystem_intelligence.py's
    # own store integrity, out of scope for a report-acceptance gate.
    strategic_events = report.get("strategic_events")
    if strategic_events is None:
        findings.append(_finding("provenance", "fail", False,
                                  "strategic_events section missing from report"))
    else:
        findings.append(_finding("provenance", "fail", True, "strategic_events section present"))

    # 8. "What changed since yesterday" — verified against the actual
    # rendered intelligence-brief markdown (not the internal render
    # function, which needs render-time state this gate doesn't have) so
    # this checks what was really delivered, not an intermediate value.
    intel_md = rendered_md.get("intelligence")
    if intel_md is None:
        findings.append(_finding("what_changed", "fail", None,
                                  "not_checked: no rendered intelligence brief markdown supplied"))
    elif "## What Changed Today" not in intel_md:
        findings.append(_finding("what_changed", "fail", False,
                                  "rendered intelligence brief has no 'What Changed Today' section"))
    else:
        section = intel_md.split("## What Changed Today", 1)[1].split("\n## ", 1)[0]
        if len(section.strip()) < 20:
            findings.append(_finding("what_changed", "fail", False,
                                      "'What Changed Today' section present but empty"))
        else:
            findings.append(_finding("what_changed", "fail", True,
                                      "'What Changed Today' section present and non-empty"))

    # 9-17. Content-quality checks against the real rendered markdown --
    # see the "Content-quality checks" block above for definitions. Skipped
    # (not_checked) rather than silently passed when their required input
    # wasn't supplied, same convention as check 8.

    # 9, 13-17 need the intelligence brief specifically (section structure).
    if intel_md is None:
        for name in (
            "no_cross_section_duplicate_headlines", "headlines_have_direct_article_links",
            "section_headline_counts", "prohibited_literal_headers_absent",
            "proof_dashboard_is_last_section", "newsletter_section_has_min_links",
        ):
            findings.append(_finding(name, "fail", None,
                                      "not_checked: no rendered intelligence brief markdown supplied"))
    else:
        findings.append(check_no_cross_section_duplicate_headlines(intel_md))
        findings.append(check_no_duplicate_story_clusters(intel_md))
        findings.append(check_headlines_have_direct_article_links(intel_md))
        findings.append(check_section_headline_counts(intel_md))
        findings.append(check_prohibited_literal_headers_absent(intel_md))
        findings.append(check_proof_dashboard_is_last_section(intel_md))
        findings.append(check_newsletter_section_has_min_links(intel_md))

    # 11, 12 run against whichever rendered docs are present (intelligence
    # and/or daily) -- a leaked DOM artifact or unfilled placeholder in
    # either document is equally a real defect.
    findings.append(check_no_dom_html_artifacts(rendered_md))
    findings.append(check_no_unfilled_template_placeholders(rendered_md))

    daily_md = rendered_md.get("daily")
    if daily_md is None:
        findings.append(_finding("daily_policy_alignment", "fail", None,
                                  "not_checked: no rendered daily brief markdown supplied"))
    else:
        findings.append(check_daily_policy_alignment(daily_md))

    # 10 only needs today's intelligence text (defaults to "" so the check
    # itself, not this call site, decides what counts as "no overlap").
    findings.append(check_no_cross_day_repeat_without_grace(intel_md or "", previous_intel_md))

    fail_findings = [f for f in findings if f["severity"] == "fail" and f["passed"] is False]
    return {
        "passed": len(fail_findings) == 0,
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "findings": findings,
        "fail_count": len(fail_findings),
        "warn_count": len([f for f in findings if f["severity"] == "warn"]),
    }


def _load_report(target_date: date) -> dict | None:
    cache_path = CACHE_DIR / "daily_brief.json"
    if not cache_path.exists():
        return None
    try:
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return cached.get("data")


def _load_rendered_md(target_date: date) -> dict[str, str]:
    out: dict[str, str] = {}
    intel_path = BRIEFS_DIR / f"{target_date.isoformat()}-intelligence-brief.md"
    daily_path = BRIEFS_DIR / f"{target_date.isoformat()}-daily-brief.md"
    if intel_path.exists():
        out["intelligence"] = intel_path.read_text(encoding="utf-8")
    if daily_path.exists():
        out["daily"] = daily_path.read_text(encoding="utf-8")
    return out


def _load_previous_intel_md(target_date: date) -> str | None:
    prior_path = BRIEFS_DIR / f"{(target_date - timedelta(days=1)).isoformat()}-intelligence-brief.md"
    if prior_path.exists():
        return prior_path.read_text(encoding="utf-8")
    return None


def run(target_date: date | None = None) -> dict:
    target_date = target_date or date.today()
    report = _load_report(target_date)
    if report is None:
        result = {
            "passed": False,
            "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "findings": [_finding("report_load", "fail", False,
                                   "system/.cache/daily_brief.json missing or unreadable")],
            "fail_count": 1,
            "warn_count": 0,
        }
    else:
        result = check_brief(
            report,
            rendered_md=_load_rendered_md(target_date),
            previous_intel_md=_load_previous_intel_md(target_date),
            target_date=target_date,
        )
    result["date"] = target_date.isoformat()
    return result


def _save_result(result: dict) -> None:
    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = RESULT_PATH.with_suffix(RESULT_PATH.suffix + ".tmp")
    tmp.write_text(json.dumps(result, indent=2, default=str) + "\n", encoding="utf-8")
    import os
    os.replace(tmp, RESULT_PATH)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=str, default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    target_date = date.fromisoformat(args.date) if args.date else date.today()
    result = run(target_date)
    _save_result(result)

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(f"{'PASS' if result['passed'] else 'FAIL'} — "
              f"{result['fail_count']} failing, {result['warn_count']} warnings")
        for f in result["findings"]:
            if f["severity"] == "fail" and not f["passed"]:
                print(f"  FAIL [{f['check']}]: {f['detail']}")
        for f in result["findings"]:
            if f["severity"] == "warn":
                print(f"  WARN [{f['check']}]: {f['detail']}")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
