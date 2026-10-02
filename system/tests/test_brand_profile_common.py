"""
test_brand_profile_common.py — Ecosystem Lookup Tool, Phase A.

Covers: the honest-blank field convention, the trajectory-badge rule
(decision #3: Technomic net-unit-change YoY, ±3% bands, sales-delta
fallback, explicit insufficient_data rather than a guessed default),
footprint derivation from Technomic data with human-review protection, and
basic profile storage round-tripping.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import brand_profile_common as bpc  # noqa: E402


def _entity(brand_id="brand-test", *, unit_delta=None, sales_delta=None,
            technomic_history=None, technomic_detailed_history=None, unit_count=None):
    attrs = {}
    if unit_delta is not None:
        attrs["unit_delta"] = unit_delta
    if sales_delta is not None:
        attrs["sales_delta"] = sales_delta
    if technomic_history is not None:
        attrs["technomic_history"] = technomic_history
    if technomic_detailed_history is not None:
        attrs["technomic_detailed_history"] = technomic_detailed_history
    if unit_count is not None:
        attrs["unit_count"] = unit_count
    return {"id": brand_id, "name": "Test Brand", "entity_type": "brand", "attributes": attrs}


# --- trajectory ---------------------------------------------------------

def test_growing_above_3_percent_unit_delta():
    entity = _entity(unit_delta=5.2)
    result = bpc.compute_trajectory(entity)
    assert result["badge"] == "Growing"
    assert result["metric_used"] == "unit_delta_pct"
    assert result["value_pct"] == 5.2


def test_contracting_below_negative_3_percent():
    entity = _entity(unit_delta=-4.1)
    result = bpc.compute_trajectory(entity)
    assert result["badge"] == "Contracting"


def test_flat_within_band():
    entity = _entity(unit_delta=1.0)
    assert bpc.compute_trajectory(entity)["badge"] == "Flat"


def test_boundary_exactly_3_percent_is_flat_not_growing():
    """The rule is '> +3%', not '>= +3%' -- exactly at the boundary is Flat."""
    entity = _entity(unit_delta=3.0)
    assert bpc.compute_trajectory(entity)["badge"] == "Flat"


def test_boundary_exactly_negative_3_percent_is_flat_not_contracting():
    entity = _entity(unit_delta=-3.0)
    assert bpc.compute_trajectory(entity)["badge"] == "Flat"


def test_falls_back_to_sales_delta_when_unit_data_missing():
    entity = _entity(sales_delta=6.0)
    result = bpc.compute_trajectory(entity)
    assert result["badge"] == "Growing"
    assert result["metric_used"] == "sales_delta_pct"


def test_unit_delta_preferred_over_sales_delta_when_both_present():
    entity = _entity(unit_delta=-5.0, sales_delta=10.0)
    result = bpc.compute_trajectory(entity)
    assert result["metric_used"] == "unit_delta_pct"
    assert result["badge"] == "Contracting"


def test_neither_metric_present_is_insufficient_data_never_a_guess():
    entity = _entity()
    result = bpc.compute_trajectory(entity)
    assert result["badge"] == "insufficient_data"
    assert result["value_pct"] is None
    assert result["metric_used"] is None


def test_underlying_numbers_are_shown_not_just_the_badge():
    """Todd's explicit design rule: 'Show the underlying numbers, not just
    the badge.'"""
    hist = {"2024": {"system_sales_usd": 100.0, "unit_count": 50.0, "units_delta_pct": 4.0}}
    entity = _entity(unit_delta=4.0, technomic_history=hist)
    result = bpc.compute_trajectory(entity)
    assert result["underlying"] == hist["2024"]
    assert result["as_of_year"] == "2024"
    assert "rule" in result and "3%" in result["rule"]


def test_latest_year_selected_when_multiple_years_present():
    hist = {"2019": {"unit_count": 10.0}, "2024": {"unit_count": 50.0}, "2022": {"unit_count": 30.0}}
    entity = _entity(unit_delta=1.0, technomic_history=hist)
    assert bpc.compute_trajectory(entity)["as_of_year"] == "2024"


# --- footprint derivation ------------------------------------------------

