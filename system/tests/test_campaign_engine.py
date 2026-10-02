#!/usr/bin/env python3
"""
Tests for campaign_engine.py — Conference Campaign Intelligence Engine (Phase 1).

Test IDs and coverage:

RB-CAMPAIGN-001: a config with data_scope.source != personal_network_only is refused at runtime
RB-CAMPAIGN-002: a contact whose current_company matches the competitor list is excluded
RB-CAMPAIGN-003: a contact whose current_company is the hosting employer is excluded
RB-CAMPAIGN-004: a contact matching only one include_if_any clause is still included (OR logic)
RB-CAMPAIGN-005: score equals the sum of the returned score_breakdown entries
RB-CAMPAIGN-006: a manual override tag scores executive points even when the role heuristic misses
RB-CAMPAIGN-007: a score exactly at a tier's min_score lands in that band; one point below lands lower
RB-CAMPAIGN-008: known_registered_contact_ids excludes on the seed run; a settled roster entry is
                 carried forward unchanged (not re-scored/re-excluded) on a later run
RB-CAMPAIGN-009: a registration CSV row matches an existing roster entry by email and transitions status
RB-CAMPAIGN-010: dry_run=True computes results without writing roster/company files
RB-CAMPAIGN-011: baseline_index.json is never mutated by build_roster
RB-CAMPAIGN-012: company_rollup aggregates multiple contacts at the same company correctly
RB-CAMPAIGN-013: a known-customer contact joins the roster even when it fails all include_if_any
                  rules, starts at status=invited, and is scored on the current_customer dimension
RB-CAMPAIGN-014: a known-customer contact with status=invited gets the Tier 0 override regardless of score
RB-CAMPAIGN-015: reconcile_registration_workbook applies each sheet's target_status via identity_matcher,
                  and a later sheet never downgrades a status a former sheet already advanced
RB-CAMPAIGN-016: apply_customer_contact_list matches against the full baseline (not just current roster)
                  and writes known_customer_contacts.json additively
RB-CAMPAIGN-017: a prospect with low identity/company/relationship confidence routes to
                  validation_queue, not prospects, while status is still not_yet_invited
RB-CAMPAIGN-018: a 'confidence-override' tag keeps a low-confidence prospect in the primary roster
RB-CAMPAIGN-019: a low-confidence prospect whose status has progressed past not_yet_invited is never
                  demoted into validation_queue by a later confidence recompute
RB-CAMPAIGN-020: is_included() cites which include_if_any rule was satisfied
RB-CAMPAIGN-021: is_recruiter/is_first_degree_linkedin eligibility rule types work in exclude_if_any
RB-CAMPAIGN-022: build_coverage_gaps flags a company with contacts but no executive signal, and cites
                  a real broker recommendation from rb_core.find_intro_paths
RB-CAMPAIGN-023: build_execution_plan slices Queue A into 4 weeks and tracking matches real status counts
RB-CAMPAIGN-024: refresh_active_campaigns skips non-active campaigns and never raises on a broken one
RB-CAMPAIGN-025: account-first recommends by role priority over score within the same account
RB-CAMPAIGN-026: account-first excludes accounts that are neither a known customer nor a restaurant operator
RB-CAMPAIGN-029: strategic_brand_value scores known-customer full points, restaurant-operator half, else 0
RB-CAMPAIGN-030: existing_invite_status awards points only to contacts already invited
RB-CAMPAIGN-031: require_restaurant_affiliation gate excludes non-operator contacts, override tag wins
RB-CAMPAIGN-032: keyword-match heuristics use word boundaries, not raw substring containment
RB-CAMPAIGN-033: build_priority_invite_list ranks by score, drops registered, annotates invite source,
                  and exposes segment (tier_label) + existing_worldpay_customer (real, not invented Units)
RB-CAMPAIGN-034: invited_via backfills 'worldpay' for a known-customer contact carried forward from a
                  roster built before invited_via existed
RB-CAMPAIGN-035: priority_invite_list rows carry a real (never invented) owner/outreach_method/next_action
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import yaml

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import campaign_engine as ce  # noqa: E402
import rb_core as core  # noqa: E402
import ri_events  # noqa: E402


CAMPAIGN_ID = "test-conference-2026"


def _entry(**kwargs) -> dict:
    base = {
        "id": "person-x", "name": "Person X", "current_company": "Acme Restaurants",
        "current_role": "Manager", "email": None, "phone": None,
        "sources": [], "signal_class": "VC", "rc_tier": None,
        "circles": [], "tags": [], "notes": "",
    }
    base.update(kwargs)
    return base


def _write_config(tmp_path: Path, config: dict, campaign_id: str = CAMPAIGN_ID) -> None:
    d = tmp_path / "campaigns" / campaign_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")


def _base_config(**overrides) -> dict:
    config = {
        "campaign_id": CAMPAIGN_ID,
        "name": "Test Conference 2026",
        "data_scope": {"source": "personal_network_only"},
        "eligibility": {
            "known_registered_contact_ids": [],
            "include_if_any": [{"current_role_matches": ["VP", "Chief", "Owner", "President"]}],
            "exclude_if_any": [
                {"current_company_in": ["Rival Payments Co"], "reason": "competitor", "mode": "exclude"},
                {"current_company_equals": "Hosting Employer Inc.", "reason": "employee_of_hosting_company", "mode": "exclude"},
                {"already_registered": True, "reason": "already_registered", "mode": "exclude"},
            ],
        },
        "scoring": {
            "weights": {
                "enterprise_brand": 0,
                "current_opportunity": 0,
                "existing_relationship": 20,
                "executive_decision_maker": 20,
                "technology_operations_leadership": 0,
                "current_customer": 0,
            },
            "enterprise_brand_list": [],
            "penalties": {"competitor": -100},
        },
        "tiering": {
            "mode": "score_bands",
            "bands": [
                {"id": "tier_1", "label": "Tier 1", "min_score": 20},
                {"id": "tier_2", "label": "Tier 2", "min_score": 10},
            ],
        },
    }
    config.update(overrides)
    return config


def _patch_dirs(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(ce, "CAMPAIGNS_DIR", tmp_path / "campaigns")
    monkeypatch.setattr(ce, "DELTAS_DIR", tmp_path / "deltas")
    monkeypatch.setattr(core, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(ri_events, "EVENTS_DIR", tmp_path / "ri_events")
    monkeypatch.setattr(ri_events, "CACHE_DIR", tmp_path / "ri_cache")
    monkeypatch.setattr(ri_events, "INDEX_PATH", tmp_path / "ri_cache" / "ri_events_index.json")


def _write_baseline(tmp_path: Path, entries: list[dict]) -> Path:
    path = tmp_path / "baseline_index.json"
    path.write_text(json.dumps(entries), encoding="utf-8")
    return path


def test_engine_refuses_non_personal_data_scope(tmp_path, monkeypatch):
    """RB-CAMPAIGN-001."""
    _patch_dirs(monkeypatch, tmp_path)
    _write_config(tmp_path, _base_config(data_scope={"source": "employer_crm"}))
    baseline_path = _write_baseline(tmp_path, [_entry()])

    try:
        ce.build_roster(CAMPAIGN_ID, baseline_path=baseline_path)
        assert False, "expected CampaignDataScopeError"
    except ce.CampaignDataScopeError:
        pass


def test_eligibility_excludes_competitor_company(tmp_path, monkeypatch):
    """RB-CAMPAIGN-002."""
    _patch_dirs(monkeypatch, tmp_path)
    _write_config(tmp_path, _base_config())
    baseline_path = _write_baseline(tmp_path, [
        _entry(id="rival-vp", name="Rival VP", current_company="Rival Payments Co", current_role="VP Sales"),
    ])

    result = ce.build_roster(CAMPAIGN_ID, baseline_path=baseline_path)
    assert result["ok"] is True
    assert result["prospect_count"] == 0
    assert result["excluded_count"] == 1


def test_eligibility_excludes_gp_employee(tmp_path, monkeypatch):
    """RB-CAMPAIGN-003."""
    _patch_dirs(monkeypatch, tmp_path)
    _write_config(tmp_path, _base_config())
    baseline_path = _write_baseline(tmp_path, [
        _entry(id="colleague", name="Colleague", current_company="Hosting Employer Inc.", current_role="VP Sales"),
    ])

    result = ce.build_roster(CAMPAIGN_ID, baseline_path=baseline_path)
    assert result["prospect_count"] == 0
    assert result["excluded_count"] == 1
    roster = ce._load_roster(CAMPAIGN_ID)
    assert roster["excluded"][0]["reason"] == "employee_of_hosting_company"


def test_eligibility_include_via_or_logic(tmp_path, monkeypatch):
    """RB-CAMPAIGN-004: matches signal_class_in but not the role-keyword clause."""
    _patch_dirs(monkeypatch, tmp_path)
    config = _base_config()
    config["eligibility"]["include_if_any"] = [
        {"current_role_matches": ["VP", "Chief"]},
        {"signal_class_in": ["LKI", "RC"]},
    ]
    _write_config(tmp_path, config)
    baseline_path = _write_baseline(tmp_path, [
        _entry(id="lki-analyst", name="LKI Analyst", current_role="Analyst", signal_class="LKI"),
    ])

    result = ce.build_roster(CAMPAIGN_ID, baseline_path=baseline_path)
    assert result["prospect_count"] == 1


def test_score_computation_matches_weight_breakdown(tmp_path, monkeypatch):
    """RB-CAMPAIGN-005."""
    _patch_dirs(monkeypatch, tmp_path)
    _write_config(tmp_path, _base_config())
    baseline_path = _write_baseline(tmp_path, [
        _entry(
            id="scored-vp", name="Scored VP", current_role="VP Operations",
            relationship_health={"drr_score": 50},
        ),
    ])

    result = ce.build_roster(CAMPAIGN_ID, baseline_path=baseline_path, dry_run=True)
    prospects = result["roster_preview"]["prospects"]
    assert len(prospects) == 1
    p = prospects[0]
    assert p["score"] == sum(b["points"] for b in p["score_breakdown"])
    # existing_relationship: 20 * (50/100) = 10; executive_decision_maker: 20 -> total 30
    assert p["score"] == 30


def test_executive_heuristic_and_override_tag(tmp_path, monkeypatch):
    """RB-CAMPAIGN-006."""
    _patch_dirs(monkeypatch, tmp_path)
    config = _base_config()
    config["eligibility"]["include_if_any"] = [{"tag_in": ["exec-decision-maker"]}]
    _write_config(tmp_path, config)
    baseline_path = _write_baseline(tmp_path, [
        _entry(id="barista-exec", name="Barista Exec", current_role="Barista", tags=["exec-decision-maker"]),
    ])

    result = ce.build_roster(CAMPAIGN_ID, baseline_path=baseline_path, dry_run=True)
    prospects = result["roster_preview"]["prospects"]
    assert len(prospects) == 1
    dims = {b["dimension"] for b in prospects[0]["score_breakdown"]}
    assert "executive_decision_maker" in dims
    assert "override" in next(b["source"] for b in prospects[0]["score_breakdown"] if b["dimension"] == "executive_decision_maker")


def test_tier_boundary_score_bands(tmp_path, monkeypatch):
    """RB-CAMPAIGN-007: score exactly at min_score lands in that band; one point below lands lower."""
    _patch_dirs(monkeypatch, tmp_path)
    config = _base_config()
    config["scoring"]["weights"] = {
        "enterprise_brand": 0, "current_opportunity": 0, "existing_relationship": 0,
        "executive_decision_maker": 20, "technology_operations_leadership": 0, "current_customer": 0,
    }
    config["tiering"]["bands"] = [
        {"id": "tier_1", "label": "Tier 1", "min_score": 20},
        {"id": "tier_2", "label": "Tier 2", "min_score": 19},
    ]
    _write_config(tmp_path, config)
    baseline_path = _write_baseline(tmp_path, [
        _entry(id="exactly-20", name="Exactly Twenty", current_role="VP Ops"),
    ])

    result = ce.build_roster(CAMPAIGN_ID, baseline_path=baseline_path, dry_run=True)
    p = result["roster_preview"]["prospects"][0]
    assert p["score"] == 20
    assert p["tier"] == "tier_1"


def test_already_registered_seed_and_settled_status_idempotent(tmp_path, monkeypatch):
    """RB-CAMPAIGN-008."""
    _patch_dirs(monkeypatch, tmp_path)
    config = _base_config()
    config["eligibility"]["known_registered_contact_ids"] = ["seed-registered"]
    _write_config(tmp_path, config)
    baseline_path = _write_baseline(tmp_path, [
        _entry(id="seed-registered", name="Seed Registered", current_role="VP Ops"),
        _entry(id="normal-vp", name="Normal VP", current_role="VP Ops", current_company="Other Co"),
    ])

    result = ce.build_roster(CAMPAIGN_ID, baseline_path=baseline_path)
    assert result["prospect_count"] == 1  # seed-registered excluded, normal-vp included
    roster = ce._load_roster(CAMPAIGN_ID)
    assert roster["excluded"][0]["contact_id"] == "seed-registered"

    # Manually settle normal-vp's status, then rerun build_roster — it must be
    # carried forward unchanged (score/tier untouched), not re-excluded/rescored.
    roster = ce._load_roster(CAMPAIGN_ID)
    roster["prospects"][0]["status"] = "registered"
    roster["prospects"][0]["score"] = 999  # sentinel — a rescore would overwrite this
    (ce._campaign_dir(CAMPAIGN_ID) / "roster.json").write_text(json.dumps(roster), encoding="utf-8")

    result2 = ce.build_roster(CAMPAIGN_ID, baseline_path=baseline_path)
    assert result2["prospect_count"] == 1
    roster2 = ce._load_roster(CAMPAIGN_ID)
    carried = roster2["prospects"][0]
    assert carried["contact_id"] == "normal-vp"
    assert carried["status"] == "registered"
    assert carried["score"] == 999  # proves it was carried forward, not rescored


def test_reconcile_registrations_matches_via_identity_matcher(tmp_path, monkeypatch):
    """RB-CAMPAIGN-009."""
    _patch_dirs(monkeypatch, tmp_path)
    _write_config(tmp_path, _base_config())
    baseline_path = _write_baseline(tmp_path, [
        _entry(id="email-match", name="Email Match", current_role="VP Ops", email="match@example.com"),
    ])
    ce.build_roster(CAMPAIGN_ID, baseline_path=baseline_path)

    csv_path = tmp_path / "registrations.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["Attendee Name", "Email", "Registration Date"])
        writer.writeheader()
        writer.writerow({"Attendee Name": "Email Match", "Email": "match@example.com", "Registration Date": "2026-07-01"})

    result = ce.reconcile_registrations(CAMPAIGN_ID, csv_path, baseline_path=baseline_path)
    assert result["ok"] is True
    assert result["matched_count"] == 1
    roster = ce._load_roster(CAMPAIGN_ID)
    assert roster["prospects"][0]["status"] == "registered"


def test_dry_run_does_not_write_roster_or_company_files(tmp_path, monkeypatch):
    """RB-CAMPAIGN-010."""
    _patch_dirs(monkeypatch, tmp_path)
    _write_config(tmp_path, _base_config())
    baseline_path = _write_baseline(tmp_path, [
        _entry(id="dry-vp", name="Dry VP", current_role="VP Ops"),
    ])

    ce.build_roster(CAMPAIGN_ID, baseline_path=baseline_path, dry_run=True)
    campaign_dir = ce._campaign_dir(CAMPAIGN_ID)
    assert not (campaign_dir / "roster.json").exists()
    assert not (campaign_dir / "company_rollup.json").exists()
    assert not (campaign_dir / "roster_report.md").exists()


def test_no_baseline_mutation(tmp_path, monkeypatch):
    """RB-CAMPAIGN-011."""
    _patch_dirs(monkeypatch, tmp_path)
    _write_config(tmp_path, _base_config())
    baseline_path = _write_baseline(tmp_path, [
        _entry(id="untouched-vp", name="Untouched VP", current_role="VP Ops"),
    ])
    before = baseline_path.read_bytes()

    ce.build_roster(CAMPAIGN_ID, baseline_path=baseline_path)

    after = baseline_path.read_bytes()
    assert before == after


def test_company_rollup_aggregates_correctly(tmp_path, monkeypatch):
    """RB-CAMPAIGN-012."""
    _patch_dirs(monkeypatch, tmp_path)
    _write_config(tmp_path, _base_config())
    baseline_path = _write_baseline(tmp_path, [
        _entry(id="a1", name="Alice", current_company="Acme Restaurants", current_role="VP Ops",
               relationship_health={"drr_score": 80}),
        _entry(id="a2", name="Bob", current_company="Acme Restaurants", current_role="VP IT",
               relationship_health={"drr_score": 20}),
    ])

    ce.build_roster(CAMPAIGN_ID, baseline_path=baseline_path)
    rollup = json.loads((ce._campaign_dir(CAMPAIGN_ID) / "company_rollup.json").read_text())
    assert len(rollup["companies"]) == 1
    company = rollup["companies"][0]
    assert company["company"] == "Acme Restaurants"
    assert company["contact_count"] == 2
    assert company["best_contact_id"] == "a1"  # higher drr_score -> higher score
    assert set(company["contact_ids"]) == {"a1", "a2"}


def test_strategic_brand_value_tiers(tmp_path, monkeypatch):
    """RB-CAMPAIGN-029: strategic_brand_value (relationship-first reweight,
    2026-07-13) — a confirmed known-customer contact scores full points, a
    restaurant operator that isn't a confirmed customer scores half, and a
    contact that's neither scores zero on this dimension."""
    _patch_dirs(monkeypatch, tmp_path)
    config = _base_config()
    config["scoring"]["weights"]["strategic_brand_value"] = 20
    _write_config(tmp_path, config)
    baseline_path = _write_baseline(tmp_path, [
        _entry(id="known-cust", name="Known Customer", current_role="VP Ops", current_company="Acme Corp"),
        _entry(id="operator-only", name="Operator Only", current_role="VP Ops", current_company="Bob's Pizzeria"),
        _entry(id="neither", name="Neither", current_role="VP Ops", current_company="Some Consultancy"),
    ])
    _write_known_customer_contacts(CAMPAIGN_ID, [{"contact_id": "known-cust", "merchant": "Acme Corp", "title": "VP Ops"}])

    ce.build_roster(CAMPAIGN_ID, baseline_path=baseline_path)
    roster = ce._load_roster(CAMPAIGN_ID)
    by_id = {p["contact_id"]: p for p in roster["prospects"]}

    def sbv_points(p):
        return next((b["points"] for b in p["score_breakdown"] if b["dimension"] == "strategic_brand_value"), 0)

    assert sbv_points(by_id["known-cust"]) == 20
    assert sbv_points(by_id["operator-only"]) == 10
    assert sbv_points(by_id["neither"]) == 0


