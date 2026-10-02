"""
test_query_engine.py — tests for the Unified Query Engine (RB 9.42)

QE1: Intent parsing — entity + question → correct module scores
QE2: Module activation — threshold logic and signal synthesis trigger
QE3: Micro dispatch — graph lookup, query_type routing, template rendering
QE4: Macro dispatch — signal/artifact/entity filtering
QE5: Relationship dispatch — interaction filtering, WMN trigger
QE6: Integration — query() contract, answer synthesis, modules override
QE7: API endpoint — POST /query via TestClient
"""
from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import query_engine as qe

# ---------------------------------------------------------------------------
# QE1 — Intent parsing
# ---------------------------------------------------------------------------

class TestIntentParsing:
    def test_micro_operator_question(self):
        scores = qe.parse_intent("McDonald's", "how many operators are in the US?")
        assert scores["micro"] > 0.3

    def test_micro_store_count(self):
        scores = qe.parse_intent("McDonald's", "largest operators by store count")
        assert scores["micro"] > 0.2

    def test_macro_consumer_behavior(self):
        scores = qe.parse_intent(None, "what behavioral signals do we have on consumer hesitation?")
        assert scores["macro"] > 0.2

    def test_macro_brand_risk(self):
        scores = qe.parse_intent("Toast", "brand risk and market sentiment")
        assert scores["macro"] > 0.1

    def test_relationship_who_matters(self):
        scores = qe.parse_intent(None, "who matters most right now?")
        assert scores["relationship"] > 0.2

    def test_relationship_last_touch(self):
        scores = qe.parse_intent("Bob Gibson", "last touch and interaction history")
        assert scores["relationship"] > 0.2

    def test_intelligence_what_do_we_know(self):
        scores = qe.parse_intent("PAR Technology", "what do we know about them?")
        assert scores["intelligence"] > 0.1

    def test_intelligence_convergence(self):
        scores = qe.parse_intent(None, "what entities are converging across sources?")
        assert scores["intelligence"] > 0.1

    def test_entity_boost_mcdonalds(self):
        scores = qe.parse_intent("McDonald's", "something")
        assert scores["micro"] >= 0.6

    def test_entity_boost_nsn(self):
        scores = qe.parse_intent("nsn", "something")
        assert scores["micro"] >= 0.6

    def test_unknown_question_fallback(self):
        scores = qe.parse_intent(None, "xyzzy nonsense words qqq")
        # Fallback: intelligence gets at least 0.4
        assert scores["intelligence"] >= 0.4

    def test_scores_are_bounded(self):
        scores = qe.parse_intent("McDonald's", "how many operators stores franchisees coop state regions covered?")
        for v in scores.values():
            assert 0.0 <= v <= 1.0


# ---------------------------------------------------------------------------
# QE2 — Module activation
# ---------------------------------------------------------------------------

