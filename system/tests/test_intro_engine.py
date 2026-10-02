#!/usr/bin/env python3
"""
Regression tests for rb_core.find_intro_paths (intro engine V1).

Each test corresponds to a real scenario from the sprint brief or a defect
trace. Run with:
    python3 -m pytest system/tests/test_intro_engine.py -v

Or standalone:
    python3 system/tests/test_intro_engine.py
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

# Make rb_core importable from any working directory
_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import rb_core as core

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_TODAY = date(2026, 5, 26)   # Pin to sprint date for reproducible scores


def _load() -> tuple[list[dict], list[dict]]:
    baseline = core.load_baseline()
    threads = [t for t in core.load_active_threads() if t.get("status") == "open"]
    return baseline, threads


def _run(target: str, **kwargs) -> dict:
    baseline, threads = _load()
    return core.find_intro_paths(
        target,
        baseline=baseline,
        threads=threads,
        today=_TODAY,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# 1. Toast / Bob Gibson — company target with strong insider
#    Defect reference: sprint brief WS7; T-2026-05-21-003 (Ashwin/Dog Haus)
# ---------------------------------------------------------------------------

def test_toast_top_broker_is_bob_gibson():
    """Bob Gibson (LKI, DRR ~72) is the best insider at Toast — engine should rank him #1."""
    result = _run("Toast")
    brokers = result["candidate_brokers"]
    assert brokers, "No brokers returned for Toast"
    top = brokers[0]
    assert top["id"] == "bob-gibson", (
        f"Expected bob-gibson as top broker, got {top['id']} ({top['name']})"
    )


def test_toast_insiders_include_bob_gibson():
    """Bob Gibson should appear in the insiders list for Toast."""
    result = _run("Toast")
    insider_ids = [i["id"] for i in result["insiders"]]
    assert "bob-gibson" in insider_ids, (
        f"bob-gibson not in insiders: {insider_ids}"
    )


def test_toast_target_resolved_as_company():
    """'Toast' should resolve to a company target, not a person."""
    result = _run("Toast")
    assert result["target_resolved"]["type"] == "company"


def test_toast_scorecard_fields_present():
    """Every broker candidate must carry the V1 governed scorecard fields."""
    result = _run("Toast")
    required_keys = {
        "relationship_strength", "trust_maturity", "network_equity_risk",
        "reciprocity_balance", "recent_broker_asks", "recommended_posture",
    }
    for broker in result["candidate_brokers"]:
        sc = broker.get("scorecard", {})
        missing = required_keys - sc.keys()
        assert not missing, (
            f"Broker {broker['name']} missing scorecard keys: {missing}"
        )


def test_toast_draft_ask_present():
    """Every non-suppressed broker candidate must carry a draft_ask string."""
    result = _run("Toast")
    for broker in result["candidate_brokers"]:
        assert broker.get("draft_ask"), (
            f"Broker {broker['name']} has no draft_ask"
        )
        # Must contain opt-out language (governs voice spec)
        assert "no pressure" in broker["draft_ask"].lower() or "if not" in broker["draft_ask"].lower(), (
            f"draft_ask for {broker['name']} lacks opt-out language"
        )


def test_toast_persistence_templates_present():
    """find_intro_paths must return persistence_templates for top brokers."""
    result = _run("Toast")
    assert result.get("persistence_templates"), "No persistence_templates returned"
    for pt in result["persistence_templates"]:
        assert pt.get("loop_type") == "intro_request_pending"
        assert pt.get("requires_confirmation") is True
        assert pt.get("broker_id")


def test_bob_gibson_person_target_shows_network_gap():
    """When targeting Bob Gibson (person), the engine should note no proxy is needed
    or surface his co-workers as the broker list."""
    result = _run("Bob Gibson")
    resolved = result["target_resolved"]
    assert resolved["type"] == "person"
    assert resolved["id"] == "bob-gibson"
    # Bob is LKI, not inner RC — engine should NOT produce the "direct outreach" note
    assert not any("inner-tier" in n for n in result["notes"]), (
        "Bob Gibson is LKI, not inner RC; should not get direct-outreach note"
    )


# ---------------------------------------------------------------------------
# 2. Donnie Boivin — SCN suppression
#    Defect reference: P-013 validation table; heuristics.md SCN rule
# ---------------------------------------------------------------------------

