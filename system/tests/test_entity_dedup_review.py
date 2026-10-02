"""
test_entity_dedup_review.py — RB-2026-09-27.

Coverage for entity_dedup_review.py, the review-first duplicate-entity
scan+merge tool built to close the real, confirmed Zaxby's (brand-zaxby-s
/ brand-zaxbys) and NCR (vendor-ncr / vendor-ncr-voyix) duplicate-entity
gaps -- see the module's own docstring for full context.

Isolated from real production data throughout (own tmp graph, own tmp
candidate store) -- same pattern as test_ownership_promotion.py /
test_multibrand_franchisee_operator_ingest.py.

RB-2026-09-27: ecosystem_intelligence.py's own _write_graph() validates
AFTER writing, with no in-process rollback (same architectural gap
documented in test_multibrand_franchisee_operator_ingest.py) -- its
validator subprocess used to always check the REAL production
system/ecosystem_intelligence.json by a hardcoded path regardless of this
suite's own monkeypatched core.ECOSYSTEM_INTELLIGENCE_PATH, which would
have provided zero real coverage here. That path-blindness was fixed in
ecosystem_intelligence.py the same day this module was built.
TestSchemaValidation below still runs the actual validator a second time,
directly, against this test's own isolated output -- belt-and-suspenders
coverage that doesn't depend on that upstream fix staying in place.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = ROOT / "system" / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ei = _load("ecosystem_intelligence")
sys.path.insert(0, str(SCRIPTS_DIR))
edr = _load("entity_dedup_review")


def _graph_with(entities=None, relationships=None, signals=None, assessments=None,
                user_relevance=None, strategic_recommendations=None, sources=None) -> dict:
    return {
        "version": 1, "contract": "rb_ecosystem_intelligence_v1", "last_updated": "2026-09-27",
        "domain_packs": ["restaurants"],
        "entities": entities or [], "relationships": relationships or [],
        "signals": signals or [], "sources": sources or [],
        "assessments": assessments or [], "user_relevance": user_relevance or [],
        "strategic_recommendations": strategic_recommendations or [],
    }


def _brand(entity_id, name, aliases=None, subtype="restaurant_brand", attributes=None, **extra):
    e = {
        "id": entity_id, "name": name, "entity_type": "brand", "subtype": subtype, "status": "active",
        "aliases": aliases or [], "attributes": attributes or {}, "sources": [],
        "confidence": {"level": "high"}, "domains": ["restaurants"],
    }
    e.update(extra)
    return e


def _vendor(entity_id, name, aliases=None, subtype=None, attributes=None, **extra):
    e = {
        "id": entity_id, "name": name, "entity_type": "vendor", "subtype": subtype, "status": "active",
        "aliases": aliases or [], "attributes": attributes or {}, "sources": [],
        "confidence": {"level": "medium"}, "domains": ["restaurants"],
    }
    e.update(extra)
    return e


def _rel(rel_id, from_id, to_id, relationship_type="uses_vendor_for_category", **extra):
    r = {
        "id": rel_id, "from_entity_id": from_id, "to_entity_id": to_id,
        "relationship_type": relationship_type, "status": "active", "sources": [],
        "confidence": {"level": "medium"},
    }
    r.update(extra)
    return r


class _IsolatedFixtureMixin(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        tmp = Path(self._tmpdir)
        self._graph_path = tmp / "ecosystem_intelligence.json"
        self._store_path = tmp / "entity_dedup_candidates.json"

        self._orig_graph_path = ei.core.ECOSYSTEM_INTELLIGENCE_PATH
        self._orig_store_path = edr.STORE_PATH
        ei.core.ECOSYSTEM_INTELLIGENCE_PATH = self._graph_path
        edr.STORE_PATH = self._store_path

    def tearDown(self):
        ei.core.ECOSYSTEM_INTELLIGENCE_PATH = self._orig_graph_path
        edr.STORE_PATH = self._orig_store_path
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _write_graph(self, graph: dict) -> None:
        self._graph_path.write_text(json.dumps(graph), encoding="utf-8")

    def _read_graph(self) -> dict:
        return json.loads(self._graph_path.read_text())

    def _entity(self, graph: dict, entity_id: str):
        return next((e for e in graph["entities"] if e["id"] == entity_id), None)


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

class TestFindCollisionCandidates(unittest.TestCase):
    def test_punctuation_insensitive_alias_to_name_collision(self):
        """The real, confirmed case: brand-zaxby-s (name "Zaxby's", alias
        "Zaxbys") and brand-zaxbys (name "Zaxbys") -- ecosystem_
        intelligence._norm_key() would normalize "Zaxby's" to "zaxby_s",
        which does NOT collide with "zaxbys"; this module's stricter
        alnum-only key must catch it via the alias."""
        graph = _graph_with(entities=[
            _brand("brand-zaxby-s", "Zaxby's", aliases=["Zaxbys"]),
            _brand("brand-zaxbys", "Zaxbys"),
        ])
        found = edr.find_collision_candidates(graph)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["candidate_id"], "brand-zaxby-s::brand-zaxbys")
        self.assertEqual(found[0]["entity_type"], "brand")

    def test_alias_to_alias_collision_ncr_case(self):
        """The real, older, never-cleaned-up case: vendor-ncr already
        lists "NCR Voyix" as an alias, and vendor-ncr-voyix's own name IS
        "NCR Voyix" -- an exact alias<->name collision, no fuzzy matching
        needed at all."""
        graph = _graph_with(entities=[
            _vendor("vendor-ncr", "NCR", aliases=["NCR Corporation", "NCR Voyix"], subtype="restaurant_technology_vendor"),
            _vendor("vendor-ncr-voyix", "NCR Voyix", subtype="restaurant_technology_vendor"),
        ])
        found = edr.find_collision_candidates(graph)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["candidate_id"], "vendor-ncr::vendor-ncr-voyix")

    def test_different_entity_type_never_flagged(self):
        graph = _graph_with(entities=[
            _brand("brand-flynn-group", "Flynn Group"),
            _vendor("vendor-flynn-group", "Flynn Group"),
        ])
        self.assertEqual(edr.find_collision_candidates(graph), [])

    def test_different_subtype_both_set_never_flagged(self):
        """An ordinary restaurant brand sharing a name with a multi-brand
        franchisee operator entity (also entity_type "brand", different
        subtype) is a deliberate, different real-world thing -- see
        multibrand_franchisee_operator_ingest.py's own name_collision_
        skipped path, which never even creates such an entity."""
        graph = _graph_with(entities=[
            _brand("brand-flynn-group", "Flynn Group", subtype="restaurant_brand"),
            _brand("operator-flynn-group", "Flynn Group", subtype="multi_brand_franchisee_operator"),
        ])
        self.assertEqual(edr.find_collision_candidates(graph), [])

    def test_one_side_missing_subtype_still_flagged(self):
        graph = _graph_with(entities=[
            _brand("brand-a", "Acme Co", subtype="restaurant_brand"),
            _brand("brand-b", "Acme Co", subtype=None),
        ])
        found = edr.find_collision_candidates(graph)
        self.assertEqual(len(found), 1)

    def test_genuinely_different_names_never_flagged(self):
        graph = _graph_with(entities=[
            _brand("brand-del-taco", "Del Taco"),
            _brand("brand-taco-del-mar", "Taco Del Mar"),
        ])
        self.assertEqual(edr.find_collision_candidates(graph), [])

    def test_same_entity_never_paired_with_itself(self):
        graph = _graph_with(entities=[_brand("brand-a", "Acme Co")])
        self.assertEqual(edr.find_collision_candidates(graph), [])


# ---------------------------------------------------------------------------
# scan()
# ---------------------------------------------------------------------------

class TestScan(_IsolatedFixtureMixin):
    def test_dry_run_reports_but_never_persists(self):
        self._write_graph(_graph_with(entities=[
            _brand("brand-zaxby-s", "Zaxby's", aliases=["Zaxbys"]),
            _brand("brand-zaxbys", "Zaxbys"),
        ]))
        result = edr.scan(dry_run=True)
        self.assertEqual(result["collision_pairs_found_this_scan"], 1)
        self.assertEqual(result["new_candidates"], 1)
        self.assertFalse(edr.STORE_PATH.exists())
        self.assertEqual(edr.pending_candidates(), [])

    def test_real_scan_persists_and_is_idempotent(self):
        self._write_graph(_graph_with(entities=[
            _brand("brand-zaxby-s", "Zaxby's", aliases=["Zaxbys"]),
            _brand("brand-zaxbys", "Zaxbys"),
        ]))
        result1 = edr.scan()
        self.assertEqual(result1["new_candidates"], 1)
        self.assertEqual(result1["total_pending"], 1)

        result2 = edr.scan()
        self.assertEqual(result2["collision_pairs_found_this_scan"], 1)
        self.assertEqual(result2["new_candidates"], 0)  # never re-propose an already-seen pair
        self.assertEqual(result2["total_pending"], 1)

    def test_rejected_candidate_not_re_proposed_on_rescan(self):
        self._write_graph(_graph_with(entities=[
            _brand("brand-zaxby-s", "Zaxby's", aliases=["Zaxbys"]),
            _brand("brand-zaxbys", "Zaxbys"),
        ]))
        edr.scan()
        cid = edr.pending_candidates()[0]["candidate_id"]
        edr.record_proposal(cid, confirmed=False)
        result = edr.scan()
        self.assertEqual(result["new_candidates"], 0)
        self.assertEqual(result["total_pending"], 0)


# ---------------------------------------------------------------------------
# pending_candidates()
# ---------------------------------------------------------------------------

class TestPendingCandidates(_IsolatedFixtureMixin):
    def test_evidence_includes_full_entities_and_relationships(self):
        self._write_graph(_graph_with(
            entities=[
                _brand("brand-zaxby-s", "Zaxby's", aliases=["Zaxbys"], attributes={"rank": 39}),
                _brand("brand-zaxbys", "Zaxbys", attributes={"rank": 39, "auv": 2685000}),
            ],
            relationships=[_rel("rel-zaxbys-ncr", "brand-zaxby-s", "vendor-ncr", category="pos")],
        ))
        edr.scan()
        pending = edr.pending_candidates()
        self.assertEqual(len(pending), 1)
        cand = pending[0]
        self.assertTrue(cand["still_valid"])
        ev_a = cand["evidence"]["brand-zaxby-s"]
        self.assertEqual(ev_a["entity"]["name"], "Zaxby's")
        self.assertEqual(ev_a["relationship_count"], 1)
        ev_b = cand["evidence"]["brand-zaxbys"]
        self.assertEqual(ev_b["entity"]["attributes"]["auv"], 2685000)

    def test_rejected_candidate_not_surfaced(self):
        self._write_graph(_graph_with(entities=[
            _brand("brand-zaxby-s", "Zaxby's", aliases=["Zaxbys"]),
            _brand("brand-zaxbys", "Zaxbys"),
        ]))
        edr.scan()
        cid = edr.pending_candidates()[0]["candidate_id"]
        edr.record_proposal(cid, confirmed=False)
        self.assertEqual(edr.pending_candidates(), [])


# ---------------------------------------------------------------------------
# record_proposal() reject
# ---------------------------------------------------------------------------

class TestReject(_IsolatedFixtureMixin):
    def test_reject_never_touches_graph(self):
        self._write_graph(_graph_with(entities=[
            _brand("brand-zaxby-s", "Zaxby's", aliases=["Zaxbys"]),
            _brand("brand-zaxbys", "Zaxbys"),
        ]))
        before = self._graph_path.read_text()
        edr.scan()
        cid = edr.pending_candidates()[0]["candidate_id"]
        result = edr.record_proposal(cid, confirmed=False)
        self.assertTrue(result["rejected"])
        self.assertEqual(self._graph_path.read_text(), before)

    def test_unknown_candidate_id_errors(self):
        result = edr.record_proposal("nope::nope", confirmed=False)
        self.assertIn("error", result)

    def test_already_resolved_candidate_errors_on_second_call(self):
        self._write_graph(_graph_with(entities=[
            _brand("brand-zaxby-s", "Zaxby's", aliases=["Zaxbys"]),
            _brand("brand-zaxbys", "Zaxbys"),
        ]))
        edr.scan()
        cid = edr.pending_candidates()[0]["candidate_id"]
        edr.record_proposal(cid, confirmed=False)
        result = edr.record_proposal(cid, confirmed=False)
        self.assertIn("error", result)


# ---------------------------------------------------------------------------
# record_proposal() confirm / merge_entities()
# ---------------------------------------------------------------------------

class TestConfirmMerge(_IsolatedFixtureMixin):
    def _seed_and_scan(self, graph: dict) -> str:
        self._write_graph(graph)
        edr.scan()
        return edr.pending_candidates()[0]["candidate_id"]

    def test_canonical_id_must_be_one_of_the_pair(self):
        cid = self._seed_and_scan(_graph_with(entities=[
            _brand("brand-zaxby-s", "Zaxby's", aliases=["Zaxbys"]),
            _brand("brand-zaxbys", "Zaxbys"),
        ]))
        result = edr.record_proposal(cid, confirmed=True, canonical_id="brand-not-in-pair")
        self.assertIn("error", result)

    def test_dry_run_confirm_previews_without_writing_or_resolving(self):
        graph = _graph_with(entities=[
            _brand("brand-zaxby-s", "Zaxby's", aliases=["Zaxbys"]),
            _brand("brand-zaxbys", "Zaxbys"),
        ])
        cid = self._seed_and_scan(graph)
        before = self._graph_path.read_text()
        result = edr.record_proposal(cid, confirmed=True, canonical_id="brand-zaxby-s", dry_run=True)
        self.assertTrue(result["dry_run"])
        self.assertFalse(result["confirmed"])
        self.assertEqual(self._graph_path.read_text(), before)
        # still pending -- a dry run must not resolve the candidate
        self.assertEqual(len(edr.pending_candidates()), 1)

    def test_merge_unions_aliases_domains_sources_and_adds_loser_name_as_alias(self):
        cid = self._seed_and_scan(_graph_with(entities=[
            _brand("brand-zaxby-s", "Zaxby's", aliases=["Zaxbys"], sources=["src-a"], domains=["restaurants"]),
            _brand("brand-zaxbys", "Zaxbys", sources=["src-b"], domains=["restaurants", "franchising"]),
        ]))
        edr.record_proposal(cid, confirmed=True, canonical_id="brand-zaxby-s")
        graph = self._read_graph()
        canonical = self._entity(graph, "brand-zaxby-s")
        self.assertIsNone(self._entity(graph, "brand-zaxbys"))
        self.assertIn("Zaxbys", canonical["aliases"])
        self.assertIn("src-a", canonical["sources"])
        self.assertIn("src-b", canonical["sources"])
        self.assertIn("franchising", canonical["domains"])

    def test_net_new_attribute_applies_without_conflict(self):
        cid = self._seed_and_scan(_graph_with(entities=[
            _brand("brand-zaxby-s", "Zaxby's", aliases=["Zaxbys"], attributes={"rank": 39}),
            _brand("brand-zaxbys", "Zaxbys", attributes={"rank": 39, "auv": 2685000}),
        ]))
        result = edr.record_proposal(cid, confirmed=True, canonical_id="brand-zaxby-s")
        self.assertEqual(result["merge_summary"]["attribute_conflicts"], 0)
        graph = self._read_graph()
        canonical = self._entity(graph, "brand-zaxby-s")
        self.assertEqual(canonical["attributes"]["auv"], 2685000)  # net-new copied over
        self.assertEqual(canonical["attributes"]["rank"], 39)      # identical value, no conflict

    def test_conflicting_populated_attribute_never_silently_overwritten(self):
        """The core discipline: a field populated DIFFERENTLY on both
        sides must never be silently overwritten -- the canonical value
        stays, and the loser's value is recorded for follow-up instead."""
        cid = self._seed_and_scan(_graph_with(entities=[
            _brand("brand-zaxby-s", "Zaxby's", aliases=["Zaxbys"], attributes={"unit_count": 941}),
            _brand("brand-zaxbys", "Zaxbys", attributes={"unit_count": 960}),
        ]))
        result = edr.record_proposal(cid, confirmed=True, canonical_id="brand-zaxby-s")
        self.assertEqual(result["merge_summary"]["attribute_conflicts"], 1)
        graph = self._read_graph()
        canonical = self._entity(graph, "brand-zaxby-s")
        self.assertEqual(canonical["attributes"]["unit_count"], 941)  # untouched
        conflicts = canonical["attributes"]["dedup_merge_conflicts"]
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["field"], "attributes.unit_count")
        self.assertEqual(conflicts[0]["canonical_value"], 941)
        self.assertEqual(conflicts[0]["duplicate_value"], 960)
        self.assertEqual(conflicts[0]["duplicate_entity_id"], "brand-zaxbys")

    def test_merge_history_recorded_on_canonical(self):
        cid = self._seed_and_scan(_graph_with(entities=[
            _brand("brand-zaxby-s", "Zaxby's", aliases=["Zaxbys"]),
            _brand("brand-zaxbys", "Zaxbys"),
        ]))
        edr.record_proposal(cid, confirmed=True, canonical_id="brand-zaxby-s")
        graph = self._read_graph()
        canonical = self._entity(graph, "brand-zaxby-s")
        history = canonical["attributes"]["dedup_merge_history"]
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["merged_entity_id"], "brand-zaxbys")

    def test_relationships_repointed_to_canonical(self):
        graph = _graph_with(
            entities=[
                _vendor("vendor-ncr", "NCR", aliases=["NCR Voyix"], subtype="restaurant_technology_vendor"),
                _vendor("vendor-ncr-voyix", "NCR Voyix", subtype="restaurant_technology_vendor"),
                _brand("brand-pizza-ranch", "Pizza Ranch"),
            ],
            relationships=[_rel("rel-pizza-ranch-ncr-voyix", "brand-pizza-ranch", "vendor-ncr-voyix", category="pos")],
        )
        cid = self._seed_and_scan(graph)
        edr.record_proposal(cid, confirmed=True, canonical_id="vendor-ncr")
        result_graph = self._read_graph()
        rel = next(r for r in result_graph["relationships"] if r["id"] == "rel-pizza-ranch-ncr-voyix")
        self.assertEqual(rel["to_entity_id"], "vendor-ncr")

    def test_self_loop_relationship_dropped_after_repoint(self):
        """A relationship that pointed at BOTH sides of the merge becomes
        a meaningless from==to edge after repointing -- it must be
        dropped, not kept."""
        graph = _graph_with(
            entities=[
                _brand("brand-zaxby-s", "Zaxby's", aliases=["Zaxbys"]),
                _brand("brand-zaxbys", "Zaxbys"),
            ],
            relationships=[_rel("rel-x", "brand-zaxby-s", "brand-zaxbys", relationship_type="operates")],
        )
        cid = self._seed_and_scan(graph)
        result = edr.record_proposal(cid, confirmed=True, canonical_id="brand-zaxby-s")
        self.assertEqual(result["merge_summary"]["self_loop_relationships_dropped"], 1)
        result_graph = self._read_graph()
        self.assertEqual(result_graph["relationships"], [])

    def test_other_entity_owner_entity_id_repointed(self):
        graph = _graph_with(entities=[
            _brand("brand-zaxby-s", "Zaxby's", aliases=["Zaxbys"]),
            _brand("brand-zaxbys", "Zaxbys"),
            _brand("operator-x", "Some Operator", subtype="multi_brand_franchisee_operator",
                   owner_name="Zaxbys", owner_entity_id="brand-zaxbys"),
        ])
        cid = self._seed_and_scan(graph)
        edr.record_proposal(cid, confirmed=True, canonical_id="brand-zaxby-s")
        result_graph = self._read_graph()
        operator = self._entity(result_graph, "operator-x")
        self.assertEqual(operator["owner_entity_id"], "brand-zaxby-s")

    def test_signals_entities_repointed_and_deduped(self):
        graph = _graph_with(
            entities=[
                _brand("brand-zaxby-s", "Zaxby's", aliases=["Zaxbys"]),
                _brand("brand-zaxbys", "Zaxbys"),
            ],
            signals=[{
                "id": "sig-2026-09-01-zaxbys-test", "event_at": "2026-09-01", "signal_type": "test_signal",
                "summary": "test", "entities": ["brand-zaxby-s", "brand-zaxbys"],
                "sources": [], "confidence": {"level": "medium"},
            }],
        )
        cid = self._seed_and_scan(graph)
        edr.record_proposal(cid, confirmed=True, canonical_id="brand-zaxby-s")
        result_graph = self._read_graph()
        sig = result_graph["signals"][0]
        self.assertEqual(sig["entities"], ["brand-zaxby-s"])  # deduped, not ["brand-zaxby-s","brand-zaxby-s"]

    def test_assessments_and_user_relevance_repointed(self):
        graph = _graph_with(
            entities=[
                _brand("brand-zaxby-s", "Zaxby's", aliases=["Zaxbys"]),
                _brand("brand-zaxbys", "Zaxbys"),
            ],
            assessments=[{
                "id": "asm-brand-zaxbys-01", "entity_id": "brand-zaxbys", "assessment_type": "test",
                "summary": "test", "confidence": {"level": "medium"}, "sources": [],
            }],
            user_relevance=[{
                "id": "ur-brand-zaxbys-01", "entity_id": "brand-zaxbys", "relevance_type": "watch",
                "score": 0.5, "relationship_coverage": {"status": "unknown"},
            }],
        )
        cid = self._seed_and_scan(graph)
        edr.record_proposal(cid, confirmed=True, canonical_id="brand-zaxby-s")
        result_graph = self._read_graph()
        self.assertEqual(result_graph["assessments"][0]["entity_id"], "brand-zaxby-s")
        self.assertEqual(result_graph["user_relevance"][0]["entity_id"], "brand-zaxby-s")

    def test_strategic_recommendations_entities_repointed_and_deduped(self):
        graph = _graph_with(
            entities=[
                _brand("brand-zaxby-s", "Zaxby's", aliases=["Zaxbys"]),
                _brand("brand-zaxbys", "Zaxbys"),
            ],
            strategic_recommendations=[{
                "id": "rec-zaxbys-consolidate", "recommendation_type": "test", "summary": "test", "priority": "monitor",
                "entities": ["brand-zaxbys"], "confidence": {"level": "medium"},
            }],
        )
        cid = self._seed_and_scan(graph)
        edr.record_proposal(cid, confirmed=True, canonical_id="brand-zaxby-s")
        result_graph = self._read_graph()
        self.assertEqual(result_graph["strategic_recommendations"][0]["entities"], ["brand-zaxby-s"])

    def test_loser_entity_deleted(self):
        cid = self._seed_and_scan(_graph_with(entities=[
            _brand("brand-zaxby-s", "Zaxby's", aliases=["Zaxbys"]),
            _brand("brand-zaxbys", "Zaxbys"),
        ]))
        edr.record_proposal(cid, confirmed=True, canonical_id="brand-zaxby-s")
        result_graph = self._read_graph()
        self.assertEqual(len(result_graph["entities"]), 1)
        self.assertIsNone(self._entity(result_graph, "brand-zaxbys"))

    def test_stale_candidate_errors_when_entity_already_gone(self):
        """Two candidates sharing an entity (a 3-way key collision): once
        one merge deletes the shared entity, the other candidate must
        fail cleanly, not crash or silently do nothing."""
        graph = _graph_with(entities=[
            _brand("brand-a", "Acme Co"),
            _brand("brand-b", "Acme Co"),
            _brand("brand-c", "Acme Co"),
        ])
        self._write_graph(graph)
        edr.scan()
        pending_ids = sorted(c["candidate_id"] for c in edr.pending_candidates())
        self.assertEqual(pending_ids, ["brand-a::brand-b", "brand-a::brand-c", "brand-b::brand-c"])
        # Merging a+b deletes brand-b. brand-a::brand-c still has both
        # entities present and must still succeed normally.
        edr.record_proposal("brand-a::brand-b", confirmed=True, canonical_id="brand-a")
        result = edr.record_proposal("brand-a::brand-c", confirmed=True, canonical_id="brand-a")
        self.assertTrue(result.get("confirmed"))
        # brand-b::brand-c references the now-deleted brand-b -- this one
        # must fail cleanly rather than crash or silently no-op.
        result2 = edr.record_proposal("brand-b::brand-c", confirmed=True, canonical_id="brand-b")
        self.assertIn("error", result2)


