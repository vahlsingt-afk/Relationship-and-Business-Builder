"""Tests for scripts/hunter_office_manager.py."""
from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import hunter_drive_inbox_sync as dis  # noqa: E402
import hunter_office_manager as om  # noqa: E402


def _env(tmp_path, monkeypatch, *, sms_enabled=False):
    receipts = tmp_path / "receipts.jsonl"
    monkeypatch.setattr(dis, "RECEIPTS_PATH", receipts)
    monkeypatch.setattr(om, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(om, "LOG_PATH", tmp_path / "log.jsonl")
    monkeypatch.setattr(om, "_display_names", lambda keys: {k: k.split(":", 1)[-1].replace("-", " ").title() for k in keys})
    settings = {"daily_briefing": {"hunter_office_manager": {"sms": {
        "enabled": sms_enabled, "gateway_address": "9205175090@vtext.com",
    }}}}
    monkeypatch.setattr(om.core, "load_settings", lambda: settings)
    return receipts


def _write_receipt(path, *, targets, file="hunter-packet-x.json"):
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"observed_at": "2026-10-08T12:00:00+00:00", "file": file,
                              "destination": f"/inbox/{file}", "sha256": "abc", "targets": targets}) + "\n")


def test_dry_run_reports_without_writing_log_or_advancing_cursor(tmp_path, monkeypatch):
    receipts = _env(tmp_path, monkeypatch)
    _write_receipt(receipts, targets=["company:brand-mcdonalds"])

    out = om.run(confirm=False)
    assert out["dry_run"] is True
    assert len(out["completions"]) == 1
    assert out["completions"][0]["company"] == "Brand Mcdonalds"
    assert not om.LOG_PATH.exists()
    assert om._load_state().get("processed_receipts", 0) == 0


def test_confirm_writes_one_log_row_per_completed_company(tmp_path, monkeypatch):
    receipts = _env(tmp_path, monkeypatch)
    _write_receipt(receipts, targets=["company:brand-mcdonalds", "company:brand-subway"])

    out = om.run(confirm=True)
    assert len(out["completions"]) == 2
    rows = [json.loads(line) for line in om.LOG_PATH.read_text(encoding="utf-8").splitlines()]
    assert {r["company"] for r in rows} == {"Brand Mcdonalds", "Brand Subway"}
    assert om._load_state()["processed_receipts"] == 1


def test_a_later_run_only_processes_receipts_added_since_the_cursor(tmp_path, monkeypatch):
    receipts = _env(tmp_path, monkeypatch)
    _write_receipt(receipts, targets=["company:brand-mcdonalds"])
    om.run(confirm=True)

    _write_receipt(receipts, targets=["company:brand-wendys"])
    out = om.run(confirm=True)
    assert len(out["completions"]) == 1
    assert out["completions"][0]["company"] == "Brand Wendys"
    rows = [json.loads(line) for line in om.LOG_PATH.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 2  # one from each run, not reprocessed


def test_sms_not_attempted_when_disabled_in_settings(tmp_path, monkeypatch):
    receipts = _env(tmp_path, monkeypatch, sms_enabled=False)
    _write_receipt(receipts, targets=["company:brand-mcdonalds"])

    om.run(confirm=True)
    row = json.loads(om.LOG_PATH.read_text(encoding="utf-8").splitlines()[0])
    assert row["sms"]["attempted"] is False


def test_sms_attempted_and_routed_through_the_configured_gateway_when_enabled(tmp_path, monkeypatch):
    receipts = _env(tmp_path, monkeypatch, sms_enabled=True)
    _write_receipt(receipts, targets=["company:brand-mcdonalds"])
    sent = {}

    def fake_send_text(message):
        sent["message"] = message
        return {"sent": True, "status": "smtp_sent", "gateway": "9205175090@vtext.com"}
    monkeypatch.setattr(om, "send_text", fake_send_text)

    om.run(confirm=True)
    assert "Brand Mcdonalds" in sent["message"]
    assert "office manager" in sent["message"].lower()
    row = json.loads(om.LOG_PATH.read_text(encoding="utf-8").splitlines()[0])
    assert row["sms"] == {"attempted": True, "sent": True, "status": "smtp_sent", "gateway": "9205175090@vtext.com"}


def test_dry_run_never_calls_send_text_even_when_sms_enabled(tmp_path, monkeypatch):
    receipts = _env(tmp_path, monkeypatch, sms_enabled=True)
    _write_receipt(receipts, targets=["company:brand-mcdonalds"])

    def boom(message):
        raise AssertionError("send_text must not be called on a dry run")
    monkeypatch.setattr(om, "send_text", boom)

    om.run(confirm=False)


def test_no_new_receipts_is_a_clean_no_op(tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch)
    out = om.run(confirm=True)
    assert out["completions"] == []
    assert not om.LOG_PATH.exists()


def test_send_text_reports_no_smtp_when_credentials_missing(tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch, sms_enabled=True)
    monkeypatch.delenv("RB_SMTP_HOST", raising=False)
    monkeypatch.delenv("RB_SMTP_USER", raising=False)
    monkeypatch.delenv("RB_SMTP_PASS", raising=False)
    result = om.send_text("hello")
    assert result == {"sent": False, "status": "no_smtp",
                       "detail": "Set RB_SMTP_HOST, RB_SMTP_USER, RB_SMTP_PASS env vars"}
