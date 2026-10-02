#!/usr/bin/env python3
"""Regression coverage for LinkedIn export longitudinal RI output."""
from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import linkedin_ingest as li  # noqa: E402


def _write_fixture_zip(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            "Connections.csv",
            "LinkedIn export\nGenerated for test\n"
            "First Name,Last Name,URL,Email Address,Company,Position,Connected On\n"
            "Jane,Buyer,https://www.linkedin.com/in/jane-buyer,,"
            "Restaurant AI Co,VP Operations,20 May 2026\n"
            "Sam,Talent,https://www.linkedin.com/in/sam-talent,,"
            "Executive Search Partners,Recruiter,21 May 2026\n",
        )
        zf.writestr("messages.csv", "FROM,TO,DATE,SUBJECT,CONTENT\na,b,2026-05-20,,hello\n")


def test_linkedin_ingest_outputs_longitudinal_delta_intelligence(monkeypatch, tmp_path):
    baseline_path = tmp_path / "baseline_index.json"
    snapshot_dir = tmp_path / "_snapshots"
    deltas_dir = tmp_path / "deltas"
    cache_path = tmp_path / ".cache" / "linkedin_ingest_latest.json"
    zip_path = tmp_path / "linkedin_export.zip"
    _write_fixture_zip(zip_path)

    baseline = [
        {
            "id": "jane-buyer",
            "name": "Jane Buyer",
            "current_company": "Legacy Vendor",
            "current_role": "Manager",
            "linkedin_url": "https://www.linkedin.com/in/jane-buyer",
            "sources": ["linkedin_export_2026-05-01"],
            "signal_class": "LKI",
            "last_touch": None,
            "tags": [],
            "notes": "",
        },
        {
            "id": "lost-contact",
            "name": "Lost Contact",
            "current_company": "Old Restaurant Group",
            "current_role": "Director",
            "linkedin_url": "https://www.linkedin.com/in/lost-contact",
            "sources": ["linkedin_export_2026-05-01"],
            "signal_class": "LKI",
            "last_touch": "2025-01-15",
            "tags": [],
            "notes": "",
        },
    ]
    baseline_path.write_text(json.dumps(baseline, indent=2) + "\n", encoding="utf-8")

    monkeypatch.setattr(li.core, "BASELINE_PATH", baseline_path)
    monkeypatch.setattr(li.core, "SNAPSHOTS_DIR", snapshot_dir)
    monkeypatch.setattr(li.core, "load_baseline", lambda path=baseline_path: json.loads(baseline_path.read_text(encoding="utf-8")))
    monkeypatch.setattr(li, "DELTAS_DIR", deltas_dir)
    monkeypatch.setattr(li, "LINKEDIN_CACHE_PATH", cache_path)

    result = li.ingest(zip_path, ingest_date="2026-05-28", dry_run=True)

    h = result["headline_counts"]
    assert h["baseline_before"] == 2
    assert h["baseline_after"] == 3
    assert h["new_connections"] == 1
    assert h["disconnections"] == 1
    assert h["company_changes"] == 1
    assert h["role_changes"] == 1

    intel = result["delta_intelligence"]
    metrics = intel["relationship_delta_metrics"]
    assert metrics["prior_baseline_record_count"] == 2
    assert metrics["current_baseline_record_count"] == 3
    assert metrics["net_new_relationships"] == 1
    assert metrics["lost_relationships"] == 1
    assert metrics["recruiter_additions"] == 1
    assert metrics["restaurant_tech_adjacency_expansion"] == 1

    professional = intel["professional_change_detection"]
    assert professional["title_changes"] == 1
    assert professional["company_changes"] == 1
    assert professional["promotions"] == 1

    graph = intel["graph_mutations"]
    assert graph["persistent_graph_mutated"] is False
    assert graph["baseline_entries_added"] == 1
    assert graph["baseline_entries_updated"] >= 3
    assert graph["who_matters_now_mutations"] >= 1

    summary = result["summary_markdown"]
    for heading in (
        "## What Should Todd Do Now?",
        "## Network Segment Comparison",
        "## Baseline Comparison",
        "## Professional Change Detection",
        "## Strategic Relationship Changes",
        "## Intelligence Trust Statistics",
        "## Graph Mutations",
        "## Daily Brief Mutations",
        "## Persistence Verification",
    ):
        assert heading in summary, f"Missing section: {heading!r}"
    assert "What changed" not in summary
    assert "File processed successfully" not in summary
    # Trust statistics present
    assert intel.get("trust_statistics") is not None
    trust = intel["trust_statistics"]
    assert trust["confidence_score"] > 0
    assert "Connections.csv" in trust["sources_present"]
    # Segment deltas present
    assert intel.get("segment_deltas") is not None
    segs = {s["segment"]: s for s in intel["segment_deltas"]}
    assert "Total Connections" in segs
    assert segs["Total Connections"]["delta"] == 1
    assert segs["Recruiters"]["delta"] == 1
    # Career activation present and sorted by priority
    activation = intel.get("career_activation") or []
    assert len(activation) >= 1
    assert activation[0]["priority_score"] >= activation[-1]["priority_score"]
    assert "recommended_action" in activation[0]
    assert "relationship_strength" in activation[0]
    assert "change_significance" in activation[0]