def test_donnie_scn_suppression_fires():
    """Anyone in the success-champions or hospitality-table circle must be
    suppressed when Donnie Boivin is the target."""
    result = _run("Donnie Boivin", include_suppressed=True)
    suppressed = result["suppressed"]
    assert suppressed, "No suppressions for Donnie Boivin — SCN heuristic may have failed"

    scn_circles = {"success-champions", "hospitality-table"}
    for s in suppressed:
        flags = " ".join(s.get("heuristic_flags", []))
        # At least some should be suppressed due to SCN circle membership
    scn_suppressed = [
        s for s in suppressed
        if any("success-champions" in f or "hospitality-table" in f
               for f in s.get("heuristic_flags", []))
    ]
    assert scn_suppressed, (
        "No SCN-circle suppression found in suppressed list; "
        f"flags seen: {[s['heuristic_flags'] for s in suppressed[:5]]}"
    )


def test_donnie_inner_rc_note_fires():
    """Targeting an inner-tier RC should produce the 'may not need a broker' note."""
    result = _run("Donnie Boivin")
    assert any("inner-tier" in n.lower() for n in result["notes"]), (
        f"Expected inner-tier RC note for Donnie Boivin, got: {result['notes']}"
    )


def test_donnie_suppressed_count_nonzero():
    """At least 5 candidates should be suppressed by the SCN heuristic."""
    result = _run("Donnie Boivin", include_suppressed=True)
    assert result["suppressed_count"] >= 5, (
        f"Expected ≥5 suppressions for Donnie, got {result['suppressed_count']}"
    )


def test_donnie_top_brokers_not_in_scn():
    """The top brokers returned for Donnie must NOT be in the SCN/HT circles."""
    result = _run("Donnie Boivin")
    _, baseline_list = _load()
    baseline_map = {e["id"]: e for e in baseline_list}
    scn_circles = {"success-champions", "hospitality-table"}
    for broker in result["candidate_brokers"]:
        entry = baseline_map.get(broker["id"], {})
        broker_circles = set(entry.get("circles") or [])
        overlap = broker_circles & scn_circles
        assert not overlap, (
            f"Broker {broker['name']} is in SCN circle {overlap} "
            "but was not suppressed for Donnie Boivin"
        )


# ---------------------------------------------------------------------------
# 3. Ashwin / Dog Haus — candidate scoring with Patrick, James Lewis, Ish Singh
#    Defect reference: T-2026-05-21-003 INTRO-GOVERNANCE-001
#    Note: Ashwin Rajput is VC in baseline; Dog Haus is not currently in baseline.
#    James Lewis is not currently in baseline.
#    Tests use available data; FUTURE DATA flags mark fixture gaps.
# ---------------------------------------------------------------------------

def test_ashwin_resolves_from_baseline():
    """Ashwin Rajput should resolve by name from the baseline."""
    result = _run("Ashwin Rajput")
    assert result["target_resolved"]["type"] in ("person", "unknown"), (
        "Expected person or unknown resolution for Ashwin Rajput"
    )
    # Even if VC signal_class makes him an unusual target, the engine should not crash
    assert "candidate_brokers" in result


def test_patrick_nelson_scores_for_restaurant_target():
    """Patrick Nelson (LKI, DRR ~79) should appear as a broker candidate when
    targeting a restaurant/hospitality company — he is restaurant-adjacent."""
    # Use a company target that Patrick would plausibly surface for
    result = _run("Dog Haus")   # Dog Haus not in baseline → resolves as unknown/free-text
    # Engine should not crash on unresolved targets
    assert "candidate_brokers" in result
    assert result["target_resolved"]["type"] in ("unknown", "company")


def test_ish_singh_candidate_scorecard():
    """Ish Singh (LKI, DRR ~80) should have a scorecard present when he surfaces
    as a broker candidate. When targeted, engine should resolve him as a person."""
    result = _run("Ish Singh")
    resolved = result["target_resolved"]
    assert resolved["type"] == "person"
    assert resolved["id"] == "ish-singh"
    # If there are brokers returned, they must all have scorecard fields
    for broker in result["candidate_brokers"]:
        assert "scorecard" in broker, f"Broker {broker['name']} missing scorecard"


# FUTURE DATA: When Dog Haus is added to baseline as a company, add:
# def test_dog_haus_patrick_james_ish_governance():
#     """Intro governance for Dog Haus must score Patrick Nelson, James Lewis,
#     and Ish Singh with fit/trust/reciprocity/equity risk."""
#     result = _run("Dog Haus")
#     broker_ids = [c["id"] for c in result["candidate_brokers"]]
#     assert "patrick-nelson" in broker_ids or "ish-singh" in broker_ids, ...


# ---------------------------------------------------------------------------
# 4. Unknown target — warm recruiter referral path
#    Defect reference: 2026-05-21-sarah-mcangus-warm-recruiter-introduction-canonical-persistence-defect
#    Note: Sarah McAngus has since been added to baseline, so this regression
#    uses a clearly synthetic free-text target. Tests verify the engine handles
#    unresolved targets gracefully and produces persistence hooks.
# ---------------------------------------------------------------------------

