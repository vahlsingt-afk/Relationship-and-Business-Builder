from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))
import render_intelligence_brief as renderer
import rb_core as core


def test_cycle_report_separates_cycles_and_renders_grounded_learnings(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "CACHE_DIR", tmp_path)
    sections = {
        "intelligence_collection_summary": [{"extras": {"intelligence_cycle_statistics": {
            "daily_monitoring": {
                "sources_scanned": 15, "items_fetched": 230,
                "intelligence_signals_classified": 7, "multi_source_convergences": 4,
                "mutation_proposals": 3, "records_changed": 2,
                "newsworthy_baseline_gaps": 1, "urgent_research_requests": 1,
                "trace_audit_status": "complete", "complete_intelligence_traces": 2,
                "incomplete_intelligence_traces": 0,
                "watchdog_status": "degraded", "watchdog_critical_alerts": 0,
                "watchdog_warnings": 1, "priority_source_blind_spots": ["market_signals"],
                "entities_checked_for_new_sources": 12, "source_candidates_observed": 7,
            },
            "routine_research": {
                "accounts_or_entities_researched": 2, "sources_checked": 18,
                "evidence_items_added": 12, "entities": ["Darden", "Toast"],
                "tech_stack_candidates_proposed": 4,
                "field_evidence_queued_for_review": 3,
            },
        }}}],
        "what_todd_doesnt_know_yet": [{
            "title": "Darden names a new CIO", "summary": "The appointment is effective October 1.",
            "source_refs": ["Darden press release"],
        }],
        "new_intelligence_today": [],
    }
    text = renderer._render_intelligence_cycle_report(sections)
    assert "RB evaluated 15 source channels" in text
    assert "Baseline construction:" in text
    assert "Darden, Toast" in text
    assert "**Health:** Degraded" in text
    assert "**Sources requiring recovery:** market_signals" in text
    assert "Darden names a new CIO" in text
    assert "Darden press release" in text
    assert "Score:" not in text


def test_cycle_report_explicitly_reports_no_new_learning():
    text = renderer._render_intelligence_cycle_report({})
    assert "No evidence-backed net-new learning was produced" in text


def test_normal_zero_convergence_is_silent():
    assert renderer._render_entity_convergence({
        "entities_scanned": 290,
        "new_findings": [],
        "persisting_findings": [],
    }) == ""


def test_food_toast_is_not_gp_competitive_intelligence():
    assert renderer._gp_score("Why IHOP brought back Stuffed French Toast Restaurant Dive") == 0


def test_identity_confirmation_uses_plain_language_not_api_instructions():
    text = renderer._render_identity_match_candidates({
        "identity_match_candidates": [{
            "title": "John McCarthy?",
            "summary": "Possible name match.",
            "extras": {
                "sender_name": "John McCarthy",
                "sender_email": "john@example.com",
                "baseline_name": "John McCarthy (TRAY)",
            },
        }],
    })
    assert 'Reply **"Yes, save john@example.com"**' in text
    assert "confirmProposal" not in text
    assert "chatgpt.com" not in text


def test_relationship_delta_includes_older_linkedin_activity_learned_today():
    text = renderer._render_relationship_deltas({
        "last_24h_relationship_signals": [{
            "title": "Claire Hayek",
            "summary": "Sent 1 inbound message; last contact 2026-09-13.",
            "freshness": "fresh",
            "extras": {
                "signal_timestamp": "2026-09-13T03:16:19+00:00",
                "signal_type": "linkedin_inbound_message",
            },
            "novelty": {"autonomous_discovery_value": "low"},
            "intelligence_lifecycle": {"first_seen": "2026-09-16"},
        }],
    }, renderer.date(2026, 9, 16))
    assert "Claire Hayek" in text
    assert "LEARNED TODAY" in text
    assert "No new relationship activity" not in text