def test_linkedin_ingest_persists_latest_cache_for_daily_brief(monkeypatch, tmp_path):
    baseline_path = tmp_path / "baseline_index.json"
    snapshot_dir = tmp_path / "_snapshots"
    deltas_dir = tmp_path / "deltas"
    briefs_dir = tmp_path / "briefs"
    cache_path = tmp_path / ".cache" / "linkedin_ingest_latest.json"
    zip_path = tmp_path / "linkedin_export.zip"
    _write_fixture_zip(zip_path)
    baseline_path.write_text(json.dumps([], indent=2) + "\n", encoding="utf-8")

    monkeypatch.setattr(li.core, "BASELINE_PATH", baseline_path)
    monkeypatch.setattr(li.core, "SNAPSHOTS_DIR", snapshot_dir)
    monkeypatch.setattr(li.core, "load_baseline", lambda path=baseline_path: json.loads(baseline_path.read_text(encoding="utf-8")))
    monkeypatch.setattr(li, "DELTAS_DIR", deltas_dir)
    monkeypatch.setattr(li, "LINKEDIN_CACHE_PATH", cache_path)
    # dry_run=False below also writes the 3 LinkedIn brief markdown files and
    # emails them (via send_brief_email.send_linkedin_reports, which falls
    # back to real SMTP credentials in the morning-pipeline LaunchAgent plist
    # when no env vars are set). Isolate both: redirect BRIEFS_DIR so the
    # files don't land in the real system/briefs/, and stub the email call so
    # a test run can never trigger a real send regardless of BRIEFS_DIR.
    monkeypatch.setattr(li.core, "BRIEFS_DIR", briefs_dir)
    monkeypatch.setattr(li, "_email_linkedin_briefs", lambda d: {"sent": False, "status": "test_stub"})

    result = li.ingest(zip_path, ingest_date="2026-05-28", dry_run=False)

    assert cache_path.exists()
    payload = json.loads(cache_path.read_text(encoding="utf-8"))
    assert payload["delta_intelligence"]["daily_brief_mutations"]["items_available_for_brief"] >= 1
    assert any(p.endswith("linkedin_ingest_latest.json") for p in result["files_written"])
    assert "baseline_json_written_delta_markdown_written_cache_written" in result["summary_markdown"]
    assert result["email_result"]["status"] == "test_stub"
    # The 3 brief files must land in the redirected briefs_dir (proving the
    # real system/briefs/ was never touched), not just get skipped entirely.
    assert (briefs_dir / "2026-05-28-linkedin-intelligence-report.md").exists()
    rationalization_path = briefs_dir / "2026-05-28-linkedin-contact-rationalization.md"
    mutation_receipt_path = briefs_dir / "2026-05-28-linkedin-mutation-package.md"
    assert rationalization_path.exists()
    assert mutation_receipt_path.exists()

    rationalization = rationalization_path.read_text(encoding="utf-8")
    receipt = mutation_receipt_path.read_text(encoding="utf-8")
    assert "New Connections Added to Baseline" in rationalization
    assert "contacts retained" in rationalization.lower()
    assert "Pending Baseline Review" not in rationalization
    assert "Awaiting approval" not in rationalization
    assert "RB LinkedIn Mutation Receipt" in receipt
    assert "Applied Fact Mutations" in receipt
    assert "New Contact Records Applied From Export Facts" in receipt
    assert "Pending Todd Approval" not in receipt
    assert "Mutations Ready to Apply" not in receipt
    assert "No inferred information was mutated" in receipt
    assert "no contacts were deleted" in receipt


