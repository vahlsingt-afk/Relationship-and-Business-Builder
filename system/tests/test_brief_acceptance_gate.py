"""
test_brief_acceptance_gate.py

RB-2026-08-23: morning_pipeline.py's only prior readiness check (RB-DEFECT-
017's _check_brief_readiness) computed a real status but ran AFTER render
and send steps had already executed, and the result only ever landed in an
advisory field nothing gated on -- a brief with stale/missing required
sources got built, rendered, and emailed exactly as if healthy. Each test
below is phrased as "this specific way silent degradation happened before
must now fail loudly."
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import brief_acceptance_check as bac


def _healthy_report(**overrides) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    report = {
        "source_health": {
            "sources": {
                name: {"status": "refreshed", "last_refreshed_at": now}
                for name in bac.REQUIRED_SOURCES
            },
        },
        "delivery_check": {"automated_pass_count": 11, "automated_warn_count": 0,
                            "automated_fail_count": 0, "reasons": {}},
        "ecosystem_intelligence": {"entities": [], "signals": []},
        "loops": {"overdue": [], "due_today": [], "this_week": [], "future": [], "closed": []},
        "active_threads": [{"id": "T-1"}],
        "relationship_signals": {"signals": []},
        "cos_judgment": {"hard_truths": ["something real"]},
        "strategic_events": {"events": []},
    }
    report.update(overrides)
    return report


def _healthy_rendered_md() -> dict[str, str]:
    return {"intelligence": "## What Changed Today\n\n- Real content here, not empty.\n\n## A: World Headlines\n"}


def _healthy_delivery_readiness() -> dict:
    """RB-DEFECT-072: delivery_check is now computed live by
    check_delivery_readiness (real on-disk publish state / launchd), not
    read from report["delivery_check"] -- pass a synthetic passing finding
    so gate tests stay deterministic and don't depend on this machine's
    real published/daily/latest_brief.json or launchagent state."""
    return bac._finding("delivery_check", "fail", True, "synthetic: healthy for test")


def test_healthy_report_passes():
    result = bac.check_brief(_healthy_report(), rendered_md=_healthy_rendered_md(),
                              delivery_readiness=_healthy_delivery_readiness())
    assert result["passed"] is True
    assert result["fail_count"] == 0


def test_stale_required_source_fails_the_gate():
    """The core RB-DEFECT-017 incident class: a required source is stale but
    everything downstream proceeds as if it weren't."""
    stale_report = _healthy_report()
    old_ts = "2020-01-01T00:00:00+00:00"
    stale_report["source_health"]["sources"]["email:personal"]["last_refreshed_at"] = old_ts
    result = bac.check_brief(stale_report, rendered_md=_healthy_rendered_md())
    assert result["passed"] is False
    freshness = next(f for f in result["findings"] if f["check"] == "freshness")
    assert freshness["passed"] is False
    assert "email:personal" in freshness["detail"]


def test_failed_required_source_fails_the_gate():
    failed_report = _healthy_report()
    failed_report["source_health"]["sources"]["calls"]["status"] = "error"
    result = bac.check_brief(failed_report, rendered_md=_healthy_rendered_md())
    assert result["passed"] is False


def test_missing_required_source_entirely_fails_the_gate():
    missing_report = _healthy_report()
    del missing_report["source_health"]["sources"]["messages"]
    result = bac.check_brief(missing_report, rendered_md=_healthy_rendered_md())
    assert result["passed"] is False


def test_delivery_check_automated_failures_fail_the_gate():
    """RB-DEFECT-072: delivery_check is now injected via delivery_readiness
    (computed live in production) rather than report["delivery_check"],
    which the gate no longer reads for its pass/fail decision at all."""
    bad_delivery = bac._finding(
        "delivery_check", "fail", False,
        "latest artifact dated 2026-09-22 (expected 2026-09-23)",
    )
    result = bac.check_brief(_healthy_report(), rendered_md=_healthy_rendered_md(),
                              delivery_readiness=bad_delivery)
    assert result["passed"] is False


def test_unresolved_priority_earnings_coverage_fails_the_gate():
    bad_report = _healthy_report(earnings_intelligence=[{
        "title": "[SCAN-HEALTH] Earnings monitor status",
        "extras": {
            "earnings_type": "scan_health",
            "scan_complete": True,
            "unresolved_ir_failures": ["PAR Technology"],
        },
    }])
    result = bac.check_brief(bad_report, rendered_md=_healthy_rendered_md())
    assert result["passed"] is False
    finding = next(f for f in result["findings"] if f["check"] == "earnings_coverage_recovery")
    assert "PAR Technology" in finding["detail"]