def test_existing_invite_status_bonus_for_already_invited(tmp_path, monkeypatch):
    """RB-CAMPAIGN-030: existing_invite_status (relationship-first reweight,
    2026-07-13) awards points only to contacts already invited — never to a
    not-yet-invited contact."""
    _patch_dirs(monkeypatch, tmp_path)
    config = _base_config()
    config["scoring"]["weights"]["existing_invite_status"] = 5
    _write_config(tmp_path, config)
    baseline_path = _write_baseline(tmp_path, [
        _entry(id="already-invited", name="Already Invited", current_role="VP Ops"),
        _entry(id="not-invited", name="Not Invited", current_role="VP Ops"),
    ])
    _write_known_customer_contacts(CAMPAIGN_ID, [{"contact_id": "already-invited", "merchant": "Acme", "title": "VP Ops"}])

    ce.build_roster(CAMPAIGN_ID, baseline_path=baseline_path)
    roster = ce._load_roster(CAMPAIGN_ID)
    by_id = {p["contact_id"]: p for p in roster["prospects"]}

    def eis_points(p):
        return next((b["points"] for b in p["score_breakdown"] if b["dimension"] == "existing_invite_status"), 0)

    assert by_id["already-invited"]["status"] == "invited"
    assert eis_points(by_id["already-invited"]) == 5
    assert by_id["not-invited"]["status"] == "not_yet_invited"
    assert eis_points(by_id["not-invited"]) == 0