def test_strategic_account_match_matches_tracked_accounts():
    assert li._strategic_account_match("Global Payments") == "Global Payments / Worldpay"
    assert li._strategic_account_match("Worldpay, a Global Payments brand") == "Global Payments / Worldpay"
    assert li._strategic_account_match("Foods Connected") == "Foods Connected"
    assert li._strategic_account_match("Toast, Inc.") == "Toast"
    assert li._strategic_account_match("Restaurant365") == "Restaurant365"
    assert li._strategic_account_match("Restaurant 365") == "Restaurant365"
    assert li._strategic_account_match("DoorDash") == "DoorDash"


def test_strategic_account_match_avoids_false_positives():
    assert li._strategic_account_match(None) is None
    assert li._strategic_account_match("") is None
    assert li._strategic_account_match("Acme Software Inc") is None
    assert li._strategic_account_match("Qu Bistro Holdings") is None
    assert li._strategic_account_match("GK Software") is None


def _write_strategic_fixture_zip(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            "Connections.csv",
            "LinkedIn export\nGenerated for test\n"
            "First Name,Last Name,URL,Email Address,Company,Position,Connected On\n"
            "Pat,Newhire,https://www.linkedin.com/in/pat-newhire,,"
            "DoorDash,Director of Partnerships,20 May 2026\n",
        )
        zf.writestr("messages.csv", "FROM,TO,DATE,SUBJECT,CONTENT\na,b,2026-05-20,,hello\n")


def test_linkedin_ingest_flags_strategic_account_map_signals(monkeypatch, tmp_path):
    baseline_path = tmp_path / "baseline_index.json"
    snapshot_dir = tmp_path / "_snapshots"
    deltas_dir = tmp_path / "deltas"
    cache_path = tmp_path / ".cache" / "linkedin_ingest_latest.json"
    zip_path = tmp_path / "linkedin_export.zip"
    _write_strategic_fixture_zip(zip_path)

    baseline = [
        {
            "id": "existing-contact",
            "name": "Existing Contact",
            "current_company": "Old Co",
            "current_role": "Manager",
            "linkedin_url": "https://www.linkedin.com/in/existing-contact",
            "sources": ["linkedin_export_2026-05-01"],
            "signal_class": "LKI",
            "last_touch": None,
            "tags": [],
            "notes": "",
        },
    ]
    baseline_path.write_text(json.dumps(baseline, indent=2) + "\n", encoding="utf-8")

    monkeypatch.setattr(li.core, "BASELINE_PATH", baseline_path)
    monkeypatch.setattr(li.core, "SNAPSHOTS_DIR", snapshot_dir)
    monkeypatch.setattr(li.core, "load_baseline", lambda path=baseline_path: json.loads(baseline_path.read_text(encoding="utf-8")))
    monkeypatch.setattr(li, "DELTAS_DIR", deltas_dir)
    monkeypatch.setattr(li, "LINKEDIN_CACHE_PATH", cache_path)

    result = li.ingest(zip_path, ingest_date="2026-05-28", dry_run=True)

    intel = result["delta_intelligence"]
    signals = intel["strategic_relationship_changes"]["account_map_signals"]
    assert len(signals) == 1
    signal = signals[0]
    assert signal["person"] == "Pat Newhire"
    assert signal["account"] == "DoorDash"
    assert signal["company"] == "DoorDash"
    assert signal["movement_type"] == "new_connection"

    summary = result["summary_markdown"]
    assert "### Account Map Signals" in summary
    assert "Pat Newhire" in summary
    assert "DoorDash" in summary

    actions = {a["person"]: a for a in result["recommended_actions"]}
    assert actions["Pat Newhire"]["priority"] == "high"
    assert "DoorDash account map" in actions["Pat Newhire"]["action"]


def test_ingest_records_company_and_title_history_on_change(monkeypatch, tmp_path):
    baseline_path = tmp_path / "baseline_index.json"
    snapshot_dir = tmp_path / "_snapshots"
    deltas_dir = tmp_path / "deltas"
    cache_path = tmp_path / ".cache" / "linkedin_ingest_latest.json"
    zip_path = tmp_path / "linkedin_export.zip"
    _write_fixture_zip(zip_path)

    baseline = [
        {
            "id": "jane-buyer",
            "name": "Jane Buyer",
            "current_company": "Legacy Vendor",
            "current_role": "Manager",
            "linkedin_url": "https://www.linkedin.com/in/jane-buyer",
            "sources": ["linkedin_export_2026-05-01"],
            "signal_class": "LKI",
            "last_touch": None,
            "tags": [],
            "notes": "",
        },
    ]
    baseline_path.write_text(json.dumps(baseline, indent=2) + "\n", encoding="utf-8")

    monkeypatch.setattr(li.core, "BASELINE_PATH", baseline_path)
    monkeypatch.setattr(li.core, "SNAPSHOTS_DIR", snapshot_dir)
    monkeypatch.setattr(li.core, "load_baseline", lambda path=baseline_path: json.loads(baseline_path.read_text(encoding="utf-8")))
    monkeypatch.setattr(li, "DELTAS_DIR", deltas_dir)
    monkeypatch.setattr(li, "LINKEDIN_CACHE_PATH", cache_path)

    result = li.ingest(zip_path, ingest_date="2026-05-28", dry_run=True)

    # Jane Buyer moved from "Legacy Vendor"/"Manager" to "Restaurant AI Co"/"VP Operations"
    assert len(result["lki_moves"]) == 1
    snapshot = result["lki_moves"][0]["entry_snapshot"]
    assert snapshot["company_history"] == [{"company": "Legacy Vendor", "changed_on": "2026-05-28"}]
    assert snapshot["title_history"] == [{"role": "Manager", "changed_on": "2026-05-28"}]