class TestModuleActivation:
    def test_active_modules_threshold(self):
        scores = {"micro": 0.8, "macro": 0.0, "relationship": 0.0, "intelligence": 0.05}
        active = qe._active_modules(scores, threshold=0.1)
        assert active == ["micro"]

    def test_active_modules_multiple(self):
        scores = {"micro": 0.5, "macro": 0.3, "relationship": 0.0, "intelligence": 0.2}
        active = qe._active_modules(scores, threshold=0.1)
        assert "micro" in active
        assert "macro" in active
        assert "intelligence" in active
        assert "relationship" not in active

    def test_active_modules_sorted_descending(self):
        scores = {"micro": 0.1, "macro": 0.5, "relationship": 0.3, "intelligence": 0.2}
        active = qe._active_modules(scores, threshold=0.0)
        assert active[0] == "macro"

    def test_signal_synthesis_trigger(self):
        result = qe.query(entity="PAR Technology", question="exit positioning and M&A signals",
                          modules=None)
        # signals module should be in modules_used when exit/M&A in question + entity present
        assert "signals" in result["modules_used"]

    def test_signal_synthesis_no_entity_skipped(self):
        result = qe.query(entity=None, question="exit positioning and M&A signals",
                          modules=None)
        # signals module requires entity
        assert "signals" not in result["modules_used"] or \
               result["results"].get("signals", {}).get("status") == "skipped"

    def test_earnings_trend_trigger(self):
        # RB-2026-09-05: earnings_trend activates on its own trigger word
        # ("trend"), independent of the signals trigger set.
        result = qe.query(entity="Starbucks Corp", question="what is the earnings trend",
                          modules=None)
        assert "earnings_trend" in result["modules_used"]
        assert "earnings_trend" in result["results"]

    def test_earnings_trend_no_entity_skipped(self):
        result = qe.query(entity=None, question="what is the earnings trend",
                          modules=None)
        assert "earnings_trend" not in result["modules_used"] or \
               result["results"].get("earnings_trend", {}).get("status") == "skipped"

    def test_earnings_trend_also_activates_on_signal_triggers(self):
        # Piggybacks on _SIGNAL_TRIGGERS too, since a growth/pattern question
        # about a company plausibly wants both reads.
        result = qe.query(entity="Starbucks Corp", question="growth pattern for this company",
                          modules=None)
        assert "earnings_trend" in result["modules_used"]


# ---------------------------------------------------------------------------
# QE3 — Micro dispatch
# ---------------------------------------------------------------------------

def _make_mock_registry(status="partial"):
    return {
        "graphs": [{
            "graph_id": "micro_ecosystem:mcdonalds_us_ops",
            "graph_slug": "mcdonalds_us_ops",
            "name": "McDonald's US Operations Micro Ecosystem",
            "status": status,
            "source_workbook": "NSN Lookup 2026-05 MAY.xlsx",
            "activation_terms": ["mcdonalds", "mcdonald's", "nsn"],
        }]
    }


def _make_mock_index():
    return {
        "counts": {
            "nodes": 19694,
            "edges": 127385,
            "operator_entities_with_stores": 1347,
            "stores_with_operator_entity": 14049,
            "coops_with_stores": 49,
            "states_with_stores": 54,
            "operator_tiers": {
                "enterprise_25_plus": 103,
                "mid_tier_5_to_24": 807,
                "single_digit_1_to_4": 437,
            },
        },
        "retrieval_answer_templates": {
            "operator_distribution": (
                "McDonald's U.S. micro graph contains {operator_entities_with_stores} operators "
                "and {stores_with_operator_entity} stores."
            )
        },
        "top_operator_entities": [{"name": "Flynn Group", "store_count": 500}],
        "state_distribution": {"TX": 1500, "CA": 1200},
        "coop_distribution": {"Southeast": 200},
    }


def _make_mock_index_with_precomputed():
    """RB-DEFECT-2026-07-27: mirrors a real graph build's precomputed_answers
    (micro_graph_builder.py / build_precomputed_answers), which is keyed by
    "coop_distribution" / "state_distribution" -- distinct from
    query_engine's own "coop_lookup" / "state_lookup" query_type labels."""
    idx = _make_mock_index()
    idx["precomputed_answers"] = {
        "operator_count": {"answer": "PRECOMPUTED operator count answer."},
        "largest_operators": {"answer": "PRECOMPUTED largest operators answer."},
        "coop_distribution": {"answer": "PRECOMPUTED co-op distribution answer."},
        "state_distribution": {"answer": "PRECOMPUTED state distribution answer."},
    }
    return idx