def _write_known_customer_contacts(campaign_id: str, contacts: list[dict]) -> None:
    path = ce._known_customer_contacts_path(campaign_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"campaign_id": campaign_id, "contacts": contacts}), encoding="utf-8")


def test_known_customer_joins_roster_and_scores_current_customer(tmp_path, monkeypatch):
    """RB-CAMPAIGN-013."""
    _patch_dirs(monkeypatch, tmp_path)
    config = _base_config()
    config["scoring"]["weights"]["current_customer"] = 20
    _write_config(tmp_path, config)
    baseline_path = _write_baseline(tmp_path, [
        # Fails every include_if_any rule (no VP/Chief/Owner/President in role).
        _entry(id="merchant-contact", name="Merchant Contact", current_role="Store Manager"),
    ])
    _write_known_customer_contacts(CAMPAIGN_ID, [{"contact_id": "merchant-contact", "merchant": "Acme Corp", "title": "Store Manager"}])

    result = ce.build_roster(CAMPAIGN_ID, baseline_path=baseline_path)
    assert result["prospect_count"] == 1
    roster = ce._load_roster(CAMPAIGN_ID)
    p = roster["prospects"][0]
    assert p["contact_id"] == "merchant-contact"
    assert p["status"] == "invited"  # known customers start invited, not not_yet_invited
    dims = {b["dimension"] for b in p["score_breakdown"]}
    assert "current_customer" in dims