def test_ingest_from_csv_text_matches_existing_entry_without_crashing(monkeypatch, tmp_path):
    baseline_path = tmp_path / "baseline_index.json"
    snapshot_dir = tmp_path / "_snapshots"
    deltas_dir = tmp_path / "deltas"
    cache_path = tmp_path / ".cache" / "linkedin_ingest_latest.json"

    baseline = [
        {
            "id": "jane-buyer",
            "name": "Jane Buyer",
            "current_company": "Legacy Vendor",
            "current_role": "Manager",
            "linkedin_url": "https://www.linkedin.com/in/jane-buyer",
            "sources": ["linkedin_export_2026-05-01"],
            "signal_class": "LKI",
            "last_touch": None,
            "tags": [],
            "notes": "",
        },
    ]
    baseline_path.write_text(json.dumps(baseline, indent=2) + "\n", encoding="utf-8")

    monkeypatch.setattr(li.core, "BASELINE_PATH", baseline_path)
    monkeypatch.setattr(li.core, "SNAPSHOTS_DIR", snapshot_dir)
    monkeypatch.setattr(li.core, "load_baseline", lambda path=baseline_path: json.loads(baseline_path.read_text(encoding="utf-8")))
    monkeypatch.setattr(li, "DELTAS_DIR", deltas_dir)
    monkeypatch.setattr(li, "LINKEDIN_CACHE_PATH", cache_path)

    csv_text = (
        "First Name,Last Name,URL,Email Address,Company,Position,Connected On\n"
        "Jane,Buyer,https://www.linkedin.com/in/jane-buyer,,"
        "Restaurant AI Co,VP Operations,20 May 2026\n"
    )

    result = li.ingest_from_csv_text(csv_text, ingest_date="2026-05-28", dry_run=True)

    assert result["headline_counts"]["matched_existing"] == 1
    assert result["headline_counts"]["company_changes"] == 1
    assert result["headline_counts"]["role_changes"] == 1
    company_change = result["delta"]["company_changes"][0]
    assert company_change["entry"]["company_history"] == [{"company": "Legacy Vendor", "changed_on": "2026-05-28"}]


def _baseline_entry(**overrides) -> dict:
    entry = {
        "id": "jane-buyer",
        "name": "Jane Buyer",
        "current_company": "Restaurant AI Co",
        "current_role": "VP Operations",
        "linkedin_url": "https://www.linkedin.com/in/jane-buyer",
        "sources": ["linkedin_export_2026-05-01"],
        "signal_class": "LKI",
        "last_touch": None,
        "tags": [],
        "notes": "",
        "email": "jane.buyer@oldcompany.com",
    }
    entry.update(overrides)
    return entry


def _patch_linkedin_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(li.core, "SNAPSHOTS_DIR", tmp_path / "_snapshots")
    monkeypatch.setattr(li, "DELTAS_DIR", tmp_path / "deltas")
    monkeypatch.setattr(li, "LINKEDIN_CACHE_PATH", tmp_path / ".cache" / "linkedin_ingest_latest.json")


