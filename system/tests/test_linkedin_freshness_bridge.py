#!/usr/bin/env python3
"""Regression tests for RB-LI-DAILY-SIGNAL-BRIDGE-001."""
from __future__ import annotations

import json
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import linkedin_freshness_bridge as bridge  # noqa: E402


BRIAN_DECK_POST = """
LinkedIn
Brian Deck
Chair & CEO at Smooth Commerce

The Pizza Hut / Yum Brands / Dragontail AI rollout lawsuit with Chaac Pizza
Northeast is exactly why restaurant AI has to work operationally, not just
technically. Franchisees do not care whether the demo is impressive if the
system cannot survive the Friday night rush, preserve trust, and fit real
store workflows.
"""


def _patch_paths(monkeypatch, tmp_path: Path) -> tuple[Path, Path]:
    daily = tmp_path / "inbox" / "linkedin.daily_signals.jsonl"
    market = tmp_path / "inbox" / "market_signals.json"
    queue = tmp_path / "inbox" / "ecosystem" / "vendor_verification_queue.json"
    graph = tmp_path / "system" / "ecosystem_intelligence.json"
    monkeypatch.setattr(bridge, "DAILY_SIGNALS_PATH", daily)
    monkeypatch.setattr(bridge, "MARKET_SIGNALS_PATH", market)
    monkeypatch.setattr(bridge, "VENDOR_VERIFICATION_QUEUE_PATH", queue)
    monkeypatch.setattr(bridge.core, "ECOSYSTEM_INTELLIGENCE_PATH", graph)

    def write_graph(payload):
        graph.parent.mkdir(parents=True, exist_ok=True)
        graph.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    monkeypatch.setattr(bridge.eco, "_write_graph", write_graph)
    monkeypatch.setattr(
        bridge.intelligence_mutation_engine,
        "run",
        lambda *args, **kwargs: {
            "trust_stats": {"total_mutations": 0},
            "apply_result": {"applied": 0},
        },
    )
    return daily, market


def test_linkedin_bridge_calls_mutation_engine_with_author(monkeypatch, tmp_path):
    _patch_paths(monkeypatch, tmp_path)
    captured = {}

    def fake_run(text, **kwargs):
        captured["text"] = text
        captured.update(kwargs)
        return {"apply_result": {"applied": 2}}

    monkeypatch.setattr(bridge.intelligence_mutation_engine, "run", fake_run)
    result = bridge.process({
        "input_type": "copied_post_text",
        "source_url": "https://www.linkedin.com/posts/bruce-nelson-example",
        "raw_text": (
            "Bruce Nelson says human judgment must remain in the loop. "
            "AI should assist operators, not replace humans."
        ),
        "author_name": "Bruce Nelson",
        "author_company": "Tempo Hospitality Group",
        "event_at": "2026-06-07",
        "confirm": True,
    })

    assert captured["source_author_name"] == "Bruce Nelson"
    assert captured["source_author_org"] == "Tempo Hospitality Group"
    assert captured["source_url"].endswith("bruce-nelson-example")
    assert result["intelligence_mutation_result"]["apply_result"]["applied"] == 2


def test_brian_deck_post_is_canonical_linkedin_signal(monkeypatch, tmp_path):
    daily, market = _patch_paths(monkeypatch, tmp_path)

    result = bridge.process({
        "input_type": "copied_post_text",
        "source_url": "https://www.linkedin.com/feed/update/urn:li:activity:123/",
        "raw_text": BRIAN_DECK_POST,
        "event_at": "2026-05-26",
        "captured_at": "2026-05-26T12:00:00+00:00",
        "confirm": True,
    })

    assert result["detected"] is True
    assert result["persistence_status"] == "persisted"
    assert result["canonical_output"]["headline"] == "LinkedIn signal detected."
    assert result["proof_stats"] == {
        "captured": 1,
        "recorded": 1,
        "updated": 0,
        "ignored": 0,
        "deduped": 0,
        "marked_stale": 0,
    }

    record = result["record"]
    people = {p["name"] for p in record["entities"]["people"]}
    companies = {c["name"] for c in record["entities"]["companies"]}
    assert "Brian Deck" in people
    assert "Smooth Commerce" in companies
    assert "Pizza Hut" in companies
    assert "Yum Brands" in companies
    assert "Dragontail" in companies
    assert "Chaac Pizza Northeast" in companies

    classifications = set(record["classification"]["signal_types"])
    assert "industry_intelligence" in classifications
    assert "content_opportunity" in classifications
    assert record["classification"]["strategic_relevance"] == "high"
    assert record["classification"]["relationship_relevance"] == "medium"
    assert record["thesis_alignment"]["aligned"] is True
    assert "operational_ai_realism" in record["thesis_alignment"]["matched_theses"]
    assert "friday_night_survivability" in record["thesis_alignment"]["matched_theses"]
    assert "Friday-night survivability" in record["recommended_action"]

    assert daily.exists()
    assert len(daily.read_text(encoding="utf-8").splitlines()) == 1
    market_payload = json.loads(market.read_text(encoding="utf-8"))
    assert any(
        item.get("bridge_signal_id") == result["bridge_signal_id"]
        for item in market_payload["items"]
    )