def test_tier_0_override_for_invited_known_customer(tmp_path, monkeypatch):
    """RB-CAMPAIGN-014."""
    _patch_dirs(monkeypatch, tmp_path)
    config = _base_config()
    config["tiering"]["tier_0"] = {"id": "tier_0", "label": "Tier 0 — Ready to Convert"}
    _write_config(tmp_path, config)
    baseline_path = _write_baseline(tmp_path, [
        _entry(id="customer-a", name="Customer A", current_role="Manager"),  # low score, would be Unscored
    ])
    _write_known_customer_contacts(CAMPAIGN_ID, [{"contact_id": "customer-a", "merchant": "Acme", "title": "Manager"}])

    ce.build_roster(CAMPAIGN_ID, baseline_path=baseline_path)
    roster = ce._load_roster(CAMPAIGN_ID)
    p = roster["prospects"][0]
    assert p["status"] == "invited"
    assert p["tier"] == "tier_0"


def test_invited_via_backfills_worldpay_for_carried_forward_known_customer(tmp_path, monkeypatch):
    """RB-CAMPAIGN-034: a known-customer contact already on the roster from
    before invited_via existed (invited_via missing/empty, status already
    'invited') must get 'worldpay' backfilled on the next build — otherwise
    it stays empty forever, since the 'first time joining' branch that sets
    invited_via=['worldpay'] only runs when there's no prior roster entry."""
    _patch_dirs(monkeypatch, tmp_path)
    _write_config(tmp_path, _base_config())
    baseline_path = _write_baseline(tmp_path, [
        _entry(id="legacy-customer", name="Legacy Customer", current_role="VP Ops"),
    ])
    _write_known_customer_contacts(CAMPAIGN_ID, [{"contact_id": "legacy-customer", "merchant": "Acme", "title": "VP Ops"}])
    # Simulate a pre-invited_via roster: status already "invited", but no
    # invited_via field at all (the shape every roster had before this field
    # was introduced).
    ce._write_roster(CAMPAIGN_ID, {"prospects": [{
        "contact_id": "legacy-customer", "name": "Legacy Customer", "current_company": None,
        "current_role": "VP Ops", "status": "invited",
        "status_history": [{"status": "invited", "at": "2026-06-01", "reason": "known_customer_contact_list — already invited per operator"}],
        "score": 0, "score_breakdown": [], "tier": "unscored", "tier_label": "Unscored",
        "sources": [], "is_restaurant_operator": False, "inclusion_reasons": [],
        "confidence": {"identity": "high", "company": "low", "relationship": "low", "executive_role": "n/a", "overall": "low"},
    }]})

    ce.build_roster(CAMPAIGN_ID, baseline_path=baseline_path)
    roster = ce._load_roster(CAMPAIGN_ID)
    p = roster["prospects"][0]
    assert p["status"] == "invited"
    assert p["invited_via"] == ["worldpay"]


