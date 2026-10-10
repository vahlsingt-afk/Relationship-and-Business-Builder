"""Tests for scripts/hunter_mutation_review.py."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import hunter_mutation_review as hmr  # noqa: E402


def _queue_row(proposal_id: str, *, queued_at: str = "2026-10-03T09:00:00+00:00", packet_id: str = "pkt-1"):
    return {
        "schema": "rb.hunter_mutation_queue.v1",
        "queued_at": queued_at,
        "packet_id": packet_id,
        "proposal": {"proposal_id": proposal_id, "target_key": "competitor:example", "field_path": "leadership_event_history"},
        "decision": {"decision_class": "auto_added_net_new"},
        "reason": "review required or no registered narrow writer",
    }


@pytest.fixture()
def env(tmp_path, monkeypatch):
    queue = tmp_path / "proposals.jsonl"
    resolutions = tmp_path / "resolutions.jsonl"
    monkeypatch.setattr(hmr, "PROPOSAL_QUEUE_PATH", queue)
    monkeypatch.setattr(hmr, "RESOLUTIONS_PATH", resolutions)
    return {"queue": queue, "resolutions": resolutions}


def _write_queue(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def test_list_pending_returns_everything_when_nothing_resolved(env):
    _write_queue(env["queue"], [_queue_row("mut-a"), _queue_row("mut-b")])
    pending = hmr.list_pending()
    assert [r["proposal"]["proposal_id"] for r in pending] == ["mut-a", "mut-b"]


def test_list_pending_is_oldest_first(env):
    _write_queue(env["queue"], [
        _queue_row("mut-newer", queued_at="2026-10-05T00:00:00+00:00"),
        _queue_row("mut-older", queued_at="2026-10-03T00:00:00+00:00"),
    ])
    pending = hmr.list_pending()
    assert [r["proposal"]["proposal_id"] for r in pending] == ["mut-older", "mut-newer"]


def test_empty_queue_file_is_not_an_error(env):
    assert hmr.list_pending() == []


def test_resolve_approved_removes_it_from_pending(env):
    _write_queue(env["queue"], [_queue_row("mut-a"), _queue_row("mut-b")])
    hmr.resolve("mut-a", "approved", resolved_by="todd")
    pending = hmr.list_pending()
    assert [r["proposal"]["proposal_id"] for r in pending] == ["mut-b"]


def test_resolve_rejected_also_removes_it_from_pending(env):
    _write_queue(env["queue"], [_queue_row("mut-a")])
    hmr.resolve("mut-a", "rejected", resolved_by="todd", note="stale, superseded")
    assert hmr.list_pending() == []


def test_resolve_records_who_when_and_why(env):
    _write_queue(env["queue"], [_queue_row("mut-a", packet_id="pkt-xyz")])
    record = hmr.resolve("mut-a", "approved", resolved_by="todd", note="confirmed via LinkedIn")
    assert record["proposal_id"] == "mut-a"
    assert record["decision"] == "approved"
    assert record["resolved_by"] == "todd"
    assert record["note"] == "confirmed via LinkedIn"
    assert record["packet_id"] == "pkt-xyz"
    assert "resolved_at" in record


def test_resolve_unknown_proposal_id_raises(env):
    _write_queue(env["queue"], [_queue_row("mut-a")])
    with pytest.raises(ValueError, match="no pending proposal"):
        hmr.resolve("mut-does-not-exist", "approved", resolved_by="todd")


def test_resolve_twice_raises_the_second_time(env):
    # Idempotency: a double-click or a stale page shouldn't record two
    # conflicting decisions for the same proposal.
    _write_queue(env["queue"], [_queue_row("mut-a")])
    hmr.resolve("mut-a", "approved", resolved_by="todd")
    with pytest.raises(ValueError, match="no pending proposal"):
        hmr.resolve("mut-a", "rejected", resolved_by="todd")


def test_resolve_invalid_decision_raises(env):
    _write_queue(env["queue"], [_queue_row("mut-a")])
    with pytest.raises(ValueError, match="unknown decision"):
        hmr.resolve("mut-a", "maybe", resolved_by="todd")


def test_list_resolved_is_most_recent_first(env):
    # Written directly (not via resolve()) so the two rows get distinct
    # resolved_at timestamps regardless of how fast the test runs -- two
    # real decisions seconds/minutes apart always sort correctly; this
    # isolates list_resolved()'s own sort from resolve()'s clock precision.
    hmr._append_jsonl(env["resolutions"], {"proposal_id": "mut-a", "resolved_at": "2026-10-03T09:00:00+00:00"})
    hmr._append_jsonl(env["resolutions"], {"proposal_id": "mut-b", "resolved_at": "2026-10-05T09:00:00+00:00"})
    resolved = hmr.list_resolved()
    assert [r["proposal_id"] for r in resolved] == ["mut-b", "mut-a"]


def test_list_resolved_respects_limit(env):
    _write_queue(env["queue"], [_queue_row(f"mut-{i}") for i in range(5)])
    for i in range(5):
        hmr.resolve(f"mut-{i}", "approved", resolved_by="todd")
    assert len(hmr.list_resolved(limit=2)) == 2


def test_malformed_queue_lines_are_skipped_not_fatal(env):
    env["queue"].write_text("not json\n" + json.dumps(_queue_row("mut-a")) + "\n", encoding="utf-8")
    pending = hmr.list_pending()
    assert len(pending) == 1
    assert pending[0]["proposal"]["proposal_id"] == "mut-a"


def test_main_list_prints_json(env, capsys):
    _write_queue(env["queue"], [_queue_row("mut-a")])
    with patch.object(sys, "argv", ["hunter_mutation_review.py", "list"]):
        rc = hmr.main()
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out[0]["proposal"]["proposal_id"] == "mut-a"


def test_main_resolve_cli(env, capsys):
    _write_queue(env["queue"], [_queue_row("mut-a")])
    with patch.object(sys, "argv", ["hunter_mutation_review.py", "resolve", "mut-a", "--decision", "approved", "--by", "todd"]):
        rc = hmr.main()
    assert rc == 0
    assert hmr.list_pending() == []


def test_main_resolve_cli_reports_error_without_crashing(env, capsys):
    with patch.object(sys, "argv", ["hunter_mutation_review.py", "resolve", "mut-ghost", "--decision", "approved", "--by", "todd"]):
        rc = hmr.main()
    assert rc == 2