def test_duplicate_post_does_not_write_second_record(monkeypatch, tmp_path):
    daily, _market = _patch_paths(monkeypatch, tmp_path)
    req = {
        "input_type": "copied_post_text",
        "source_url": "https://www.linkedin.com/feed/update/urn:li:activity:123/?utm_source=x",
        "raw_text": BRIAN_DECK_POST,
        "event_at": "2026-05-26",
        "captured_at": "2026-05-26T12:00:00+00:00",
        "confirm": True,
    }

    first = bridge.process(req)
    second = bridge.process({**req, "captured_at": "2026-05-26T13:00:00+00:00"})

    assert first["persistence_status"] == "persisted"
    assert second["persistence_status"] == "persisted"
    assert second["proof_stats"]["deduped"] == 1
    assert second["canonical_output"]["rb_recorded"]["persistence_action"] == (
        "Already recorded; no duplicate written."
    )
    assert len(daily.read_text(encoding="utf-8").splitlines()) == 1


def test_preview_reports_pending_persistence_without_writing(monkeypatch, tmp_path):
    daily, market = _patch_paths(monkeypatch, tmp_path)

    result = bridge.process({
        "input_type": "linkedin_screenshot",
        "raw_text": BRIAN_DECK_POST,
        "event_at": "2026-05-26",
        "captured_at": "2026-05-26T12:00:00+00:00",
        "confirm": False,
    })

    assert result["detected"] is True
    assert result["persistence_status"] == "proposed_write_pending_confirmation"
    assert result["canonical_output"]["rb_recorded"]["persistence_action"] == (
        "Ready to record when confirm=true."
    )
    assert not daily.exists()
    assert not market.exists()


def test_reconcile_with_export_matches_daily_capture(monkeypatch, tmp_path):
    _daily, _market = _patch_paths(monkeypatch, tmp_path)
    bridge.process({
        "input_type": "copied_post_text",
        "source_url": "https://www.linkedin.com/feed/update/urn:li:activity:123/",
        "raw_text": BRIAN_DECK_POST,
        "event_at": "2026-05-26",
        "captured_at": "2026-05-26T12:00:00+00:00",
        "confirm": True,
    })

    reconciliation = bridge.reconcile_with_export([{
        "input_type": "linkedin_export",
        "source_url": "https://www.linkedin.com/feed/update/urn:li:activity:123/",
        "text": BRIAN_DECK_POST,
    }])

    assert reconciliation["daily_records"] == 1
    assert reconciliation["export_items"] == 1
    assert reconciliation["matched_without_duplicate"] == 1
    assert reconciliation["unmatched_daily_records"] == 0


QU_CUSTOMER_POST = """
LinkedIn
Qu

We are proud to support Blaze Pizza, Dave's Hot Chicken, GoTo Foods, and
Playa Bowls as Qu customers on modern restaurant POS technology.
"""


def test_qu_vendor_customer_post_mutates_ecosystem_graph(monkeypatch, tmp_path):
    _daily, _market = _patch_paths(monkeypatch, tmp_path)

    result = bridge.process({
        "input_type": "linkedin_screenshot",
        "source_url": "https://www.linkedin.com/feed/update/urn:li:activity:qu123/",
        "raw_text": QU_CUSTOMER_POST,
        "event_at": "2026-05-27",
        "captured_at": "2026-05-27T14:00:00+00:00",
        "confirm": True,
    })

    assert result["detected"] is True
    assert result["persistence_status"] == "persisted"
    assert result["ecosystem_graph_result"]["status"] == "persisted"
    assert result["ecosystem_graph_result"]["relationships_added"] == 4

    graph = json.loads(bridge.core.ECOSYSTEM_INTELLIGENCE_PATH.read_text(encoding="utf-8"))
    rels = {r["from_entity_id"]: r for r in graph["relationships"] if r["to_entity_id"] == "vendor-qu"}
    for brand_id in {
        "brand-blaze-pizza",
        "brand-dave-s-hot-chicken",
        "brand-goto-foods",
        "brand-playa-bowls",
    }:
        assert brand_id in rels
        assert rels[brand_id]["category"] == "pos"
        assert rels[brand_id]["evidence_posture"] == "provisional"
        assert rels[brand_id]["deployment_claim_type"] == "logo_or_customer_page"
        assert rels[brand_id]["confidence"]["level"] == "medium"

    assert any(s["signal_type"] == "vendor_claimed_customer_relationship" for s in graph["signals"])
    queue = json.loads(bridge.VENDOR_VERIFICATION_QUEUE_PATH.read_text(encoding="utf-8"))
    assert len(queue["items"]) == 4
    goto_task = next(i for i in queue["items"] if i["customer_brand"] == "GoTo Foods")
    assert goto_task["weak_signal_classification"] == "vendor_claimed"
    assert "module_scope_unverified" in goto_task["flags"]
    assert "holding_company_expansion_review" in goto_task


