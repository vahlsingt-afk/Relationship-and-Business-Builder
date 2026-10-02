import json
from pathlib import Path

from system.scripts import outlook_gui_capture as capture


def _payload():
    return {
        "captured_at": "2026-09-18T00:00:00-05:00",
        "account_id": "global-payments",
        "inbox": [{
            "counterpart_name": "Example Sender",
            "counterpart_email": "sender@example.com",
            "subject": "Example signal",
            "received_at": "2026-09-17T15:00:00-05:00",
            "unread": False,
        }],
        "sent": [],
        "calendar": [],
    }


def test_stage_scan_and_ingest_are_idempotent(tmp_path, monkeypatch):
    capture_dir = tmp_path / "captures"
    manifest = tmp_path / "manifest.json"
    canonical = tmp_path / "email.global-payments.json"
    monkeypatch.setattr(capture.core, "email_path_for", lambda _account: canonical)

    staged = capture.stage_capture(_payload(), capture_dir=capture_dir)
    assert staged["counts"] == {"inbox": 1, "sent": 0, "calendar": 0}
    assert Path(staged["file"]).exists()
    assert len(capture.scan_staged(capture_dir=capture_dir, manifest_path=manifest)["pending"]) == 1

    first = capture.ingest_staged(
        confirm=True, capture_dir=capture_dir, manifest_path=manifest
    )
    assert first["ingested"] == 1
    assert len(json.loads(canonical.read_text())["threads"]) == 1

    second = capture.ingest_staged(
        confirm=True, capture_dir=capture_dir, manifest_path=manifest
    )
    assert second["ingested"] == 0


def test_capture_rejects_wrong_account_and_empty_payload():
    bad_account = _payload()
    bad_account["account_id"] = "personal"
    try:
        capture._validate_capture(bad_account)
    except ValueError as exc:
        assert "account_id" in str(exc)
    else:
        raise AssertionError("wrong account was accepted")

    try:
        capture._validate_capture({"account_id": "global-payments"})
    except ValueError as exc:
        assert "contains no" in str(exc)
    else:
        raise AssertionError("empty capture was accepted")