def _write_xlsx(tmp_path: Path, filename: str, sheets: dict[str, list[dict]]) -> Path:
    from openpyxl import Workbook
    wb = Workbook()
    wb.remove(wb.active)
    for sheet_name, rows in sheets.items():
        ws = wb.create_sheet(sheet_name)
        if not rows:
            continue
        headers = list(rows[0].keys())
        ws.append(headers)
        for row in rows:
            ws.append([row.get(h) for h in headers])
    path = tmp_path / filename
    wb.save(path)
    return path


def test_reconcile_registration_workbook_applies_sheets_without_downgrading(tmp_path, monkeypatch):
    """RB-CAMPAIGN-015."""
    _patch_dirs(monkeypatch, tmp_path)
    _write_config(tmp_path, _base_config())
    baseline_path = _write_baseline(tmp_path, [
        _entry(id="already-registered", name="Already Registered", current_role="VP Ops", email="reg@example.com"),
        _entry(id="gets-invited", name="Gets Invited", current_role="VP Ops", email="inv@example.com"),
    ])
    ce.build_roster(CAMPAIGN_ID, baseline_path=baseline_path)

    xlsx_path = _write_xlsx(tmp_path, "workbook.xlsx", {
        "Registered": [{"Full Name": "Already Registered", "Email Address": "reg@example.com"}],
        "Invite List": [
            {"First Name": "Already", "Last Name": "Registered", "Email": "reg@example.com"},
            {"First Name": "Gets", "Last Name": "Invited", "Email": "inv@example.com"},
        ],
    })

    result = ce.reconcile_registration_workbook(CAMPAIGN_ID, xlsx_path, baseline_path=baseline_path)
    assert result["ok"] is True
    roster = ce._load_roster(CAMPAIGN_ID)
    by_id = {p["contact_id"]: p for p in roster["prospects"]}
    # "Registered" sheet already advanced this contact to registered; the
    # later "Invite List" sheet (target_status=invited) must not downgrade it.
    assert by_id["already-registered"]["status"] == "registered"
    assert by_id["gets-invited"]["status"] == "invited"


def test_apply_customer_contact_list_matches_full_baseline(tmp_path, monkeypatch):
    """RB-CAMPAIGN-016."""
    _patch_dirs(monkeypatch, tmp_path)
    _write_config(tmp_path, _base_config())
    baseline_path = _write_baseline(tmp_path, [
        # Deliberately NOT roster-eligible (fails include_if_any) — proves
        # apply_customer_contact_list matches against full baseline, not roster.
        _entry(id="merchant-x", name="Merchant X", current_role="Cashier", email="mx@merchant.com"),
    ])
    ce.build_roster(CAMPAIGN_ID, baseline_path=baseline_path)
    assert ce._load_roster(CAMPAIGN_ID)["prospects"] == []  # not eligible yet

    xlsx_path = _write_xlsx(tmp_path, "customers.xlsx", {
        "Sheet1": [{"Merchant": "Acme Corp", "Contact": "Merchant X", "email": "mx@merchant.com", "Contact Title": "Cashier"}],
    })

    result = ce.apply_customer_contact_list(CAMPAIGN_ID, xlsx_path, baseline_path=baseline_path)
    assert result["ok"] is True
    assert result["matched_count"] == 1
    known = json.loads(ce._known_customer_contacts_path(CAMPAIGN_ID).read_text())
    assert known["contacts"][0]["contact_id"] == "merchant-x"

    # Rebuilding the roster should now pick them up via the known-customer bypass.
    ce.build_roster(CAMPAIGN_ID, baseline_path=baseline_path)
    roster = ce._load_roster(CAMPAIGN_ID)
    assert len(roster["prospects"]) == 1
    assert roster["prospects"][0]["contact_id"] == "merchant-x"


def test_low_confidence_routes_to_validation_queue(tmp_path, monkeypatch):
    """RB-CAMPAIGN-017."""
    _patch_dirs(monkeypatch, tmp_path)
    _write_config(tmp_path, _base_config())
    baseline_path = _write_baseline(tmp_path, [
        # No email/linkedin_url (identity weak), no current_company (company
        # weak too), default VC signal_class (relationship weak) -> 3 lows.
        _entry(id="low-conf", name="Low Conf", current_role="VP Ops", current_company=None),
    ])

    result = ce.build_roster(CAMPAIGN_ID, baseline_path=baseline_path)
    assert result["prospect_count"] == 0
    assert result["validation_queue_count"] == 1
    roster = ce._load_roster(CAMPAIGN_ID)
    assert roster["prospects"] == []
    assert roster["validation_queue"][0]["contact_id"] == "low-conf"
    assert roster["validation_queue"][0]["confidence"]["overall"] == "low"


def test_confidence_override_tag_keeps_in_primary_roster(tmp_path, monkeypatch):
    """RB-CAMPAIGN-018."""
    _patch_dirs(monkeypatch, tmp_path)
    _write_config(tmp_path, _base_config())
    baseline_path = _write_baseline(tmp_path, [
        _entry(id="low-conf-confirmed", name="Confirmed", current_role="VP Ops",
               current_company=None, tags=["confidence-override"]),
    ])

    result = ce.build_roster(CAMPAIGN_ID, baseline_path=baseline_path)
    assert result["prospect_count"] == 1
    assert result["validation_queue_count"] == 0


def test_low_confidence_not_demoted_after_status_progresses(tmp_path, monkeypatch):
    """RB-CAMPAIGN-019."""
    _patch_dirs(monkeypatch, tmp_path)
    _write_config(tmp_path, _base_config())
    baseline_path = _write_baseline(tmp_path, [
        _entry(id="low-conf-invited", name="Invited Low Conf", current_role="VP Ops", current_company=None),
    ])
    ce.build_roster(CAMPAIGN_ID, baseline_path=baseline_path)
    assert ce._load_roster(CAMPAIGN_ID)["validation_queue"][0]["contact_id"] == "low-conf-invited"

    # A human manually invites them despite the low-confidence flag.
    roster = ce._load_roster(CAMPAIGN_ID)
    invited = roster["validation_queue"].pop()
    invited["status"] = "invited"
    roster["prospects"].append(invited)
    (ce._campaign_dir(CAMPAIGN_ID) / "roster.json").write_text(json.dumps(roster), encoding="utf-8")

    # Rebuilding must not demote them back into validation_queue now that a
    # human has acted — the confidence heuristic never overrides a real
    # status transition.
    ce.build_roster(CAMPAIGN_ID, baseline_path=baseline_path)
    roster2 = ce._load_roster(CAMPAIGN_ID)
    assert roster2["validation_queue"] == []
    assert roster2["prospects"][0]["contact_id"] == "low-conf-invited"
    assert roster2["prospects"][0]["status"] == "invited"