def test_missing_relationship_signals_section_fails_the_gate():
    bad_report = _healthy_report()
    del bad_report["relationship_signals"]
    result = bac.check_brief(bad_report, rendered_md=_healthy_rendered_md())
    assert result["passed"] is False


def test_empty_cos_judgment_fails_the_gate():
    bad_report = _healthy_report()
    bad_report["cos_judgment"] = {"hard_truths": [], "focus_leaks": [], "unsupported_assumptions": [],
                                   "opportunity_costs": [], "prioritization_tradeoffs": []}
    result = bac.check_brief(bad_report, rendered_md=_healthy_rendered_md())
    assert result["passed"] is False


def test_missing_what_changed_section_in_rendered_output_fails_the_gate():
    result = bac.check_brief(_healthy_report(), rendered_md={"intelligence": "## A: World Headlines\nstuff\n"})
    assert result["passed"] is False


def test_empty_what_changed_section_fails_the_gate():
    result = bac.check_brief(
        _healthy_report(),
        rendered_md={"intelligence": "## What Changed Today\n\n## A: World Headlines\nstuff\n"},
    )
    assert result["passed"] is False


def test_different_outlets_same_event_fails_story_cluster_gate():
    md = """## A: World Headlines

[Russia hits Ukrainian train near Poland](https://bbc.example/train)

[Russian drone hits train near Ukraine-Poland border](https://npr.example/drone)
"""
    result = bac.check_no_duplicate_story_clusters(md)
    assert result["passed"] is False


def test_distinct_events_for_same_company_do_not_false_positive():
    md = """## C: Restaurant Industry

[McDonald's names a new development officer](https://example.com/hire)

[McDonald's launches a new value menu](https://example.com/menu)
"""
    result = bac.check_no_duplicate_story_clusters(md)
    assert result["passed"] is True


def test_same_market_template_for_different_companies_is_not_a_duplicate():
    md = """## F: Watchlist

[McDonald's (MCD) — 🔻 near 52W low](https://example.com/mcd)

[Dutch Bros Inc. (BROS) — 🔻 near 52W low](https://example.com/bros)

[Kura Sushi USA, Inc. (KRUS) — 🔻 near 52W low](https://example.com/krus)
"""
    result = bac.check_no_duplicate_story_clusters(md)
    assert result["passed"] is True


def test_cross_day_exact_article_repeat_is_flagged_but_not_release_blocking():
    """RB-DEFECT (2026-09-15): this test originally asserted severity=="fail"
    (release-blocking), contradicting check_no_cross_day_repeat_without_grace's
    own docstring ("WARN-only since some repeats (multi-day corporate grace)
    are intentional") and check_brief()'s documented contract ("passed
    reflects only FAIL-severity findings"). render_intelligence_brief.py has
    a real, deliberate multi-day corporate-grace re-render mechanism
    (_is_duplicate/_story_ledger.should_render, up to CORPORATE_DEDUP_GRACE_DAYS)
    -- a URL legitimately re-rendering inside its grace window is expected,
    correct behavior, not a defect. This backstop check can't see the
    per-section grace period, so it must stay WARN (flag for review) rather
    than FAIL (block a healthy send over a normal grace re-render). The URL-
    normalization behavior this test exists to prove (same article, different
    tracking-param URL, still detected as a repeat) is unchanged and still
    covered below."""
    today = "## A: World Headlines\n[Story](https://example.com/a?today=1)"
    yesterday = "## A: World Headlines\n[Story](https://example.com/a?yesterday=1)"
    result = bac.check_no_cross_day_repeat_without_grace(today, yesterday)
    assert result["severity"] == "warn"
    assert result["passed"] is False


def test_daily_brief_blocks_stale_bridgepoint_growth_advice():
    result = bac.check_daily_policy_alignment(
        "## Capacity Plan\n- **BridgePoint business development**"
    )
    assert result["passed"] is False


def test_overdue_loops_warn_but_do_not_block_a_healthy_report():
    """RB-2026-08-22 finding: 9 real overdue loops existed in production and
    were only ever surfaced as one buried bullet. They're accurate business
    content, not a generation defect -- must be surfaced loudly (warn) but
    must not block an otherwise-healthy brief from being delivered."""
    report = _healthy_report()
    report["loops"]["overdue"] = [{"id": "L-2026-08-10-003"}, {"id": "L-2026-08-10-005"}]
    result = bac.check_brief(report, rendered_md=_healthy_rendered_md(),
                              delivery_readiness=_healthy_delivery_readiness())
    assert result["passed"] is True
    overdue_finding = next(f for f in result["findings"] if f["check"] == "overdue_loops")
    assert overdue_finding["severity"] == "warn"
    assert "L-2026-08-10-003" in overdue_finding["detail"]
    assert "L-2026-08-10-005" in overdue_finding["detail"]