def test_footprint_derived_from_technomic_on_a_fresh_profile():
    entity = _entity(
        unit_count=100.0,
        technomic_detailed_history={"2024": {"franchise_units": 80.0, "company_units": 20.0}},
    )
    profile = bpc.empty_profile("brand-test", "Test Brand")
    bpc._refresh_footprint(profile, entity)
    assert profile["footprint"]["total_units"]["value"] == 100.0
    assert profile["footprint"]["franchised_units"]["value"] == 80.0
    assert profile["footprint"]["company_owned_units"]["value"] == 20.0
    assert profile["footprint"]["total_units"]["status"] == "confirmed"
    assert profile["footprint"]["total_units"]["last_reviewed_by"] == "system:technomic_ingest"


def test_footprint_missing_technomic_data_stays_unresearched():
    entity = _entity()  # no attributes at all
    profile = bpc.empty_profile("brand-test", "Test Brand")
    bpc._refresh_footprint(profile, entity)
    assert profile["footprint"]["total_units"]["status"] == "not_yet_researched"
    assert profile["footprint"]["franchisee_count"]["status"] == "not_yet_researched"  # never derivable


def test_human_reviewed_footprint_field_is_never_overwritten_by_refresh():
    """A confirmed human correction always wins over a re-derived value --
    the whole point of is_human_reviewed()."""
    entity = _entity(unit_count=999.0)
    profile = bpc.empty_profile("brand-test", "Test Brand")
    profile["footprint"]["total_units"] = bpc.field(
        12345, status="confirmed", last_reviewed_by="human:todd",
    )
    bpc._refresh_footprint(profile, entity)
    assert profile["footprint"]["total_units"]["value"] == 12345  # untouched


def test_system_derived_footprint_field_is_refreshed_on_later_call():
    """A system-derived value (not human-reviewed) DOES get updated when
    the underlying Technomic data changes -- this is a live re-derivation,
    not a one-time snapshot."""
    entity_v1 = _entity(unit_count=100.0)
    profile = bpc.empty_profile("brand-test", "Test Brand")
    bpc._refresh_footprint(profile, entity_v1)
    assert profile["footprint"]["total_units"]["value"] == 100.0

    entity_v2 = _entity(unit_count=150.0)
    bpc._refresh_footprint(profile, entity_v2)
    assert profile["footprint"]["total_units"]["value"] == 150.0


# --- field provenance shape ------------------------------------------------

def test_unresearched_field_shape_matches_real_field_shape():
    """Both must carry the exact same key set so a reader never has to
    special-case which kind of field it's looking at."""
    assert set(bpc.unresearched_field().keys()) == set(bpc.field("x").keys())


def test_is_human_reviewed_true_for_human_and_team_prefixes():
    assert bpc.is_human_reviewed(bpc.field("x", last_reviewed_by="human:todd"))
    assert bpc.is_human_reviewed(bpc.field("x", last_reviewed_by="team:jane"))


def test_is_human_reviewed_false_for_system_and_unset():
    assert not bpc.is_human_reviewed(bpc.field("x", last_reviewed_by="system:technomic_ingest"))
    assert not bpc.is_human_reviewed(bpc.unresearched_field())
    assert not bpc.is_human_reviewed(None)


# --- storage round-trip ----------------------------------------------------

def test_profile_round_trips_through_save_and_load(tmp_path):
    with patch.object(bpc, "ROOT", tmp_path):
        profile = bpc.empty_profile("brand-test", "Test Brand")
        profile["synopsis"] = bpc.field("A real test synopsis.", status="confirmed")
        bpc.save_profile("brand-test", profile)
        loaded = bpc.load_profile("brand-test")
        assert loaded["synopsis"]["value"] == "A real test synopsis."


def test_load_profile_returns_none_when_no_file_exists(tmp_path):
    with patch.object(bpc, "ROOT", tmp_path):
        assert bpc.load_profile("brand-does-not-exist") is None


def test_profile_path_rejects_unsafe_brand_id():
    import pytest
    with pytest.raises(ValueError):
        bpc.profile_path("../../etc/passwd")


# --- get_profile against the real graph -------------------------------------