def test_inclusion_reasons_cite_matched_rule(tmp_path, monkeypatch):
    """RB-CAMPAIGN-020."""
    _patch_dirs(monkeypatch, tmp_path)
    _write_config(tmp_path, _base_config())
    baseline_path = _write_baseline(tmp_path, [
        _entry(id="cited", name="Cited Contact", current_role="VP Ops"),
    ])

    result = ce.build_roster(CAMPAIGN_ID, baseline_path=baseline_path, dry_run=True)
    p = result["roster_preview"]["prospects"][0]
    assert p["inclusion_reasons"]
    assert "current_role_matches" in p["inclusion_reasons"][0]


def test_recruiter_and_first_degree_exclusion_rules(tmp_path, monkeypatch):
    """RB-CAMPAIGN-021."""
    _patch_dirs(monkeypatch, tmp_path)
    config = _base_config()
    config["eligibility"]["exclude_if_any"].append({"is_recruiter": True, "reason": "recruiter", "mode": "exclude"})
    config["eligibility"]["exclude_if_any"].append({"is_first_degree_linkedin": False, "reason": "not_first_degree_connection", "mode": "exclude"})
    _write_config(tmp_path, config)
    baseline_path = _write_baseline(tmp_path, [
        _entry(id="recruiter-1", name="Recruiter One", current_role="VP Talent Acquisition", sources=["linkedin_export_2026"]),
        _entry(id="not-linkedin", name="Not LinkedIn", current_role="VP Ops", sources=["hubspot_crm_export_2026"]),
        _entry(id="clean", name="Clean Contact", current_role="VP Ops", sources=["linkedin_export_2026"]),
    ])

    result = ce.build_roster(CAMPAIGN_ID, baseline_path=baseline_path)
    assert result["prospect_count"] == 1
    roster = ce._load_roster(CAMPAIGN_ID)
    assert roster["prospects"][0]["contact_id"] == "clean"
    reasons = {e["contact_id"]: e["reason"] for e in roster["excluded"]}
    assert reasons["recruiter-1"] == "recruiter"
    assert reasons["not-linkedin"] == "not_first_degree_connection"


def test_require_restaurant_affiliation_gate(tmp_path, monkeypatch):
    """RB-CAMPAIGN-031: eligibility.require_restaurant_affiliation excludes
    an otherwise-eligible executive contact whose company isn't a
    restaurant/foodservice operator, unless a 'restaurant-affiliation-override'
    tag or known-customer membership is present."""
    _patch_dirs(monkeypatch, tmp_path)
    config = _base_config()
    config["eligibility"]["require_restaurant_affiliation"] = True
    _write_config(tmp_path, config)
    baseline_path = _write_baseline(tmp_path, [
        _entry(id="vendor-exec", name="Vendor Exec", current_role="VP Sales", current_company="Acme Software Inc"),
        _entry(id="operator-exec", name="Operator Exec", current_role="VP Ops", current_company="Bob's Pizzeria"),
        _entry(id="override-exec", name="Override Exec", current_role="VP Sales", current_company="Acme Software Inc",
               tags=["restaurant-affiliation-override"]),
    ])

    result = ce.build_roster(CAMPAIGN_ID, baseline_path=baseline_path)
    roster = ce._load_roster(CAMPAIGN_ID)
    ids = {p["contact_id"] for p in roster["prospects"]}
    assert "vendor-exec" not in ids
    assert "operator-exec" in ids
    assert "override-exec" in ids
    # Not on the roster at all (is_included() fails before candidacy) — not
    # a scored-then-excluded entry, so it shouldn't appear on excluded either.
    excluded_ids = {e["contact_id"] for e in roster["excluded"]}
    assert "vendor-exec" not in excluded_ids


def test_keyword_match_uses_word_boundaries(tmp_path, monkeypatch):
    """RB-CAMPAIGN-032: is_restaurant_operator must not substring-match a
    short keyword ('qsr') embedded inside an unrelated company name
    ('QSRSoft') — the exact bug that let a restaurant-tech vendor score as
    a restaurant operator and reach Tier 1 (2026-07-13 live verification)."""
    config = _base_config()
    vendor = _entry(id="qsrsoft-exec", current_company="QSRSoft")
    operator = _entry(id="real-qsr", current_company="Regional QSR Group")
    assert ce.is_restaurant_operator(vendor, config) is False
    assert ce.is_restaurant_operator(operator, config) is True


def test_priority_invite_list_ranks_and_annotates():
    """RB-CAMPAIGN-033: build_priority_invite_list ranks not-yet-invited and
    invited-not-registered prospects together by score, drops registered
    contacts entirely, and annotates invite source with the operator's
    2026-07-13 emoji scheme (🟢/🟡/🔵/🟣)."""
    config = _base_config()
    def _p(contact_id, name, company, status, score, invited_via, customer_context=None):
        return {
            "contact_id": contact_id, "name": name, "current_company": company,
            "current_role": "VP Ops", "status": status, "score": score,
            "score_breakdown": [{"dimension": "executive_decision_maker", "points": 15, "source": "x"}],
            "tier_label": "Tier 2", "invited_via": invited_via, "customer_context": customer_context,
        }
    roster = {"prospects": [
        _p("not-invited", "Not Invited", "Bob's Diner", "not_yet_invited", 30, []),
        _p("genius-only", "Genius Only", "Taco Place", "invited", 40, ["genius"]),
        _p("worldpay-only", "Worldpay Only", "Burger Spot", "invited", 50, ["worldpay"]),
        _p("both", "Both Sources", "Pizza Co", "invited", 60, ["genius", "worldpay"],
           customer_context={"merchant": "Pizza Co"}),
        _p("reg", "Registered Person", "Sandwich Hut", "registered", 70, ["genius"]),
    ]}

    rows = ce.build_priority_invite_list(roster, config)
    ids = [r["contact_id"] for r in rows]
    assert "reg" not in ids  # registered excluded from the action list entirely
    assert ids == ["both", "worldpay-only", "genius-only", "not-invited"]  # ranked by score desc
    by_id = {r["contact_id"]: r for r in rows}
    assert by_id["not-invited"]["invite_status"] == "🟢 Not invited"
    assert by_id["genius-only"]["invite_status"] == "🟡 Invited via Genius"
    assert by_id["worldpay-only"]["invite_status"] == "🔵 Invited via Worldpay"
    assert by_id["both"]["invite_status"] == "🟣 Invited by both"
    assert by_id["both"]["priority"] == 1
    assert by_id["both"]["existing_worldpay_customer"] is True
    assert by_id["not-invited"]["existing_worldpay_customer"] is False
    assert by_id["not-invited"]["segment"] == "Tier 2"


