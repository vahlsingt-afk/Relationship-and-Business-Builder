from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import rb_core as core  # noqa: E402
import identity_match_review as imr  # noqa: E402


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _wire_tmp_system(tmp_path: Path, monkeypatch) -> Path:
    system_dir = tmp_path / "system"
    inbox_dir = system_dir / "inbox"
    inbox_dir.mkdir(parents=True)
    monkeypatch.setattr(core, "SYSTEM_DIR", system_dir)
    monkeypatch.setattr(core, "INBOX_DIR", inbox_dir)
    monkeypatch.setattr(core, "BASELINE_PATH", system_dir / "baseline_index.json")
    monkeypatch.setattr(core, "INBOX_ACCOUNTS_PATH", inbox_dir / "accounts.yaml")
    monkeypatch.setattr(imr, "CACHE_PATH", system_dir / ".cache" / "identity_match_candidates.json")
    return system_dir


def _baseline():
    return [
        {"id": "david-drinan", "name": "David Drinan", "current_company": "Blackthorn Strategic Advisors",
         "email": None, "notes": ""},
        {"id": "someone-else", "name": "Someone Else", "current_company": "Acme",
         "email": "someone@already-known.com", "notes": ""},
    ]


def _thread(thread_id, subject, sender_name, sender_email):
    return {
        "thread_id": thread_id,
        "subject": subject,
        "last_message_at": "Tue, 30 Jun 2026 17:34:22 -0400",
        "last_message_from": {"name": sender_name, "email": sender_email},
        "labels": ["INBOX"],
    }


def test_scan_matches_exact_name_with_no_baseline_email(tmp_path, monkeypatch):
    system_dir = _wire_tmp_system(tmp_path, monkeypatch)
    _write_json(system_dir / "baseline_index.json", _baseline())
    _write_json(system_dir / "inbox" / "email.bridgepoint.json", {"threads": [
        _thread("t1", "Introduction", "David Drinan", "david@blackthornstrategicadvisors.com"),
    ]})
    _write_json(system_dir / "inbox" / "email.personal.json", {"threads": []})

    result = imr.scan()
    assert result == {"total_candidates": 1, "new_this_run": 1, "pending": 1}

    pending = imr.pending_candidates()
    assert len(pending) == 1
    assert pending[0]["baseline_id"] == "david-drinan"
    assert pending[0]["sender_email"] == "david@blackthornstrategicadvisors.com"


def test_scan_skips_sender_already_matched_by_email(tmp_path, monkeypatch):
    system_dir = _wire_tmp_system(tmp_path, monkeypatch)
    _write_json(system_dir / "baseline_index.json", _baseline())
    _write_json(system_dir / "inbox" / "email.bridgepoint.json", {"threads": [
        _thread("t1", "Hi", "Someone Else", "someone@already-known.com"),
    ]})
    _write_json(system_dir / "inbox" / "email.personal.json", {"threads": []})

    result = imr.scan()
    assert result == {"total_candidates": 0, "new_this_run": 0, "pending": 0}


def test_scan_skips_name_match_when_baseline_already_has_different_email(tmp_path, monkeypatch):
    system_dir = _wire_tmp_system(tmp_path, monkeypatch)
    baseline = _baseline()
    baseline[0]["email"] = "david@some-other-domain.com"
    _write_json(system_dir / "baseline_index.json", baseline)
    _write_json(system_dir / "inbox" / "email.bridgepoint.json", {"threads": [
        _thread("t1", "Introduction", "David Drinan", "david@blackthornstrategicadvisors.com"),
    ]})
    _write_json(system_dir / "inbox" / "email.personal.json", {"threads": []})

    result = imr.scan()
    assert result == {"total_candidates": 0, "new_this_run": 0, "pending": 0}


def test_scan_does_not_fuzzy_match_partial_names(tmp_path, monkeypatch):
    system_dir = _wire_tmp_system(tmp_path, monkeypatch)
    _write_json(system_dir / "baseline_index.json", _baseline())
    _write_json(system_dir / "inbox" / "email.bridgepoint.json", {"threads": [
        _thread("t1", "Hi", "Dave Drinan", "dave@somewhereelse.com"),
        _thread("t2", "Hi", "D. Drinan", "d.drinan@somewhereelse.com"),
    ]})
    _write_json(system_dir / "inbox" / "email.personal.json", {"threads": []})

    result = imr.scan()
    assert result == {"total_candidates": 0, "new_this_run": 0, "pending": 0}


