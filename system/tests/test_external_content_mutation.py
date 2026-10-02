from __future__ import annotations

import json
from pathlib import Path

from system.scripts import intelligence_mutation_engine as engine
from system.scripts import social_content_mutation


ALIGNMENT_TEXT = (
    "Human judgment must remain in the loop. AI should assist operators and "
    "not replace humans. Consistent execution and operational discipline matter."
)


def _patch_engine_paths(monkeypatch, tmp_path: Path) -> Path:
    system_dir = tmp_path / "system"
    cache_dir = system_dir / ".cache"
    system_dir.mkdir()
    cache_dir.mkdir()
    baseline_path = system_dir / "baseline_index.json"
    baseline_path.write_text(json.dumps([{
        "id": "bruce-nelson",
        "name": "Bruce Nelson",
        "current_company": "Tempo Hospitality Group",
        "current_role": "Founder & CFO",
        "signal_class": "LMI",
        "sources": ["test"],
        "tags": [],
        "notes": "",
    }]), encoding="utf-8")
    (system_dir / "ecosystem_intelligence.json").write_text(
        '{"entities":[],"relationships":[]}', encoding="utf-8"
    )
    (system_dir / "strategic_memory.json").write_text(
        '{"signals":[]}', encoding="utf-8"
    )
    monkeypatch.setattr(engine.core, "SYSTEM_DIR", system_dir)
    monkeypatch.setattr(engine.core, "BASELINE_PATH", baseline_path)
    monkeypatch.setattr(engine, "MUTATION_LOG_PATH", cache_dir / "knowledge_mutations.json")
    monkeypatch.setattr(engine, "EXEC_POV_PATH", cache_dir / "executive_povs.json")
    monkeypatch.setattr(
        engine,
        "ENGAGEMENT_OPPORTUNITIES_PATH",
        cache_dir / "engagement_opportunities.json",
    )
    return baseline_path


def test_known_author_alignment_updates_relationship_and_opportunity(
    monkeypatch, tmp_path
):
    baseline_path = _patch_engine_paths(monkeypatch, tmp_path)
    result = engine.run(
        ALIGNMENT_TEXT,
        source_title="LinkedIn post by Bruce Nelson",
        source_url="https://linkedin.com/posts/bruce-example",
        source_date="2026-06-07",
        source_author_name="Bruce Nelson",
        source_author_org="Tempo Hospitality Group",
        auto_apply=True,
    )

    types = {mutation["type"] for mutation in result["mutations"]}
    assert "thesis_alignment_detected" in types
    assert "engagement_opportunity" in types
    assert result["trust_stats"]["known_author_resolved"] is True
    assert result["trust_stats"]["relationship_mutations"] == 1
    assert result["trust_stats"]["opportunities_generated"] == 1

    contact = json.loads(baseline_path.read_text(encoding="utf-8"))[0]
    assert "thought-partner" in contact["tags"]
    assert contact["affinity_score"] > 0

    opportunities = json.loads(
        engine.ENGAGEMENT_OPPORTUNITIES_PATH.read_text(encoding="utf-8")
    )
    assert opportunities["opportunities"][0]["basis"] == "thesis_reinforcement"

    brief = engine.build_mutation_brief_block()
    assert brief["relationship_alignments"] == 1
    assert brief["engagement_opportunities"] == 1


def test_vendor_relationship_mutation_supersedes_weaker_prior_claim(monkeypatch, tmp_path):
    """RB Research Intelligence Engine Phase 1 (2026-07-20): a confirmed selection
    (status "active") arriving via article ingestion must auto-supersede a
    weaker prior claim (status "evaluating") for the same brand+category,
    rather than sitting alongside it unflagged. This is the Papa John's/PAR
    scenario from the architectural directive."""
    _patch_engine_paths(monkeypatch, tmp_path)
    ecosystem_path = engine.core.SYSTEM_DIR / "ecosystem_intelligence.json"
    ecosystem_path.write_text(json.dumps({
        "entities": [
            {"id": "brand-papa-john-s", "name": "Papa John's", "entity_type": "brand", "attributes": {}},
            {"id": "vendor-worldpay", "name": "Worldpay", "entity_type": "vendor", "attributes": {}},
        ],
        "relationships": [{
            "id": "rel-brand-papa-john-s-pos-evaluating-vendor-worldpay",
            "from_entity_id": "brand-papa-john-s",
            "to_entity_id": "vendor-worldpay",
            "category": "pos",
            "status": "evaluating",
            "updated_at": "2026-01-01T00:00:00Z",
        }],
    }), encoding="utf-8")

    text = "Papa John's has selected PAR Technology to power its point-of-sale systems nationwide."
    result = engine.run(
        text,
        source_title="PAR announces Papa John's selection",
        source_url="https://example.com/par-papa-johns",
        source_date="2026-01-12",
        auto_apply=True,
    )
    assert "vendor_customer_relationship" in {m["type"] for m in result["mutations"]}

    ecosystem = json.loads(ecosystem_path.read_text(encoding="utf-8"))
    rels_by_vendor = {r["to_entity_id"]: r for r in ecosystem["relationships"]}
    assert rels_by_vendor["vendor-worldpay"]["status"] == "superseded"
    assert rels_by_vendor["vendor-par-technology"]["status"] == "active"
    assert "requires_confirmation" not in rels_by_vendor["vendor-par-technology"]

    brief = engine.build_mutation_brief_block()
    assert brief["conflicts_detected"] == 1
    assert brief["conflicts_recorded_alongside"] == 0
    assert brief["conflicts"][0]["resolution"] == "auto_superseded"