def test_ingest_overwrites_email_and_records_history_on_change(monkeypatch, tmp_path):
    """RB-2026-09-19: 'overwrites of job title, contact information and
    company are permitted -- there should be delta information displayed
    and job/title history should be kept' (Todd, working-tree cleanup
    discussion) extends the exact company_history/title_history policy
    (RB-DEFECT-041 Enhancement #4) to email -- previously email only ever
    filled when blank, never overwrote, never tracked history, and never
    surfaced a delta the way company/role changes already did."""
    _patch_linkedin_paths(monkeypatch, tmp_path)
    baseline_path = tmp_path / "baseline_index.json"
    baseline_path.write_text(json.dumps([_baseline_entry()], indent=2) + "\n", encoding="utf-8")
    monkeypatch.setattr(li.core, "BASELINE_PATH", baseline_path)
    monkeypatch.setattr(li.core, "load_baseline", lambda path=baseline_path: json.loads(baseline_path.read_text(encoding="utf-8")))

    csv_text = (
        "First Name,Last Name,URL,Email Address,Company,Position,Connected On\n"
        "Jane,Buyer,https://www.linkedin.com/in/jane-buyer,jane.buyer@newcompany.com,"
        "Restaurant AI Co,VP Operations,20 May 2026\n"
    )
    result = li.ingest_from_csv_text(csv_text, ingest_date="2026-05-28", dry_run=True)

    assert result["headline_counts"]["email_changes"] == 1
    email_change = result["delta"]["email_changes"][0]
    assert email_change["old"] == "jane.buyer@oldcompany.com"
    assert email_change["new"] == "jane.buyer@newcompany.com"
    entry = email_change["entry"]
    assert entry["email"] == "jane.buyer@newcompany.com"
    assert entry["email_history"] == [{"email": "jane.buyer@oldcompany.com", "changed_on": "2026-05-28"}]
    assert result["delta"]["conflicts"] == []


def test_ingest_email_conflict_when_operator_confirmed(monkeypatch, tmp_path):
    """The one exception, mirroring company/role: an operator-confirmed
    contact's email must not be silently overwritten -- routed to
    conflicts instead, same as a confirmed company/role would be."""
    _patch_linkedin_paths(monkeypatch, tmp_path)
    baseline_path = tmp_path / "baseline_index.json"
    baseline_path.write_text(
        json.dumps([_baseline_entry(notes="Confirmed by Todd 2026-05-01.")], indent=2) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(li.core, "BASELINE_PATH", baseline_path)
    monkeypatch.setattr(li.core, "load_baseline", lambda path=baseline_path: json.loads(baseline_path.read_text(encoding="utf-8")))

    csv_text = (
        "First Name,Last Name,URL,Email Address,Company,Position,Connected On\n"
        "Jane,Buyer,https://www.linkedin.com/in/jane-buyer,jane.buyer@newcompany.com,"
        "Restaurant AI Co,VP Operations,20 May 2026\n"
    )
    result = li.ingest_from_csv_text(csv_text, ingest_date="2026-05-28", dry_run=True)

    assert result["headline_counts"]["email_changes"] == 0
    assert len(result["delta"]["conflicts"]) == 1
    conflict = result["delta"]["conflicts"][0]
    assert conflict["field"] == "email"
    assert conflict["canonical"] == "jane.buyer@oldcompany.com"
    assert conflict["linkedin"] == "jane.buyer@newcompany.com"


def test_ingest_first_time_email_fill_matches_company_role_convention(monkeypatch, tmp_path):
    """A contact with no email on file yet still just gets filled in --
    no conflict (nothing to protect yet) -- and records a None-to-value
    history entry, exactly the same convention company_history/
    title_history already use for a first-time fill (their own
    `company_changed = bool(company and _norm(company) != _norm(old_company))`
    is equally true when old_company is None, verified directly against
    the real function before writing this assertion)."""
    _patch_linkedin_paths(monkeypatch, tmp_path)
    baseline_path = tmp_path / "baseline_index.json"
    baseline_path.write_text(
        json.dumps([_baseline_entry(email=None)], indent=2) + "\n", encoding="utf-8",
    )
    monkeypatch.setattr(li.core, "BASELINE_PATH", baseline_path)
    monkeypatch.setattr(li.core, "load_baseline", lambda path=baseline_path: json.loads(baseline_path.read_text(encoding="utf-8")))

    csv_text = (
        "First Name,Last Name,URL,Email Address,Company,Position,Connected On\n"
        "Jane,Buyer,https://www.linkedin.com/in/jane-buyer,jane.buyer@newcompany.com,"
        "Restaurant AI Co,VP Operations,20 May 2026\n"
    )
    result = li.ingest_from_csv_text(csv_text, ingest_date="2026-05-28", dry_run=True)

    assert result["headline_counts"]["email_changes"] == 1
    entry = result["delta"]["email_changes"][0]["entry"]
    assert entry["email"] == "jane.buyer@newcompany.com"
    assert entry.get("email_history") == [{"email": None, "changed_on": "2026-05-28"}]
    assert result["delta"]["conflicts"] == []