class TestMicroDispatch:
    def _run(self, entity, question, registry=None, index=None):
        """Run micro dispatch with injected file reads."""
        reg = registry or _make_mock_registry()
        idx = index or _make_mock_index()
        graphs_dir = Path("/fake/graphs")

        def fake_exists(self):
            return True

        def fake_read_text(self, **kwargs):
            if "index.json" in str(self) and "mcdonalds" in str(self):
                return json.dumps(idx)
            return json.dumps(reg)

        with patch.object(Path, "exists", fake_exists), \
             patch.object(Path, "read_text", fake_read_text):
            with patch("query_engine.core") as mock_core:
                mock_core.SYSTEM_DIR = Path("/fake")
                return qe._dispatch_micro(entity, question)

    def test_operator_count_query_type(self):
        result = self._run("McDonald's", "how many operators?")
        assert result["query_type"] == "operator_count"

    def test_largest_operators_query_type(self):
        result = self._run("McDonald's", "largest operators by store count")
        assert result["query_type"] == "largest_operators"

    def test_state_lookup_query_type(self):
        result = self._run("McDonald's", "how many states are represented?")
        assert result["query_type"] == "state_lookup"

    def test_coop_lookup_query_type(self):
        result = self._run("McDonald's", "coop distribution by region")
        assert result["query_type"] == "coop_lookup"

    def test_template_renders_with_counts(self):
        result = self._run("McDonald's", "how many operators?")
        assert "1347" in result.get("answer", "")
        assert "14049" in result.get("answer", "")

    def test_activation_term_match(self):
        result = self._run("nsn", "how many stores?")
        assert result.get("status") == "ok"

    def test_no_graph_for_unknown_entity(self):
        reg = {"graphs": [{"graph_id": "stub:x", "status": "stub", "name": "X"}]}
        result = self._run("UnknownCorp", "what is their structure?", registry=reg)
        assert result["status"] == "no_active_graph"

    def test_partial_status_accepted(self):
        result = self._run("McDonald's", "summary", registry=_make_mock_registry(status="partial"))
        assert result.get("status") == "ok"

    # -----------------------------------------------------------------------
    # RB-DEFECT-2026-07-27: precomputed_answers key-naming mismatch.
    #
    # build_precomputed_answers() (micro_graph_query.py, called at graph
    # build time by micro_graph_builder.py -- "Option C") stores its answers
    # under "coop_distribution" / "state_distribution", but this dispatcher's
    # own query_type detection produces "coop_lookup" / "state_lookup" for
    # the exact same questions. precomputed.get(query_type) silently missed
    # every time for these two types, so a real graph's precomputed co-op/
    # state breakdown was NEVER used -- confirmed live against the real
    # McDonald's US Ops graph, "which co-ops have the most stores?" returned
    # the generic operator-count template instead of the actual co-op
    # breakdown sitting in index.json. These tests exercise the previously-
    # untested precomputed_answers path directly (the existing tests above
    # only ever exercised the fixture's fallback-template path).
    # -----------------------------------------------------------------------

    def test_coop_lookup_uses_precomputed_answer_not_generic_template(self):
        result = self._run(
            "McDonald's", "coop distribution by region",
            index=_make_mock_index_with_precomputed(),
        )
        assert result["query_type"] == "coop_lookup"
        assert result["answer"] == "PRECOMPUTED co-op distribution answer."

    def test_state_lookup_uses_precomputed_answer_not_generic_template(self):
        result = self._run(
            "McDonald's", "how many states are represented?",
            index=_make_mock_index_with_precomputed(),
        )
        assert result["query_type"] == "state_lookup"
        assert result["answer"] == "PRECOMPUTED state distribution answer."

    def test_operator_count_uses_precomputed_answer_when_available(self):
        result = self._run(
            "McDonald's", "how many operators?",
            index=_make_mock_index_with_precomputed(),
        )
        assert result["answer"] == "PRECOMPUTED operator count answer."

    def test_largest_operators_uses_precomputed_answer_when_available(self):
        result = self._run(
            "McDonald's", "largest operators by store count",
            index=_make_mock_index_with_precomputed(),
        )
        assert result["answer"] == "PRECOMPUTED largest operators answer."

    def test_coop_lookup_falls_back_to_template_when_no_precomputed_answer(self):
        """No precomputed_answers at all (older/unbuilt graph) -- must still
        fall back gracefully to the live template path, not error out."""
        result = self._run("McDonald's", "coop distribution by region")
        assert result["query_type"] == "coop_lookup"
        assert "PRECOMPUTED" not in result["answer"]

    def test_result_has_required_keys(self):
        result = self._run("McDonald's", "how many operators?")
        for key in ("module", "status", "answer", "query_type", "sources"):
            assert key in result