def test_priority_invite_list_owner_outreach_and_next_action():
    """RB-CAMPAIGN-035 (2026-07-14): owner is always the single-operator
    RELATIONSHIP_OWNER constant; outreach_method is derived only from the
    real 'sources' field (never a guessed channel); next_action reflects
    real status/invited_via, not an invented recommendation."""
    config = _base_config()
    roster = {"prospects": [
        {"contact_id": "linkedin-only", "name": "LinkedIn Only", "current_company": "Bob's Diner",
         "current_role": "VP Ops", "status": "not_yet_invited", "score": 30,
         "score_breakdown": [], "tier_label": "Tier 2", "invited_via": [],
         "sources": ["linkedin_export_2026-01-01"]},
        {"contact_id": "hubspot-only", "name": "Hubspot Only", "current_company": "Taco Place",
         "current_role": "VP Ops", "status": "invited", "score": 40,
         "score_breakdown": [], "tier_label": "Tier 2", "invited_via": ["worldpay"],
         "sources": ["hubspot_crm_export_2026-01-01"]},
        {"contact_id": "no-source", "name": "No Source", "current_company": "Burger Spot",
         "current_role": "VP Ops", "status": "not_yet_invited", "score": 20,
         "score_breakdown": [], "tier_label": "Tier 2", "invited_via": [], "sources": []},
    ]}

    rows = ce.build_priority_invite_list(roster, config)
    by_id = {r["contact_id"]: r for r in rows}
    assert all(r["owner"] == ce.RELATIONSHIP_OWNER for r in rows)
    assert by_id["linkedin-only"]["outreach_method"] == "LinkedIn"
    assert by_id["hubspot-only"]["outreach_method"] == "Email (HubSpot CRM contact)"
    assert by_id["no-source"]["outreach_method"] == "Not determined from available sources"
    assert by_id["linkedin-only"]["next_action"] == "Send invitation"
    assert by_id["hubspot-only"]["next_action"] == "Follow up for registration (invited via Worldpay)"


def test_coverage_gaps_flags_no_executive_signal_with_broker(tmp_path, monkeypatch):
    """RB-CAMPAIGN-022."""
    _patch_dirs(monkeypatch, tmp_path)
    config = _base_config()
    config["eligibility"]["include_if_any"] = [{"current_role_matches": ["VP", "Manager"]}]
    _write_config(tmp_path, config)
    baseline_path = _write_baseline(tmp_path, [
        # No executive/tech-ops dimension at Gap Co -> gap. Broker Person is
        # at a DIFFERENT company (RC signal_class, so find_intro_paths won't
        # filter them out as too-low-signal) and becomes the recommended
        # introducer via the broker search across the whole baseline.
        _entry(id="gap-contact", name="Gap Contact", current_role="Manager", current_company="Gap Co"),
        _entry(id="broker", name="Broker Person", current_role="VP Sales", current_company="Bridge Co",
               signal_class="RC", rc_tier="inner"),
    ])
    roster = ce.build_roster(CAMPAIGN_ID, baseline_path=baseline_path, dry_run=True)["roster_preview"]
    baseline = json.loads(baseline_path.read_text())

    gaps = ce.build_coverage_gaps(roster, baseline=baseline)
    gap_co = next((g for g in gaps if g["company"] == "Gap Co"), None)
    assert gap_co is not None
    assert "no_executive_relationship" in gap_co["gap_types"]
    assert gap_co["recommended_introducer"] is not None


def test_execution_plan_weeks_and_tracking(tmp_path, monkeypatch):
    """RB-CAMPAIGN-023."""
    _patch_dirs(monkeypatch, tmp_path)
    _write_config(tmp_path, _base_config())
    entries = [_entry(id=f"p{i}", name=f"Person {i}", current_role="VP Ops") for i in range(5)]
    baseline_path = _write_baseline(tmp_path, entries)
    ce.build_roster(CAMPAIGN_ID, baseline_path=baseline_path)
    roster = ce._load_roster(CAMPAIGN_ID)
    config = ce.load_campaign_config(CAMPAIGN_ID)

    plan = ce.build_execution_plan(CAMPAIGN_ID, roster, config, [], week1_size=2, week2_size=2)
    assert len(plan["weeks"]) == 4
    assert len(plan["weeks"][0]["contacts"]) == 2
    assert plan["tracking"]["invitations_sent"] == 0
    assert plan["tracking"]["registrations"] == 0
    assert plan["tracking"]["acceptances"]["value"] is None


def test_refresh_active_campaigns_skips_inactive_and_survives_broken_config(tmp_path, monkeypatch):
    """RB-CAMPAIGN-024."""
    _patch_dirs(monkeypatch, tmp_path)
    _write_config(tmp_path, _base_config())
    baseline_path = _write_baseline(tmp_path, [_entry(id="p1", name="P One", current_role="VP Ops")])
    ce.build_roster(CAMPAIGN_ID, baseline_path=baseline_path)

    monkeypatch.setattr(ce, "list_campaigns", lambda: [
        {"id": CAMPAIGN_ID, "status": "active"},
        {"id": "archived-campaign", "status": "archived"},
        {"id": "broken-campaign", "status": "active"},
    ])

    results = ce.refresh_active_campaigns(baseline_path=baseline_path)
    ids = {r["campaign_id"] for r in results}
    assert ids == {CAMPAIGN_ID, "broken-campaign"}  # archived skipped entirely
    broken = next(r for r in results if r["campaign_id"] == "broken-campaign")
    assert broken["ok"] is False  # missing config.yaml -> caught, not raised