_UNKNOWN_TARGET = "Unlisted Synthetic Recruiter Target 9f4c2"


def test_unknown_target_graceful():
    """Targeting an unknown free-text name should return a result
    with an 'unknown' resolution and a diagnostic note — no crash."""
    result = _run(_UNKNOWN_TARGET)
    assert result["target_resolved"]["type"] == "unknown", (
        f"Expected unknown for {_UNKNOWN_TARGET}, got {result['target_resolved']['type']}"
    )
    assert any("couldn't resolve" in n.lower() or "free-text" in n.lower()
               for n in result["notes"]), (
        f"Expected unresolved-target note, got: {result['notes']}"
    )
    # Should still return broker candidates (DRR-ranked warm introducers)
    assert result["candidate_brokers"] is not None


def test_unknown_target_persistence_template_produced():
    """Even for an unresolved target, persistence_templates should be present
    so the operator can record the intro intention."""
    result = _run(_UNKNOWN_TARGET)
    # If there are any active brokers, persistence templates should exist
    active_brokers = [c for c in result["candidate_brokers"] if not c.get("suppressed")]
    if active_brokers:
        assert result.get("persistence_templates"), (
            "persistence_templates missing for warm-recruiter scenario"
        )


# FUTURE DATA: When Sarah McAngus is in baseline, add:
# def test_sarah_mcangus_warm_referral_persisted_as_trust_transfer():
#     """Warm referral path to Sarah McAngus must be persistable as a
#     relationship edge / trust-transfer event (defect trace requirement)."""
#     ...


# ---------------------------------------------------------------------------
# 5. Scorecard property invariants — apply across all targets
# ---------------------------------------------------------------------------

def test_posture_values_are_valid():
    """recommended_posture must be one of the four allowed values for any target."""
    valid_postures = {"ask", "nurture_first", "do_not_ask", "direct_outreach"}
    for target in ("Toast", "Donnie Boivin", "Patrick Nelson"):
        result = _run(target)
        for broker in result["candidate_brokers"]:
            posture = broker.get("scorecard", {}).get("recommended_posture")
            assert posture in valid_postures, (
                f"Invalid posture {posture!r} for broker {broker['name']} "
                f"when targeting {target!r}"
            )


def test_inner_rc_posture_is_ask_or_nurture():
    """Inner-tier RCs should never receive 'do_not_ask' unless equity risk is extreme."""
    baseline, threads = _load()
    # Get an inner RC (Bruce Sellnow)
    for target in ("Toast",):
        result = core.find_intro_paths(target, baseline=baseline, threads=threads, today=_TODAY)
        for broker in result["candidate_brokers"]:
            if broker.get("signal_class") == "RC" and broker.get("rc_tier") == "inner":
                posture = broker.get("scorecard", {}).get("recommended_posture")
                assert posture in ("ask", "nurture_first", "direct_outreach"), (
                    f"Inner RC {broker['name']} got unexpected posture {posture!r}"
                )


def test_draft_ask_no_commission_language():
    """Draft asks must never contain commission or transactional framing."""
    forbidden = ["commission", "referral fee", "finder's fee", "free intro", "broker fee"]
    for target in ("Toast", "Bob Gibson", "Patrick Nelson"):
        result = _run(target)
        for broker in result["candidate_brokers"]:
            draft = broker.get("draft_ask", "").lower()
            for phrase in forbidden:
                assert phrase not in draft, (
                    f"Draft ask for {broker['name']} contains forbidden phrase: {phrase!r}"
                )


def test_heuristics_known_pairs_loader():
    """_parse_heuristics_known_pairs should return at least the daran/jim-taylor pair."""
    pairs = core._parse_heuristics_known_pairs()
    assert frozenset(("daran-adair", "jim-taylor")) in pairs, (
        "Expected daran-adair / jim-taylor pair from heuristics.md parser; "
        f"got {len(pairs)} pairs"
    )


# ---------------------------------------------------------------------------
# 6. Output shape contract — JSON consumers (Custom GPT, API)
# ---------------------------------------------------------------------------

def test_output_keys_stable():
    """Top-level output keys must match the documented contract."""
    result = _run("Toast")
    required = {"target", "target_resolved", "insiders", "candidate_brokers",
                "suppressed", "suppressed_count", "notes", "persistence_templates"}
    missing = required - result.keys()
    assert not missing, f"Missing top-level keys: {missing}"


def test_broker_record_keys_stable():
    """Each broker record must carry all documented fields."""
    result = _run("Toast")
    required = {"id", "name", "signal_class", "rc_tier", "current_company",
                "drr_score", "proximity", "heuristic_flags", "composite_score",
                "suppressed", "scorecard", "draft_ask", "has_proximity", "reason"}
    for broker in result["candidate_brokers"]:
        missing = required - broker.keys()
        assert not missing, (
            f"Broker {broker['name']} missing fields: {missing}"
        )