# ---------------------------------------------------------------------------
# QE4 — Macro dispatch
# ---------------------------------------------------------------------------

class TestMacroDispatch:
    def _run(self, entity, question, signals=None, artifacts=None, entities=None):
        import macro_intelligence as mi
        with patch.object(mi, "query_behavioral_signals", return_value=signals or []), \
             patch.object(mi, "query_behavioral_artifacts", return_value=artifacts or []), \
             patch.object(mi, "query_entity_risks", return_value=entities or []):
            return qe._dispatch_macro(entity, question)

    def test_returns_ok_with_no_data(self):
        result = self._run(None, "consumer signals")
        assert result["status"] == "ok"
        assert result["signal_count"] == 0

    def test_counts_signals(self):
        signals = [{"signal_type": "consumer_hesitation"}, {"signal_type": "affordability_stress"}]
        result = self._run(None, "consumer signals", signals=signals)
        assert result["signal_count"] == 2

    def test_entity_filter_applied(self):
        signals = [
            {"signal_type": "consumer_hesitation", "entity": "McDonald's"},
            {"signal_type": "affordability_stress", "entity": "Toast"},
        ]
        result = self._run("McDonald's", "signals", signals=signals)
        assert result["signal_count"] == 1

    def test_answer_mentions_signal_type(self):
        signals = [{"signal_type": "consumer_hesitation"}]
        result = self._run(None, "signals", signals=signals)
        assert "consumer_hesitation" in result["answer"]

    def test_artifact_count_in_result(self):
        artifacts = [{"artifact_name": "Parking Lot Hesitation"}, {"artifact_name": "Value Migration"}]
        result = self._run(None, "artifacts", artifacts=artifacts)
        assert result["artifact_count"] == 2

    def test_result_has_required_keys(self):
        result = self._run(None, "signals")
        for key in ("module", "status", "signal_count", "artifact_count", "entity_count", "answer"):
            assert key in result


# ---------------------------------------------------------------------------
# QE4b — Signals dispatch alias resolution (RB-2026-09-16)
#
# entity_convergence_scan.py's daily scan already resolves a tracked
# competitor's aliases before scoring signals (2026-09-16 fix). This human-
# facing ad-hoc query path -- _dispatch_signals(), reached whenever someone
# asks "what's going on with X" -- had the identical gap: it passed the
# raw user-typed entity straight through with no alias expansion. Fixed by
# looking up competitor_intelligence_common.resolve_aliases(entity) and
# passing the result through to synthesize_entity_signals().
# ---------------------------------------------------------------------------

class TestSignalsDispatchAliasResolution:
    def test_passes_resolved_aliases_through_to_synthesize(self):
        import signal_synthesis as ss
        import competitor_intelligence_common as cic

        captured = {}

        def _capture(name, **kwargs):
            captured.update(kwargs)
            return {"dominant_pattern": "stable", "synthesis_hypothesis": "", "signal_count": 0}

        with patch.object(cic, "resolve_aliases", return_value=["NCR Corporation", "NCR Aloha"]), \
             patch.object(ss, "synthesize_entity_signals", side_effect=_capture):
            qe._dispatch_signals("NCR", "exit positioning?")

        assert captured.get("aliases") == ["NCR Corporation", "NCR Aloha"]

    def test_passes_none_when_entity_is_not_a_tracked_competitor(self):
        import signal_synthesis as ss
        import competitor_intelligence_common as cic

        captured = {}

        def _capture(name, **kwargs):
            captured.update(kwargs)
            return {"dominant_pattern": "stable", "synthesis_hypothesis": "", "signal_count": 0}

        with patch.object(cic, "resolve_aliases", return_value=[]), \
             patch.object(ss, "synthesize_entity_signals", side_effect=_capture):
            qe._dispatch_signals("McDonald's", "how are they doing?")

        # [] from resolve_aliases must normalize to None, not be passed
        # through as a falsy-but-truthy-shaped empty list.
        assert captured.get("aliases") is None

    def test_status_ok_and_answer_unaffected_by_alias_wiring(self):
        import signal_synthesis as ss
        import competitor_intelligence_common as cic

        fake_result = {
            "dominant_pattern": "distress",
            "synthesis_hypothesis": "test hypothesis",
            "signal_count": 3,
            "opportunity_or_risk": "test read",
        }
        with patch.object(cic, "resolve_aliases", return_value=["NCR Corporation"]), \
             patch.object(ss, "synthesize_entity_signals", return_value=fake_result):
            result = qe._dispatch_signals("NCR", "exit positioning?")

        assert result["status"] == "ok"
        assert result["dominant_pattern"] == "distress"
        assert "test hypothesis" in result["answer"]