def test_missing_report_load_fails_cleanly(tmp_path, monkeypatch):
    monkeypatch.setattr(bac, "CACHE_DIR", tmp_path / ".cache")
    monkeypatch.setattr(bac, "BRIEFS_DIR", tmp_path / "briefs")
    from datetime import date
    result = bac.run(date(2026, 8, 23))
    assert result["passed"] is False
    assert result["findings"][0]["check"] == "report_load"


def test_check_source_freshness_pure_function_matches_readiness_check_semantics():
    """Regression guard for the extraction: this must behave identically to
    the logic that used to be inline in morning_pipeline.py's
    _check_brief_readiness, since morning_pipeline.py now imports this
    instead of carrying its own copy."""
    now = datetime.now(timezone.utc).isoformat()
    healthy = {"sources": {"email:personal": {"status": "refreshed", "last_refreshed_at": now}}}
    result = bac.check_source_freshness(healthy, required_sources={"email:personal"})
    assert result == {"failed": [], "stale": []}

    stale = {"sources": {"email:personal": {"status": "refreshed",
                                             "last_refreshed_at": "2020-01-01T00:00:00+00:00"}}}
    result = bac.check_source_freshness(stale, required_sources={"email:personal"})
    assert result["stale"] == ["email:personal"]


# --- RB-DEFECT-072: structured findings + live delivery readiness ----------

def test_duplicate_story_gate_emits_structured_repair_plan():
    """The gate must not just report a duplicate exists -- it must hand the
    repair loop enough structure (repair_action, affected_item_ids,
    safe_to_auto_repair) to dispatch a fix without parsing `detail` prose."""
    md = """## C: Restaurant Industry

[Wingstop launches Game Day Punch Card, its second football promotion of the season](https://a.example/1)

## D+: Curated Trade Reads

### [Wingstop Unveils Game Day Punch Card for Rewards Members](https://a.example/2)
"""
    result = bac.check_no_duplicate_story_clusters(md)
    assert result["passed"] is False
    assert result["repair_action"] == "dedup_story_clusters"
    assert result["artifact_scope"] == "intelligence"
    assert result["failure_class"] == "content_repairable"
    assert result["safe_to_auto_repair"] is True
    assert result["retry_budget"] == 1
    assert len(result["affected_item_ids"]) == 1
    cluster = result["affected_item_ids"][0]
    assert set(cluster["urls"]) == {"https://a.example/1", "https://a.example/2"}


def test_acceptance_refreshes_delivery_health_after_publication():
    """RB-DEFECT-072 core fix: once the real on-disk artifact is dated
    today and the launch agent is loaded, check_delivery_readiness passes
    live -- it does not need anything about 'yesterday' to be true first."""
    with patch.object(bac._tdc, "_artifact_check",
                       return_value={"exists": True, "date_match": True,
                                     "artifact_date": "2026-09-23", "size_kb": 100.0}), \
         patch.object(bac._tdc, "_launchagent_status",
                       return_value={"loaded": True, "last_exit": "0", "detail": "loaded"}):
        result = bac.check_delivery_readiness(date(2026, 9, 23))
    assert result["passed"] is True
    assert "2026-09-23" in result["detail"]


def test_stale_embedded_delivery_snapshot_cannot_block_current_artifact():
    """The real 2026-09-23 incident, reproduced directly: report["delivery_check"]
    carries yesterday's stale snapshot (date mismatch, launch agent
    'not loaded'), but the REAL, current on-disk artifact is dated today and
    the agent really is loaded -- the gate must trust live state, not the
    embedded field, and must pass."""
    stale_report_field = {
        "automated_pass_count": 8, "automated_warn_count": 0, "automated_fail_count": 2,
        "reasons": {"latest_brief_artifact": "artifact date=2026-09-22 (expected 2026-09-23)",
                    "launchagent_loaded": "not loaded"},
    }
    report = _healthy_report(delivery_check=stale_report_field)
    with patch.object(bac._tdc, "_artifact_check",
                       return_value={"exists": True, "date_match": True,
                                     "artifact_date": "2026-09-23", "size_kb": 100.0}), \
         patch.object(bac._tdc, "_launchagent_status",
                       return_value={"loaded": True, "last_exit": "0", "detail": "loaded"}):
        result = bac.check_brief(report, rendered_md=_healthy_rendered_md(), target_date=date(2026, 9, 23))
    assert result["passed"] is True
    delivery_finding = next(f for f in result["findings"] if f["check"] == "delivery_check")
    assert delivery_finding["passed"] is True