def test_confirm_writes_email_and_note_onto_baseline_and_resolves_candidate(tmp_path, monkeypatch):
    system_dir = _wire_tmp_system(tmp_path, monkeypatch)
    _write_json(system_dir / "baseline_index.json", _baseline())
    _write_json(system_dir / "inbox" / "email.bridgepoint.json", {"threads": [
        _thread("t1", "Introduction", "David Drinan", "david@blackthornstrategicadvisors.com"),
    ]})
    _write_json(system_dir / "inbox" / "email.personal.json", {"threads": []})
    imr.scan()

    result = imr.confirm("david-drinan::david@blackthornstrategicadvisors.com")
    assert result["confirmed"] is True

    updated_baseline = json.loads((system_dir / "baseline_index.json").read_text(encoding="utf-8"))
    drinan = next(e for e in updated_baseline if e["id"] == "david-drinan")
    assert drinan["email"] == "david@blackthornstrategicadvisors.com"
    assert "Email identity confirmed" in drinan["notes"]

    assert imr.pending_candidates() == []

    # Confirming again is rejected — decision already resolved.
    second = imr.confirm("david-drinan::david@blackthornstrategicadvisors.com")
    assert "error" in second


def test_confirm_without_email_notes_link_but_leaves_email_blank(tmp_path, monkeypatch):
    system_dir = _wire_tmp_system(tmp_path, monkeypatch)
    _write_json(system_dir / "baseline_index.json", _baseline())
    _write_json(system_dir / "inbox" / "email.bridgepoint.json", {"threads": [
        _thread("t1", "I want to connect", "David Drinan", "invitations@linkedin.com"),
    ]})
    _write_json(system_dir / "inbox" / "email.personal.json", {"threads": []})
    imr.scan()

    result = imr.confirm_without_email(
        "david-drinan::invitations@linkedin.com",
        reason="LinkedIn invite-notification relay, not his address",
    )
    assert result["confirmed_no_email"] is True
    assert result["email_declined"] == "invitations@linkedin.com"

    updated_baseline = json.loads((system_dir / "baseline_index.json").read_text(encoding="utf-8"))
    drinan = next(e for e in updated_baseline if e["id"] == "david-drinan")
    assert drinan["email"] is None
    assert "Identity confirmed" in drinan["notes"]
    assert "not added as their email" in drinan["notes"]
    assert "LinkedIn invite-notification relay" in drinan["notes"]

    assert imr.pending_candidates() == []

    # Already resolved — can't confirm-without-email a second time.
    second = imr.confirm_without_email("david-drinan::invitations@linkedin.com")
    assert "error" in second


def test_reject_resolves_candidate_without_touching_baseline(tmp_path, monkeypatch):
    system_dir = _wire_tmp_system(tmp_path, monkeypatch)
    _write_json(system_dir / "baseline_index.json", _baseline())
    _write_json(system_dir / "inbox" / "email.bridgepoint.json", {"threads": [
        _thread("t1", "Introduction", "David Drinan", "david@blackthornstrategicadvisors.com"),
    ]})
    _write_json(system_dir / "inbox" / "email.personal.json", {"threads": []})
    imr.scan()

    result = imr.reject("david-drinan::david@blackthornstrategicadvisors.com")
    assert result["rejected"] is True

    updated_baseline = json.loads((system_dir / "baseline_index.json").read_text(encoding="utf-8"))
    drinan = next(e for e in updated_baseline if e["id"] == "david-drinan")
    assert drinan["email"] is None

    assert imr.pending_candidates() == []


def test_rescan_never_reopens_a_resolved_candidate(tmp_path, monkeypatch):
    system_dir = _wire_tmp_system(tmp_path, monkeypatch)
    _write_json(system_dir / "baseline_index.json", _baseline())
    _write_json(system_dir / "inbox" / "email.bridgepoint.json", {"threads": [
        _thread("t1", "Introduction", "David Drinan", "david@blackthornstrategicadvisors.com"),
    ]})
    _write_json(system_dir / "inbox" / "email.personal.json", {"threads": []})
    imr.scan()
    imr.reject("david-drinan::david@blackthornstrategicadvisors.com")

    result = imr.scan()
    assert result == {"total_candidates": 1, "new_this_run": 0, "pending": 0}
    assert imr.pending_candidates() == []