# ---------------------------------------------------------------------------
# QE5 — Relationship dispatch
# ---------------------------------------------------------------------------

class TestRelationshipDispatch:
    def _run(self, entity, question, interactions=None, wmn=None):
        import relationship_intake as ri
        with patch.object(ri, "query_interactions", return_value=interactions or []), \
             patch.object(ri, "query_who_matters_now", return_value=wmn or []):
            return qe._dispatch_relationship(entity, question)

    def test_returns_ok_with_no_data(self):
        result = self._run(None, "who matters?")
        assert result["status"] == "ok"

    def test_wmn_triggered_by_question(self):
        wmn = [{"contact_id": "bob-gibson", "name": "Bob Gibson", "wmn_score": 10}]
        result = self._run(None, "who matters most right now?", wmn=wmn)
        assert len(result["who_matters_now"]) > 0
        assert "Bob Gibson" in result["answer"]

    def test_interaction_count_returned(self):
        interactions = [
            {"signal_type": "meeting_completed", "entity_name": "Toast"},
            {"signal_type": "email_received", "entity_name": "Toast"},
        ]
        result = self._run("Toast", "interaction history", interactions=interactions)
        assert result["interaction_count"] == 2

    def test_entity_filter_in_answer(self):
        interactions = [{"signal_type": "meeting_completed", "entity_name": "Toast"}]
        result = self._run("Toast", "history", interactions=interactions)
        assert "Toast" in result["answer"]

    def test_result_has_required_keys(self):
        result = self._run(None, "who matters?")
        for key in ("module", "status", "interaction_count", "who_matters_now", "answer"):
            assert key in result


# ---------------------------------------------------------------------------
# QE6 — query() contract and integration
# ---------------------------------------------------------------------------

class TestQueryContract:
    def test_contract_key_present(self):
        result = qe.query(entity="McDonald's", question="how many operators?")
        assert result["contract"] == "rb_query_v1"

    def test_required_keys_present(self):
        result = qe.query(entity="McDonald's", question="how many operators?")
        for key in ("contract", "entity", "question", "intent_scores", "modules_used",
                    "results", "answer", "sources", "generated_at"):
            assert key in result

    def test_entity_passed_through(self):
        result = qe.query(entity="PAR Technology", question="exit signals")
        assert result["entity"] == "PAR Technology"

    def test_question_passed_through(self):
        result = qe.query(entity=None, question="who matters now?")
        assert result["question"] == "who matters now?"

    def test_no_entity_question_works(self):
        result = qe.query(entity=None, question="what behavioral signals do we have?")
        assert result["contract"] == "rb_query_v1"
        assert isinstance(result["modules_used"], list)

    def test_modules_override_respected(self):
        result = qe.query(entity=None, question="anything", modules=["macro"])
        assert "macro" in result["modules_used"]
        assert "micro" not in result["modules_used"]

    def test_modules_override_multiple(self):
        result = qe.query(entity="McDonald's", question="anything", modules=["micro", "macro"])
        assert "micro" in result["modules_used"]
        assert "macro" in result["modules_used"]

    def test_answer_is_string(self):
        result = qe.query(entity="McDonald's", question="how many operators?")
        assert isinstance(result["answer"], str)
        assert len(result["answer"]) > 0

    def test_intent_scores_all_modules_present(self):
        result = qe.query(entity="McDonald's", question="how many operators?")
        for mod in ("micro", "macro", "relationship", "intelligence"):
            assert mod in result["intent_scores"]

    def test_results_dict_keyed_by_module(self):
        result = qe.query(entity="McDonald's", question="how many operators?")
        for mod in result["modules_used"]:
            assert mod in result["results"]

    def test_micro_answer_in_final_answer(self):
        result = qe.query(entity="McDonald's", question="how many operators?")
        if "micro" in result["modules_used"] and result["results"]["micro"].get("status") == "ok":
            assert "[MICRO]" in result["answer"]

    def test_generated_at_is_iso(self):
        result = qe.query(entity=None, question="test")
        from datetime import datetime
        # Should not raise
        datetime.fromisoformat(result["generated_at"])


