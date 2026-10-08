"""Tests for scripts/hunter_drive_inbox_sync.py."""
from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import hunter_drive_inbox_sync as dis  # noqa: E402


def _env(tmp_path, monkeypatch):
    drive = tmp_path / "drive_inbox"
    drive.mkdir()
    pending = tmp_path / "pending"
    pending.mkdir()
    inbox = tmp_path / "inbox"
    monkeypatch.setattr(dis, "DRIVE_INBOX", drive)
    monkeypatch.setattr(dis, "PENDING", pending)
    monkeypatch.setattr(dis, "INBOX", inbox)
    monkeypatch.setattr(dis, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(dis, "RECEIPTS_PATH", tmp_path / "receipts.jsonl")
    return {"drive": drive, "pending": pending, "inbox": inbox}


def _pending_job(target_key="company:brand-example", assignment_id="priority-example"):
    return {
        "assignment_id": assignment_id,
        "directive": {"packet_requirements": {"target_keys": [target_key]}},
    }


def test_copies_a_matching_packet_and_tags_it_with_targets(tmp_path, monkeypatch):
    env = _env(tmp_path, monkeypatch)
    (env["pending"] / "a.json").write_text(json.dumps(_pending_job()), encoding="utf-8")
    packet = {"schema": dis.PACKET_SCHEMA, "targets": [{"target_key": "company:brand-example"}]}
    (env["drive"] / "hunter-packet-example.json").write_text(json.dumps(packet), encoding="utf-8")

    out = dis.run()
    assert len(out["copied"]) == 1
    assert out["copied"][0]["targets"] == ["company:brand-example"]
    assert (env["inbox"] / "hunter-packet-example.json").exists()


def test_appends_a_receipt_row_per_copied_file(tmp_path, monkeypatch):
    env = _env(tmp_path, monkeypatch)
    (env["pending"] / "a.json").write_text(json.dumps(_pending_job()), encoding="utf-8")
    packet = {"schema": dis.PACKET_SCHEMA, "targets": [{"target_key": "company:brand-example"}]}
    (env["drive"] / "hunter-packet-example.json").write_text(json.dumps(packet), encoding="utf-8")

    dis.run()
    rows = [json.loads(line) for line in dis.RECEIPTS_PATH.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["targets"] == ["company:brand-example"]
    assert rows[0]["file"] == "hunter-packet-example.json"
    assert "observed_at" in rows[0]


def test_unmatched_packet_is_not_copied_and_writes_no_receipt(tmp_path, monkeypatch):
    env = _env(tmp_path, monkeypatch)
    (env["pending"] / "a.json").write_text(json.dumps(_pending_job("company:brand-other")), encoding="utf-8")
    packet = {"schema": dis.PACKET_SCHEMA, "targets": [{"target_key": "company:brand-example"}]}
    (env["drive"] / "hunter-packet-example.json").write_text(json.dumps(packet), encoding="utf-8")

    out = dis.run()
    assert out["copied"] == []
    assert len(out["unmatched"]) == 1
    assert not dis.RECEIPTS_PATH.exists()


def test_idempotent_rerun_does_not_duplicate_the_receipt(tmp_path, monkeypatch):
    env = _env(tmp_path, monkeypatch)
    (env["pending"] / "a.json").write_text(json.dumps(_pending_job()), encoding="utf-8")
    packet = {"schema": dis.PACKET_SCHEMA, "targets": [{"target_key": "company:brand-example"}]}
    (env["drive"] / "hunter-packet-example.json").write_text(json.dumps(packet), encoding="utf-8")

    dis.run()
    second = dis.run()
    assert second["copied"] == []
    rows = dis.RECEIPTS_PATH.read_text(encoding="utf-8").splitlines()
    assert len(rows) == 1