def test_pre_send_gate_does_not_require_post_send_receipt():
    """check_delivery_readiness is a PRE-send prerequisite check -- it must
    never require evidence that only exists after an email actually went
    out (a send receipt, a delivery log entry). It passes purely on
    artifact/publish state, before either brief email has been sent."""
    import inspect
    source = inspect.getsource(bac.check_delivery_readiness)
    for forbidden in ("delivery_log", "send_receipt", "smtp_sent", "automated_pass_count"):
        assert forbidden not in source
    with patch.object(bac._tdc, "_artifact_check",
                       return_value={"exists": True, "date_match": True,
                                     "artifact_date": "2026-09-23", "size_kb": 50.0}), \
         patch.object(bac._tdc, "_launchagent_status",
                       return_value={"loaded": True, "last_exit": "0", "detail": "loaded"}):
        result = bac.check_delivery_readiness(date(2026, 9, 23))
    assert result["passed"] is True


# --- RB-2026-09-24: badge-prefixed headlines were invisible to every check -

def test_title_link_re_matches_a_badge_prefixed_bare_headline():
    """Real incident: `[[💰 FUNDING] Firebirds Raises Over $5 Million for
    Alex's Lemonade Stand Foundation](url)` -- the title's OWN nested
    `[FUNDING]` badge has a `]` that used to make the old `[^\\]]+` capture
    stop early, failing the whole line match. Confirmed live: this made
    no_duplicate_story_clusters, no_cross_section_duplicate_headlines,
    no_cross_day_repeat_without_grace, headlines_have_direct_article_links,
    and section_headline_counts all silently blind to every
    FUNDING/ACQUISITION/EXEC HIRE/BANKRUPTCY-badged headline."""
    line = ("[[💰 FUNDING] Firebirds Raises Over $5 Million for Alex's Lemonade "
            "Stand Foundation](https://www.fsrmagazine.com/industry-news/firebirds)")
    matches = bac._TITLE_LINK_RE.findall(line)
    assert len(matches) == 1
    title, url = matches[0]
    assert title == "[💰 FUNDING] Firebirds Raises Over $5 Million for Alex's Lemonade Stand Foundation"
    assert url == "https://www.fsrmagazine.com/industry-news/firebirds"


def test_title_link_re_still_excludes_the_read_more_line():
    """Regression guard: the fix (narrowing `[^\\]]+` to `.+`) must not
    start matching the bolded `**[Read more →](url)**` line every real
    item also carries -- that line intentionally starts with `**`, not `[`."""
    line = "**[Read more →](https://example.com/x)**"
    assert bac._TITLE_LINK_RE.findall(line) == []


def test_two_badge_prefixed_headlines_for_the_same_event_are_caught_as_duplicates():
    """Before the fix, two badge-prefixed items describing the same event
    would never be compared at all (both invisible to the regex) -- the
    gate would pass a real within-report duplicate silently."""
    md = (
        "## C: Restaurant Industry\n\n"
        "[[🏢 ACQUISITION] Acme Burger acquired by Big Food Group](https://a.example/1)\n\n"
        "## D+: Curated Trade Reads\n\n"
        "### [[🏢 ACQUISITION] Big Food Group acquires Acme Burger chain](https://a.example/2)\n"
    )
    result = bac.check_no_duplicate_story_clusters(md)
    assert result["passed"] is False


def test_cross_day_repeat_of_a_badge_prefixed_headline_is_now_detected():
    """The real 2026-09-23/09-24 Firebirds incident, reproduced: the exact
    same badge-prefixed URL in both days' rendered intelligence briefs must
    now be flagged, not silently pass as 'no URL overlap'."""
    today_md = "## C: Restaurant Industry\n\n[[💰 FUNDING] Firebirds Raises Over $5 Million](https://a.example/firebirds)\n"
    yesterday_md = "## C: Restaurant Industry\n\n[[💰 FUNDING] Firebirds Raises Over $5 Million](https://a.example/firebirds)\n"
    result = bac.check_no_cross_day_repeat_without_grace(today_md, yesterday_md)
    assert result["passed"] is False
    assert "1 URL" in result["detail"]
