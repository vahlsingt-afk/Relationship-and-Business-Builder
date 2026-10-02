"""
test_team_profile_submissions.py — Ecosystem Lookup Tool, Todd's
mid-implementation addition (2026-09-25): team-submitted profile/competitor
edits must go through review, never live-on-submit.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import brand_profile_common as bpc  # noqa: E402
import team_profile_submissions as tps  # noqa: E402


@pytest.fixture()
def isolated_queue(tmp_path):
    queue_path = tmp_path / "team_profile_submissions.json"
    with patch.object(tps, "QUEUE_PATH", queue_path):
        yield queue_path


def test_submit_rejects_invalid_target_type(isolated_queue):
    with pytest.raises(ValueError):
        tps.submit(target_type="vendor", target_id="x", field_path="y",
                   proposed_value="z", submitted_by="jane")


def test_submit_requires_target_id_and_field_path(isolated_queue):
    with pytest.raises(ValueError):
        tps.submit(target_type="brand", target_id="", field_path="identity.hq_city_state",
                   proposed_value="Chicago, IL", submitted_by="jane")


def test_submission_is_pending_and_not_yet_applied(isolated_queue, tmp_path):
    with patch.object(bpc, "ROOT", tmp_path / "brand_profiles"):
        submission = tps.submit(
            target_type="brand", target_id="brand-test", field_path="identity.hq_city_state",
            proposed_value="Chicago, IL", submitted_by="jane",
        )
        assert submission["status"] == "pending"
        assert submission["submission_id"] == "sub-00001"
        # Not applied to the live profile yet.
        assert bpc.load_profile("brand-test") is None


def test_pending_filters_by_target_type(isolated_queue):
    tps.submit(target_type="brand", target_id="brand-a", field_path="synopsis",
               proposed_value="x", submitted_by="jane")
    tps.submit(target_type="competitor", target_id="olo", field_path="strengths",
               proposed_value=["fast integration"], submitted_by="jane")
    assert len(tps.pending()) == 2
    assert len(tps.pending(target_type="brand")) == 1
    assert len(tps.pending(target_type="competitor")) == 1


def test_list_submissions_filters_by_status_and_target_type(isolated_queue, tmp_path):
    with patch.object(bpc, "ROOT", tmp_path / "brand_profiles"):
        s1 = tps.submit(target_type="brand", target_id="brand-a", field_path="synopsis",
                         proposed_value="x", submitted_by="jane")
        tps.submit(target_type="competitor", target_id="olo", field_path="strengths",
                   proposed_value=["fast integration"], submitted_by="jane")
        tps.confirm(s1["submission_id"], reviewed_by="todd")

        assert len(tps.list_submissions()) == 2
        assert len(tps.list_submissions(status="pending")) == 1
        assert len(tps.list_submissions(status="confirmed")) == 1
        assert len(tps.list_submissions(status="confirmed", target_type="brand")) == 1
        assert len(tps.list_submissions(status="confirmed", target_type="competitor")) == 0
        assert len(tps.list_submissions(status="rejected")) == 0


def test_confirm_applies_value_to_live_brand_profile(isolated_queue, tmp_path):
    with patch.object(bpc, "ROOT", tmp_path / "brand_profiles"):
        submission = tps.submit(
            target_type="brand", target_id="brand-test", field_path="identity.hq_city_state",
            proposed_value="Chicago, IL", submitted_by="jane", source_url="https://example.com/hq",
        )
        confirmed = tps.confirm(submission["submission_id"], reviewed_by="todd")
        assert confirmed["status"] == "confirmed"
        assert confirmed["reviewed_by"] == "todd"

        profile = bpc.load_profile("brand-test")
        assert profile["identity"]["hq_city_state"]["value"] == "Chicago, IL"
        assert profile["identity"]["hq_city_state"]["status"] == "confirmed"
        assert profile["identity"]["hq_city_state"]["last_reviewed_by"] == "human:todd"
        assert profile["identity"]["hq_city_state"]["source_url"] == "https://example.com/hq"


def test_confirmed_field_is_indistinguishable_in_shape_from_any_other_human_field(isolated_queue, tmp_path):
    """A team-submitted-then-confirmed fact must carry the exact same
    provenance shape as a deep-research-sourced one."""
    with patch.object(bpc, "ROOT", tmp_path / "brand_profiles"):
        submission = tps.submit(
            target_type="brand", target_id="brand-test", field_path="synopsis",
            proposed_value="A real synopsis.", submitted_by="jane",
        )
        tps.confirm(submission["submission_id"], reviewed_by="todd")
        profile = bpc.load_profile("brand-test")
        applied = profile["synopsis"]
        manual = bpc.field("Another fact.", last_reviewed_by="human:todd")
        assert set(applied.keys()) == set(manual.keys())
        assert bpc.is_human_reviewed(applied)


def test_confirm_appends_to_list_shaped_competitor_field(isolated_queue, tmp_path):
    """strengths/weaknesses/etc. are lists of individually-sourced entries
    -- a confirmed submission appends, it never overwrites the whole list."""
    import competitor_intelligence_common as cic
    with patch.object(cic, "ROOT", tmp_path / "competitor_intelligence"):
        cic.create_competitor_shell("olo", "Olo")
        submission = tps.submit(
            target_type="competitor", target_id="olo", field_path="strengths",
            proposed_value="Deep POS integrations", submitted_by="jane",
            source_url="https://example.com/olo-review",
        )
        tps.confirm(submission["submission_id"], reviewed_by="todd")
        competitor = cic.load_json(cic.competitor_dir("olo") / "competitor.json")
        assert len(competitor["strengths"]) == 1
        assert competitor["strengths"][0]["value"] == "Deep POS integrations"
        assert competitor["strengths"][0]["last_reviewed_by"] == "human:todd"
        assert competitor["strengths"][0]["source_url"] == "https://example.com/olo-review"

        # A second confirmed submission appends rather than replacing the first.
        submission2 = tps.submit(
            target_type="competitor", target_id="olo", field_path="strengths",
            proposed_value="Strong developer API", submitted_by="jane",
        )
        tps.confirm(submission2["submission_id"], reviewed_by="todd")
        competitor = cic.load_json(cic.competitor_dir("olo") / "competitor.json")
        assert len(competitor["strengths"]) == 2


def test_confirm_overwrites_single_valued_competitor_field(isolated_queue, tmp_path):
    """"trends" (and positioning_summary/todds_pov) are single prose
    fields, not lists -- a submission overwrites, matching brand-profile
    field behavior."""
    import competitor_intelligence_common as cic
    with patch.object(cic, "ROOT", tmp_path / "competitor_intelligence"):
        cic.create_competitor_shell("olo", "Olo")
        submission = tps.submit(
            target_type="competitor", target_id="olo", field_path="trends",
            proposed_value="Consolidating around unified ordering platforms.",
            submitted_by="jane",
        )
        tps.confirm(submission["submission_id"], reviewed_by="todd")
        competitor = cic.load_json(cic.competitor_dir("olo") / "competitor.json")
        assert competitor["trends"]["value"] == "Consolidating around unified ordering platforms."
        assert isinstance(competitor["trends"], dict)  # never became a list


def test_reject_never_applies_and_keeps_the_record_for_audit(isolated_queue, tmp_path):
    with patch.object(bpc, "ROOT", tmp_path / "brand_profiles"):
        submission = tps.submit(
            target_type="brand", target_id="brand-test", field_path="identity.founded_year",
            proposed_value="1955", submitted_by="jane",
        )
        rejected = tps.reject(submission["submission_id"], reviewed_by="todd", review_note="unsourced")
        assert rejected["status"] == "rejected"
        assert rejected["review_note"] == "unsourced"
        assert bpc.load_profile("brand-test") is None  # never applied
        # Still retrievable, not deleted.
        assert tps.get(submission["submission_id"])["status"] == "rejected"


def test_confirm_twice_raises_already_reviewed(isolated_queue, tmp_path):
    with patch.object(bpc, "ROOT", tmp_path / "brand_profiles"):
        submission = tps.submit(
            target_type="brand", target_id="brand-test", field_path="synopsis",
            proposed_value="x", submitted_by="jane",
        )
        tps.confirm(submission["submission_id"], reviewed_by="todd")
        with pytest.raises(tps.AlreadyReviewedError):
            tps.confirm(submission["submission_id"], reviewed_by="todd")


def test_confirm_unknown_submission_id_raises_not_found(isolated_queue):
    with pytest.raises(tps.NotFoundError):
        tps.confirm("sub-99999", reviewed_by="todd")


def test_reject_after_confirm_raises_already_reviewed(isolated_queue, tmp_path):
    with patch.object(bpc, "ROOT", tmp_path / "brand_profiles"):
        submission = tps.submit(
            target_type="brand", target_id="brand-test", field_path="synopsis",
            proposed_value="x", submitted_by="jane",
        )
        tps.confirm(submission["submission_id"], reviewed_by="todd")
        with pytest.raises(tps.AlreadyReviewedError):
            tps.reject(submission["submission_id"], reviewed_by="todd")