# ---------------------------------------------------------------------------
# QE7 — API endpoint (TestClient)
# ---------------------------------------------------------------------------

def _load_server():
    """Load server module via importlib to avoid import conflicts."""
    spec = importlib.util.spec_from_file_location(
        "rb_api_server_for_qe_tests",
        Path(__file__).resolve().parent.parent / "api" / "server.py",
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["rb_api_server_for_qe_tests"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def api_client():
    try:
        from fastapi.testclient import TestClient
        server = _load_server()
        return TestClient(server.app)
    except Exception:
        pytest.skip("FastAPI not available")


class TestQueryEndpoint:
    def test_post_query_200(self, api_client):
        r = api_client.post(
            "/query",
            json={"entity": "McDonald's", "question": "how many operators?"},
            headers={"x-api-key": "test-key"},
        )
        assert r.status_code == 200

    def test_post_query_missing_question_422(self, api_client):
        r = api_client.post(
            "/query",
            json={"entity": "McDonald's"},
            headers={"x-api-key": "test-key"},
        )
        assert r.status_code == 422

    def test_post_query_returns_contract(self, api_client):
        r = api_client.post(
            "/query",
            json={"question": "who matters most right now?"},
            headers={"x-api-key": "test-key"},
        )
        assert r.status_code == 200
        assert r.json()["contract"] == "rb_query_v1"

    def test_post_query_answer_is_string(self, api_client):
        r = api_client.post(
            "/query",
            json={"entity": "PAR Technology", "question": "exit positioning?"},
            headers={"x-api-key": "test-key"},
        )
        assert r.status_code == 200
        assert isinstance(r.json()["answer"], str)

    def test_post_query_modules_used_is_list(self, api_client):
        r = api_client.post(
            "/query",
            json={"question": "what behavioral signals do we have?"},
            headers={"x-api-key": "test-key"},
        )
        assert r.status_code == 200
        assert isinstance(r.json()["modules_used"], list)

    def test_post_query_module_override(self, api_client):
        r = api_client.post(
            "/query",
            json={"question": "anything", "modules": "macro"},
            headers={"x-api-key": "test-key"},
        )
        assert r.status_code == 200
        assert "macro" in r.json()["modules_used"]

    def test_post_query_no_entity(self, api_client):
        r = api_client.post(
            "/query",
            json={"question": "consumer hesitation signals"},
            headers={"x-api-key": "test-key"},
        )
        assert r.status_code == 200
        assert r.json()["entity"] is None


# ---------------------------------------------------------------------------
# QE8 — Campaign dispatch (conference invitation/registration questions
# route to campaign_engine.py instead of dead-ending on "no access")
# ---------------------------------------------------------------------------

class TestCampaignDispatch:
    @pytest.fixture
    def campaign_env(self, tmp_path, monkeypatch):
        import campaign_engine as ce
        monkeypatch.setattr(ce, "CAMPAIGNS_DIR", tmp_path / "campaigns")
        campaign_id = "test-conference-2026"
        d = tmp_path / "campaigns" / campaign_id
        d.mkdir(parents=True)
        (d / "config.yaml").write_text("campaign_id: test-conference-2026\nname: Test Conference 2026\n", encoding="utf-8")
        roster = {
            "campaign_id": campaign_id,
            "prospects": [
                {"contact_id": "a1", "name": "Alice", "current_company": "Acme Corp",
                 "current_role": "VP Ops", "score": 40, "score_breakdown": [], "tier": "t1",
                 "tier_label": "Tier 1", "status": "not_yet_invited", "sources": [],
                 "is_restaurant_operator": False, "inclusion_reasons": [], "confidence": {"overall": "medium"}},
            ],
            "excluded": [], "validation_queue": [],
        }
        (d / "roster.json").write_text(json.dumps(roster), encoding="utf-8")
        (d / "company_rollup.json").write_text(json.dumps({"companies": [
            {"company": "Acme Corp", "contact_count": 1, "best_contact_name": "Alice",
             "best_contact_score": 40, "status_summary": {"not_yet_invited": 1}},
        ]}), encoding="utf-8")
        (d / "executive_dashboard.json").write_text(json.dumps({
            "eligible_enterprise_prospects": 1, "company_count": 1,
            "invited_not_registered": 0, "registered": 0,
            "worldpay_customer_conversion_opportunities": 0,
        }), encoding="utf-8")
        monkeypatch.setattr(ce, "REGISTRY_PATH", tmp_path / "campaigns" / "registry.yaml")
        (tmp_path / "campaigns" / "registry.yaml").write_text(
            "campaigns:\n  - id: test-conference-2026\n    name: Test Conference 2026\n", encoding="utf-8",
        )
        return campaign_id

    def test_campaign_signal_scores_high_for_conference_question(self):
        scores = qe.parse_intent("Genius", "who should I invite to the genius conference?")
        assert scores["campaign"] > 0.2

    def test_dispatch_campaign_default_status_summary(self, campaign_env):
        result = qe._dispatch_campaign(None, "what is the status of the campaign?")
        assert result["status"] == "ok"
        assert result["campaign_id"] == campaign_env
        assert "1" in result["answer"]  # eligible_enterprise_prospects
        # 2026-07-14: the fallback must never be a dead-end preview — the
        # full ranked list rides along even when no specific trigger matched.
        assert result["priority_invite_list"][0]["contact"] == "Alice"

    def test_dispatch_campaign_entity_company_lookup(self, campaign_env):
        result = qe._dispatch_campaign("Acme Corp", "tell me about this account")
        assert result["status"] == "ok"
        assert "Alice" in result["answer"]

    def test_query_routes_conference_question_to_campaign_module(self, campaign_env):
        result = qe.query(None, "who should I invite next to the conference, and what are the coverage gaps?")
        assert "campaign" in result["modules_used"]
        assert result["results"]["campaign"]["campaign_id"] == campaign_env

    def test_dispatch_campaign_priority_invite_list(self, campaign_env):
        result = qe._dispatch_campaign(None, "show me the priority invite list")
        assert result["status"] == "ok"
        assert result["campaign_id"] == campaign_env
        assert result["priority_invite_list"][0]["contact"] == "Alice"

    def test_dispatch_campaign_top_n_phrasing_returns_full_list(self, campaign_env):
        """2026-07-14: 'give me a Top 100' previously fell through to the
        dashboard-summary default (a 5-6 row preview only) because no
        trigger phrase matched — this is the exact gap a live GPT round-trip
        surfaced. 'top N' must route to the full priority_invite_list."""
        result = qe._dispatch_campaign(None, "give me a Top 100 ranked invite list")
        assert result["status"] == "ok"
        assert "priority_invite_list" in result
        assert result["priority_invite_list"][0]["contact"] == "Alice"
        assert "Alice" in result["answer"]