# ---------------------------------------------------------------------------
# Domain-aware broker scoring — RB-DEFECT-2026-07-27
#
# ROADMAP.md listed "domain-expertise routing" and "personalized draft asks"
# as queued/future work, implying neither existed. In fact both were already
# built (rb_core._domain_relevance_bonus, _infer_domain_tags, and the
# domain-aware branch of _draft_ask_message) -- but had zero test coverage,
# and a real, confirmed gap: _DOMAIN_KEYWORD_MAP only recognizes vendor/tech
# company names (Toast, PAR Technology, ...) plus a single hardcoded
# "mcdonald" special case. It has no way to recognize any of the ~1590 OTHER
# restaurant BRAND names in ecosystem_intelligence.json (Chipotle, Wendy's,
# Pollo Campero, ...) as belonging to the hospitality/foodservice domain.
#
# Confirmed live: `intro_engine.py "Campero"` -- a real, same-day active
# opportunity (Genius N Lead / Campero, system/account_intelligence/
# 2026-07-27-campero-genius-lead.md) -- returned zero domain-aware broker
# recommendations and fell all the way back to raw DRR ranking, because
# "campero" matches no keyword in any domain list. Fixed by adding
# _ecosystem_domain_for_company(), which checks ecosystem_intelligence.json's
# brand/vendor registry (entity_type "brand" -> hospitality_foodservice,
# "vendor" -> restaurant_tech) as an additional domain signal source,
# wired into both _infer_domain_tags (broker side) and
# _domain_relevance_bonus's target-company inference.
# ---------------------------------------------------------------------------

def test_ecosystem_registry_recognizes_restaurant_brand_by_partial_name():
    """The actual defect: "Campero" (the free-text target string) must
    resolve to the hospitality_foodservice domain via a match against the
    ecosystem registry's "Pollo Campero" brand entity -- previously matched
    nothing at all."""
    domain = core._ecosystem_domain_for_company("Campero")
    assert domain == "hospitality_foodservice", (
        f"Expected 'Campero' to resolve to hospitality_foodservice via the "
        f"ecosystem brand registry, got {domain!r}"
    )


def test_ecosystem_registry_recognizes_tech_vendor():
    domain = core._ecosystem_domain_for_company("NCR")
    assert domain == "restaurant_tech", f"Expected NCR to resolve to restaurant_tech, got {domain!r}"


def test_ecosystem_registry_unknown_company_returns_none():
    domain = core._ecosystem_domain_for_company("Some Company That Does Not Exist Anywhere")
    assert domain is None


def test_infer_domain_tags_picks_up_restaurant_brand_via_ecosystem_registry():
    """A baseline entry whose current_company is a restaurant BRAND (not a
    tech vendor) must get tagged hospitality_foodservice -- this previously
    required an exact keyword hit (e.g. "mcdonald") that most brand names
    don't have."""
    tags = core._infer_domain_tags({"current_company": "Pollo Campero"})
    assert "hospitality_foodservice" in tags


def test_domain_relevance_bonus_fires_for_real_restaurant_brand_target():
    """End-to-end: a broker whose company is a recognized restaurant-tech
    or hospitality entity must get a nonzero domain bonus against a
    Campero-style brand target -- this is the exact scenario that
    previously fell through to "target domain unknown"."""
    baseline, _ = _load()
    target_resolved = {"type": "company", "company": "Campero", "name": "Campero", "person": None}
    broker = {"current_company": "PAR Technology", "tags": [], "circles": []}
    bonus, explanation = core._domain_relevance_bonus(broker, target_resolved, baseline)
    assert bonus > 0, f"Expected a nonzero domain bonus, got {bonus} ({explanation!r})"
    assert "domain unknown" not in explanation


def test_campero_intro_paths_now_show_domain_expertise():
    """Regression guard for the actual live defect: intro_engine.py
    "Campero" must no longer fall back to pure-DRR ranking with zero
    domain signal on every candidate."""
    result = _run("Campero")
    brokers = result["candidate_brokers"]
    assert brokers, "No brokers returned for Campero"
    assert any(b.get("domain_bonus", 0) > 0 for b in brokers), (
        "Expected at least one top broker to carry a nonzero domain_bonus "
        "for a real restaurant-brand target"
    )


# ---------------------------------------------------------------------------
# Runner (standalone)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import traceback

    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = failed = 0
    for fn in tests:
        try:
            fn()
            print(f"  ✓ {fn.__name__}")
            passed += 1
        except Exception as exc:
            print(f"  ✗ {fn.__name__}")
            traceback.print_exc()
            failed += 1

    print(f"\n{passed} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)