def test_get_profile_raises_not_found_for_unknown_brand():
    import pytest
    with pytest.raises(bpc.NotFoundError):
        bpc.get_profile("brand-this-does-not-exist-anywhere")


def test_get_profile_raises_not_found_for_a_vendor_not_a_brand():
    """entity_type must be "brand" -- a vendor id must not resolve here."""
    import pytest
    import ecosystem_intelligence as ei
    graph = ei._read_graph()
    vendor = next((e for e in graph["entities"] if e.get("entity_type") == "vendor"), None)
    assert vendor is not None, "expected at least one real vendor entity in the graph"
    with pytest.raises(bpc.NotFoundError):
        bpc.get_profile(vendor["id"])


def test_get_profile_never_persists_by_default(tmp_path):
    with patch.object(bpc, "ROOT", tmp_path):
        import ecosystem_intelligence as ei
        graph = ei._read_graph()
        real_brand = next(e for e in graph["entities"] if e.get("entity_type") == "brand")
        bpc.get_profile(real_brand["id"], persist=False)
        assert bpc.load_profile(real_brand["id"]) is None


# --- shareable view (Phase C) -----------------------------------------------

def test_shareable_view_excludes_reported_unverified_leadership():
    profile = bpc.empty_profile("brand-test", "Test Brand")
    profile["leadership"]["confirmed"] = [{"name": "Jane Doe", "title": "CEO"}]
    profile["leadership"]["reported_unverified"] = [{"name": "Rumor Guy", "title": "maybe CFO?"}]
    view = bpc.shareable_view(profile)
    assert view["leadership"]["confirmed"] == [{"name": "Jane Doe", "title": "CEO"}]
    assert "reported_unverified" not in view["leadership"]


def test_shareable_view_includes_all_other_top_level_facts():
    profile = bpc.empty_profile("brand-test", "Test Brand")
    view = bpc.shareable_view(profile)
    for key in ("brand_id", "brand_name", "identity", "synopsis", "footprint", "recent_signals"):
        assert key in view


def test_shareable_view_never_mutates_the_original_profile():
    profile = bpc.empty_profile("brand-test", "Test Brand")
    profile["leadership"]["reported_unverified"] = [{"name": "Rumor Guy"}]
    bpc.shareable_view(profile)
    assert profile["leadership"]["reported_unverified"] == [{"name": "Rumor Guy"}]


def test_shareable_view_excludes_private_pain_point_entries():
    """Real 2026-09-29 finding: pain_points/recent_signals were allowlisted
    as whole FIELDS, with no per-entry visibility -- a single entry added
    through the Team Portal's own add_brand_pain_point() write path could
    carry content Todd didn't mean to expose broadly, with no way to
    exclude just that one entry. This is the fix, and the exact real-world
    entry (Burger King kiosk-downtime pain point) that was live-leaking."""
    profile = bpc.empty_profile("brand-test", "Test Brand")
    profile["pain_points"] = [
        bpc.pain_point_field("Public-facing need", visibility="team_shareable"),
        bpc.pain_point_field("Franchisees complaining about kiosk downtime", visibility="private"),
    ]
    view = bpc.shareable_view(profile)
    values = [p["value"] for p in view["pain_points"]]
    assert values == ["Public-facing need"]


def test_shareable_view_defaults_pre_visibility_entries_to_shareable():
    """Entries written before the visibility field existed have no such
    key at all -- must default to shareable, preserving prior behavior
    for old data rather than silently hiding everything retroactively."""
    profile = bpc.empty_profile("brand-test", "Test Brand")
    profile["pain_points"] = [{"value": "Old entry, no visibility key", "status": "reported"}]
    view = bpc.shareable_view(profile)
    assert view["pain_points"] == profile["pain_points"]


def test_shareable_view_excludes_private_recent_signals_too():
    profile = bpc.empty_profile("brand-test", "Test Brand")
    profile["recent_signals"] = [
        bpc.signal_field("Public signal", signal_type="expansion_or_contraction", visibility="team_shareable"),
        bpc.signal_field("Private signal", signal_type="expansion_or_contraction", visibility="private"),
    ]
    view = bpc.shareable_view(profile)
    values = [s["value"] for s in view["recent_signals"]]
    assert values == ["Public signal"]