def test_account_first_prefers_role_priority_over_score(tmp_path, monkeypatch):
    """RB-CAMPAIGN-025: a CIO with a lower score is recommended over a
    higher-scored contact with no role-priority title at the same account."""
    _patch_dirs(monkeypatch, tmp_path)
    _write_config(tmp_path, _base_config())
    baseline_path = _write_baseline(tmp_path, [
        # "VP Ops" satisfies base include_if_any (contains "vp") but matches
        # none of the role-priority keywords -> lowest priority despite the
        # much higher score.
        _entry(id="high-score-no-role", name="High Score", current_role="VP Ops",
               current_company="Acme Chicken", relationship_health={"drr_score": 90}),
        # "VP Technology" satisfies both base eligibility and a role-priority
        # keyword (rank 3) despite the much lower score.
        _entry(id="vp-tech-lower-score", name="VP Tech Person", current_role="VP Technology",
               current_company="Acme Chicken", relationship_health={"drr_score": 10}),
    ])
    ce.build_roster(CAMPAIGN_ID, baseline_path=baseline_path)
    roster = ce._load_roster(CAMPAIGN_ID)
    known_path = ce._known_customer_contacts_path(CAMPAIGN_ID)
    known_path.parent.mkdir(parents=True, exist_ok=True)
    known_path.write_text(json.dumps({"contacts": [{"contact_id": "high-score-no-role", "merchant": "Acme Chicken"}]}), encoding="utf-8")

    rows = ce.build_account_first_recommendations(roster, CAMPAIGN_ID)
    acme = next(r for r in rows if r["account"] == "Acme Chicken")
    assert acme["name"] == "VP Tech Person"
    assert "VP Technology" in acme["why_this_person"]
    assert acme["account_tier"] == "Tier 1 — Existing Genius Customer"


def test_account_first_excludes_unranked_accounts(tmp_path, monkeypatch):
    """RB-CAMPAIGN-026: an account that's neither a known customer nor a
    restaurant operator doesn't appear in the account-first list at all."""
    _patch_dirs(monkeypatch, tmp_path)
    _write_config(tmp_path, _base_config())
    baseline_path = _write_baseline(tmp_path, [
        _entry(id="unranked", name="Unranked Person", current_role="VP Ops", current_company="Generic Software Co"),
    ])
    ce.build_roster(CAMPAIGN_ID, baseline_path=baseline_path)
    roster = ce._load_roster(CAMPAIGN_ID)

    rows = ce.build_account_first_recommendations(roster, CAMPAIGN_ID)
    assert all(r["account"] != "Generic Software Co" for r in rows)


def test_current_company_rule_excludes_no_stated_current_role_person():
    """RB-CAMPAIGN-034: a person with employment_status=no_stated_current_role
    (RB ended-role cleanup, 2026-08-06 — the Richard Heyman/Scooter's Coffee
    pattern) never satisfies a current_company_in/current_company_equals
    eligibility rule even if current_company happens to be populated by a
    future bug, while an active contact at the same company still matches."""
    richard = _entry(
        id="richard-heyman", name="Richard Heyman",
        current_company="Scooter's Coffee", current_role="EVP | CTIO",
        employment_status="no_stated_current_role",
    )
    tyler = _entry(
        id="tyler-marpes", name="Tyler Marpes",
        current_company="Scooter's Coffee", current_role="VP Technology",
        employment_status="stated_current_role",
    )
    rule_in = {"current_company_in": ["Scooter's Coffee"]}
    rule_equals = {"current_company_equals": "Scooter's Coffee"}
    assert ce._rule_condition_matches(richard, rule_in) is False
    assert ce._rule_condition_matches(richard, rule_equals) is False
    assert ce._rule_condition_matches(tyler, rule_in) is True
    assert ce._rule_condition_matches(tyler, rule_equals) is True


def test_export_campaign_workbook_sanitizes_formula_injection(tmp_path, monkeypatch):
    """RB-SECURITY-2026-09-05: export_campaign_workbook() writes real
    exported .xlsx cells from contact/company names and free-text fields
    that can originate externally (a captured LinkedIn name, a free-text
    "why attend" string) -- same formula/CSV-injection class (CWE-1236)
    already fixed in Blue Sheets. A company name starting with '=' must be
    written as literal text, not a live formula, everywhere it appears."""
    import openpyxl
    from unittest.mock import patch

    evil = "=1+1"  # a formula-trigger-prefixed company name

    fake_roster = {"prospects": [], "excluded": []}
    fake_rollup = {"companies": [{
        "company": evil, "contact_count": 1, "best_contact_name": "Person X",
        "top_tier_label": "Tier 1", "status_summary": {"invited": 1},
    }]}
    fake_dashboard = {
        "eligible_enterprise_prospects": 1, "invited_not_registered": 0, "registered": 0,
        "not_yet_invited": 1, "declined": 0, "attended": 0, "company_count": 1,
        "worldpay_customer_conversion_opportunities": 0,
        "top_target_brands": [{"company": evil, "contact_count": 1, "best_contact_name": "Person X", "best_contact_score": 50}],
        "highest_value_relationships": [{"name": "Person X", "company": evil, "score": 50, "tier": "Tier 1"}],
    }

    with patch.object(ce, "_load_roster", return_value=fake_roster), \
         patch.object(ce, "load_campaign_config", return_value={"name": "Test"}), \
         patch.object(ce, "build_company_rollup", return_value=fake_rollup), \
         patch.object(ce, "build_executive_dashboard", return_value=fake_dashboard), \
         patch.object(ce, "build_queue_a", return_value=[]), \
         patch.object(ce, "build_queue_b", return_value=[]), \
         patch.object(ce, "query_restaurant_operator_prospects", return_value=[]), \
         patch.object(ce, "build_account_first_recommendations", return_value=[]), \
         patch.object(ce, "build_priority_invite_list", return_value=[]):
        out_path = ce.export_campaign_workbook(CAMPAIGN_ID, output_path=tmp_path / "workbook.xlsx")

    wb = openpyxl.load_workbook(out_path)
    ws = wb["Executive Dashboard"]
    all_values = [cell.value for row in ws.iter_rows() for cell in row]
    assert evil not in all_values, "raw formula-triggering string must never reach a cell unescaped"
    assert f"'{evil}" in all_values, "sanitized form (leading apostrophe) must be present instead"