def test_vendor_relationship_mutation_flags_conflict_with_confirmed_incumbent(monkeypatch, tmp_path):
    """A rival vendor claim arriving for a category that already has a
    confirmed (status "active") incumbent must not be silently written as an
    equally-valid active relationship, overwriting it. 2026-09-25
    (Confidence-Based Auto-Recording): no more blocking "conflicting"/
    requires_confirmation state -- the incoming claim IS recorded (never
    silently dropped), downgraded to "rumored" and cross-referenced to the
    incumbent it didn't beat. Its confidence is capped at 0.5 by this
    module's own text-extraction-confidence cap (see intelligence_mutation_
    engine.py -- it has no real source-type classification, so a
    confidently-WORDED but unverified article can never, by wording alone,
    outrank an already-established incumbent)."""
    _patch_engine_paths(monkeypatch, tmp_path)
    ecosystem_path = engine.core.SYSTEM_DIR / "ecosystem_intelligence.json"
    ecosystem_path.write_text(json.dumps({
        "entities": [
            {"id": "brand-papa-john-s", "name": "Papa John's", "entity_type": "brand", "attributes": {}},
            {"id": "vendor-ncr", "name": "NCR", "entity_type": "vendor", "attributes": {}},
        ],
        "relationships": [{
            "id": "rel-brand-papa-john-s-pos-active-vendor-ncr",
            "from_entity_id": "brand-papa-john-s",
            "to_entity_id": "vendor-ncr",
            "category": "pos",
            "status": "active",
            "updated_at": "2026-01-01T00:00:00Z",
        }],
    }), encoding="utf-8")

    text = "Papa John's has selected PAR Technology to power its point-of-sale systems nationwide."
    engine.run(
        text,
        source_title="Unverified trade blurb",
        source_url="https://example.com/rumor",
        source_date="2026-02-01",
        auto_apply=True,
    )

    ecosystem = json.loads(ecosystem_path.read_text(encoding="utf-8"))
    rels_by_vendor = {r["to_entity_id"]: r for r in ecosystem["relationships"]}
    assert rels_by_vendor["vendor-ncr"]["status"] == "active"
    assert rels_by_vendor["vendor-par-technology"]["status"] == "rumored"
    assert rels_by_vendor["vendor-par-technology"]["related_claim_id"] == "rel-brand-papa-john-s-pos-active-vendor-ncr"
    assert "requires_confirmation" not in rels_by_vendor["vendor-par-technology"]

    brief = engine.build_mutation_brief_block()
    assert brief["conflicts_detected"] == 1
    assert brief["conflicts_recorded_alongside"] == 1
    assert brief["conflicts"][0]["resolution"] == "recorded_alongside"
    assert "Recorded alongside" in brief["conflicts"][0]["note"]


def test_social_feed_processor_is_hash_deduplicated(monkeypatch, tmp_path):
    inbox = tmp_path / "inbox"
    cache = tmp_path / "cache"
    inbox.mkdir()
    cache.mkdir()
    feed_path = inbox / "social.feed.json"
    feed_path.write_text(json.dumps({
        "posts": [{
            "id": "post-1",
            "post_url": "https://linkedin.com/posts/bruce-example",
            "posted_at": "2026-06-07",
            "text": ALIGNMENT_TEXT,
            "author": {
                "name": "Bruce Nelson",
                "company": "Tempo Hospitality Group",
                "headline": "Founder & CFO",
            },
        }],
    }), encoding="utf-8")
    monkeypatch.setattr(social_content_mutation, "FEED_PATH", feed_path)
    monkeypatch.setattr(
        social_content_mutation, "MANIFEST_PATH", cache / "manifest.json"
    )
    calls = []
    monkeypatch.setattr(
        social_content_mutation.intelligence_mutation_engine,
        "run",
        lambda text, **kwargs: calls.append((text, kwargs)) or {
            "apply_result": {"applied": 2}
        },
    )

    first = social_content_mutation.process_new()
    second = social_content_mutation.process_new()
    assert first["processed_count"] == 1
    assert second["processed_count"] == 0
    assert second["skipped"][0]["reason"] == "already_processed"
    assert calls[0][1]["source_author_name"] == "Bruce Nelson"