def test_set_pain_point_visibility_round_trips_through_disk(tmp_path):
    graph = {"entities": [_entity("brand-test")]}
    with patch.object(bpc, "ROOT", tmp_path):
        profile = bpc.empty_profile("brand-test", "Test Brand")
        bpc.add_pain_point(profile, value="Some real need")
        bpc.save_profile("brand-test", profile)

        bpc.set_pain_point_visibility("brand-test", "Some real need", "private", graph=graph)

        reloaded = bpc.load_profile("brand-test")
        assert reloaded["pain_points"][0]["visibility"] == "private"
        assert bpc.shareable_view(reloaded)["pain_points"] == []


def test_set_pain_point_visibility_rejects_unknown_value(tmp_path):
    graph = {"entities": [_entity("brand-test")]}
    with patch.object(bpc, "ROOT", tmp_path):
        profile = bpc.empty_profile("brand-test", "Test Brand")
        bpc.save_profile("brand-test", profile)
        with pytest.raises(ValueError):
            bpc.set_pain_point_visibility("brand-test", "Does not exist", "private", graph=graph)


def test_set_pain_point_visibility_rejects_invalid_visibility_value(tmp_path):
    graph = {"entities": [_entity("brand-test")]}
    with patch.object(bpc, "ROOT", tmp_path):
        profile = bpc.empty_profile("brand-test", "Test Brand")
        bpc.add_pain_point(profile, value="Some real need")
        bpc.save_profile("brand-test", profile)
        with pytest.raises(ValueError):
            bpc.set_pain_point_visibility("brand-test", "Some real need", "not-a-real-value", graph=graph)


# ---------------------------------------------------------------------------
# recent_signals structure (2026-09-25 deep-research expansion)
# ---------------------------------------------------------------------------

def test_signal_field_rejects_unknown_signal_type():
    with pytest.raises(ValueError):
        bpc.signal_field("x", signal_type="not_a_real_type")


def test_signal_field_defaults_to_reported_not_confirmed():
    f = bpc.signal_field("A real observation.", signal_type="challenge_or_headwind")
    assert f["status"] == "reported"
    assert f["signal_type"] == "challenge_or_headwind"
    assert f["as_of"] == bpc.today()


def test_add_signal_appends_to_recent_signals():
    profile = bpc.empty_profile("brand-test", "Test Brand")
    bpc.add_signal(profile, value="Opening 40 new units in 2027.", signal_type="expansion_or_contraction")
    assert len(profile["recent_signals"]) == 1
    assert profile["recent_signals"][0]["signal_type"] == "expansion_or_contraction"


def test_add_signal_dedupes_identical_type_and_value_by_default():
    profile = bpc.empty_profile("brand-test", "Test Brand")
    bpc.add_signal(profile, value="Same fact.", signal_type="strategic_initiative")
    bpc.add_signal(profile, value="Same fact.", signal_type="strategic_initiative")
    assert len(profile["recent_signals"]) == 1


def test_add_signal_dedupe_false_allows_duplicate():
    profile = bpc.empty_profile("brand-test", "Test Brand")
    bpc.add_signal(profile, value="Same fact.", signal_type="strategic_initiative")
    bpc.add_signal(profile, value="Same fact.", signal_type="strategic_initiative", dedupe=False)
    assert len(profile["recent_signals"]) == 2


def test_add_signal_never_overwrites_existing_entries():
    profile = bpc.empty_profile("brand-test", "Test Brand")
    bpc.add_signal(profile, value="First signal.", signal_type="financial_health")
    bpc.add_signal(profile, value="Second, different signal.", signal_type="leadership_change")
    assert len(profile["recent_signals"]) == 2
    values = {s["value"] for s in profile["recent_signals"]}
    assert values == {"First signal.", "Second, different signal."}


def test_recent_signals_pass_through_shareable_view_unredacted():
    profile = bpc.empty_profile("brand-test", "Test Brand")
    bpc.add_signal(profile, value="Real, shareable fact.", signal_type="competitive_positioning")
    view = bpc.shareable_view(profile)
    assert view["recent_signals"][0]["value"] == "Real, shareable fact."