# ---------------------------------------------------------------------------
# Real schema validation against isolated output
# ---------------------------------------------------------------------------

class TestSchemaValidation(_IsolatedFixtureMixin):
    def test_merged_entity_passes_real_schema_validation(self):
        """ei._write_graph()'s own validator subprocess can't see this
        test's isolated graph path (it always checks the real production
        file -- see this module's docstring), so it provides no real
        coverage here. Run the actual validator directly against this
        test's own output instead."""
        graph = _graph_with(
            entities=[
                _vendor("vendor-ncr", "NCR", aliases=["NCR Corporation", "NCR Voyix"],
                        subtype="restaurant_technology_vendor", attributes={"primary_category": "pos"}),
                _vendor("vendor-ncr-voyix", "NCR Voyix", subtype="restaurant_technology_vendor",
                        attributes={"primary_category": "pos"}),
                _brand("brand-pizza-ranch", "Pizza Ranch"),
            ],
            relationships=[_rel("rel-pizza-ranch-ncr-voyix", "brand-pizza-ranch", "vendor-ncr-voyix", category="pos")],
        )
        self._write_graph(graph)
        edr.scan()
        cand = edr.pending_candidates()[0]
        edr.record_proposal(cand["candidate_id"], confirmed=True, canonical_id="vendor-ncr")

        schema_path = ROOT / "system" / "schemas" / "ecosystem_intelligence.schema.json"
        validator_path = ROOT / "system" / "schemas" / "validate.py"
        result = subprocess.run(
            [sys.executable, str(validator_path), str(self._graph_path), "--schema", str(schema_path)],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)

    def test_conflicting_attribute_merge_still_passes_real_schema_validation(self):
        graph = _graph_with(entities=[
            _brand("brand-zaxby-s", "Zaxby's", aliases=["Zaxbys"], attributes={"unit_count": 941}),
            _brand("brand-zaxbys", "Zaxbys", attributes={"unit_count": 960}),
        ])
        self._write_graph(graph)
        edr.scan()
        cand = edr.pending_candidates()[0]
        edr.record_proposal(cand["candidate_id"], confirmed=True, canonical_id="brand-zaxby-s")

        schema_path = ROOT / "system" / "schemas" / "ecosystem_intelligence.schema.json"
        validator_path = ROOT / "system" / "schemas" / "validate.py"
        result = subprocess.run(
            [sys.executable, str(validator_path), str(self._graph_path), "--schema", str(schema_path)],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