def test_qu_vendor_customer_signal_auto_mutates_graph_without_confirm(monkeypatch, tmp_path):
    _daily, _market = _patch_paths(monkeypatch, tmp_path)

    result = bridge.process({
        "input_type": "copied_post_text",
        "raw_text": QU_CUSTOMER_POST,
        "event_at": "2026-05-27",
        "captured_at": "2026-05-27T14:00:00+00:00",
        "confirm": False,
    })

    assert result["persistence_status"] == "persisted"
    mutation = result["canonical_output"]["rb_recorded"]["ecosystem_graph_mutation"]
    assert mutation["status"] == "persisted"
    assert mutation["relationships_added"] == 4
    assert bridge.core.ECOSYSTEM_INTELLIGENCE_PATH.exists()
    assert bridge.VENDOR_VERIFICATION_QUEUE_PATH.exists()


def test_qu_vendor_claim_conflicting_with_existing_incumbent_is_flagged(monkeypatch, tmp_path):
    """RB Research Intelligence Engine Phase 1 (2026-07-20): a LinkedIn
    vendor-claimed relationship must not silently coexist UNRECORDED with a
    rival vendor's existing active claim for the same brand+category — this
    bridge previously wrote relationships via the raw, conflict-unaware
    eco._upsert_relationship(). 2026-09-25 (Confidence-Based Auto-Recording):
    "flagged" no longer means blocked in a queue -- it means recorded with
    status "rumored" and cross-referenced to the incumbent it didn't beat,
    never silently dropped, never silently promoted over NCR either."""
    _daily, _market = _patch_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(bridge.core, "SYSTEM_DIR", tmp_path / "system")

    bridge.core.ECOSYSTEM_INTELLIGENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
    bridge.core.ECOSYSTEM_INTELLIGENCE_PATH.write_text(json.dumps({
        "sources": [],
        "entities": [
            {"id": "brand-blaze-pizza", "name": "Blaze Pizza"},
            {"id": "vendor-ncr", "name": "NCR"},
        ],
        "relationships": [{
            "id": "rel-brand-blaze-pizza-pos-active-vendor-ncr",
            "from_entity_id": "brand-blaze-pizza",
            "to_entity_id": "vendor-ncr",
            "category": "pos",
            "status": "active",
            "updated_at": "2026-01-01T00:00:00Z",
        }],
        "signals": [],
        "assessments": [],
        "user_relevance": [],
        "strategic_recommendations": [],
    }), encoding="utf-8")

    result = bridge.process({
        "input_type": "linkedin_screenshot",
        "source_url": "https://www.linkedin.com/feed/update/urn:li:activity:qu456/",
        "raw_text": QU_CUSTOMER_POST,
        "event_at": "2026-05-27",
        "captured_at": "2026-05-27T14:00:00+00:00",
        "confirm": True,
    })
    assert result["detected"] is True

    graph = json.loads(bridge.core.ECOSYSTEM_INTELLIGENCE_PATH.read_text(encoding="utf-8"))
    rels_by_key = {(r["from_entity_id"], r["to_entity_id"]): r for r in graph["relationships"]}

    blaze_ncr = rels_by_key[("brand-blaze-pizza", "vendor-ncr")]
    assert blaze_ncr["status"] == "active", "existing incumbent must be untouched, not silently overwritten"

    blaze_qu = rels_by_key[("brand-blaze-pizza", "vendor-qu")]
    assert blaze_qu["status"] == "rumored"
    assert blaze_qu["related_claim_id"] == "rel-brand-blaze-pizza-pos-active-vendor-ncr"
    assert "requires_confirmation" not in blaze_qu

    # Unrelated brands with no prior claim must be unaffected.
    goto_qu = rels_by_key[("brand-goto-foods", "vendor-qu")]
    assert goto_qu["status"] == "active"
