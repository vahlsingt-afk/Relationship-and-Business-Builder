"""
test_rbb_assertion_bridge.py

RB-DEFECT-2026-08-20: RBB (the ChatGPT Project) has no Actions/API access,
so an explicit user statement recognized inside RBB couldn't be persisted
without opening the Custom GPT separately.

RB-2026-08-23: v1 of this bridge used Gmail (RBB drafts an email) as the
transport -- confirmed broken in live testing twice (RBB claimed a sent/
drafted email that never existed). Independently re-verified 2026-08-23
that RBB can reliably create a real Google Doc via ChatGPT's Drive write
action. This version drains Drive instead: RBB creates a Doc titled
"[RBB-ASSERTION] ..." with the same structured block; this script finds,
validates, and applies it through the existing mutation API on a schedule.

Covers: valid persist, duplicate assertion_id never re-applied, owner not
in self_emails rejected, unknown mutation_type rejected, missing required
fields rejected, a failed API call recorded (not silently dropped).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import rb_core as core  # noqa: E402
import rbb_assertion_bridge as bridge  # noqa: E402


def _doc(*, owner="todd@example.com", title="[RBB-ASSERTION] loop", text=""):
    return {"file_id": "f1", "title": title, "owner": owner, "text": text}


def _assertion_body(assertion: dict) -> str:
    return (
        "Logging this per our chat.\n\n"
        "-----RBB-ASSERTION-JSON-----\n"
        f"{json.dumps(assertion)}\n"
        "-----END-RBB-ASSERTION-JSON-----\n"
    )


def _wire_state(tmp_path, monkeypatch):
    state_path = tmp_path / "state.json"
    monkeypatch.setattr(bridge, "STATE_PATH", state_path)
    monkeypatch.setattr(core, "load_inbox_accounts", lambda: [
        {"enabled": True, "email": "todd@example.com"},
    ])
    return state_path


def _add_loop_assertion(aid="a-1"):
    return {
        "assertion_id": aid, "mutation_type": "add_loop",
        "asserted_at": "2026-08-20T12:00:00Z",
        "user_statement": "Add sending Ed Gardner the MAPS deck as my number-two priority.",
        "fields": {"party": "Ed Gardner", "description": "Send the MAPS deck", "target": "2026-08-21"},
    }


def test_valid_assertion_persists_via_api(tmp_path, monkeypatch):
    _wire_state(tmp_path, monkeypatch)
    assertion = _add_loop_assertion()
    doc = _doc(text=_assertion_body(assertion))

    with patch.object(bridge, "_fetch_candidate_docs", return_value=[doc]), \
         patch.object(bridge, "_call_api", return_value=(True, {"ok": True, "id": "L-2026-08-20-001"})) as mocked:
        result = bridge.run()

    assert result["applied"] == 1
    assert result["results"][0]["status"] == "persisted"
    mocked.assert_called_once_with("/loops", assertion["fields"])


def test_duplicate_assertion_id_never_reapplied(tmp_path, monkeypatch):
    state_path = _wire_state(tmp_path, monkeypatch)
    assertion = _add_loop_assertion()
    doc = _doc(text=_assertion_body(assertion))

    with patch.object(bridge, "_fetch_candidate_docs", return_value=[doc]), \
         patch.object(bridge, "_call_api", return_value=(True, {"ok": True})) as mocked:
        bridge.run()
        bridge.run()  # same doc still in Drive on the next drain

    assert mocked.call_count == 1
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert "f1" in state["processed"]


def test_reused_assertion_id_on_a_different_doc_is_not_silently_dropped(tmp_path, monkeypatch):
    """RB-2026-08-24 live incident: RBB restarted its assertion_id numbering
    mid-session ("rbb-2026-08-24-1" used for both a discarded early test and,
    later, a real loop closure). Deduping on assertion_id made the drain
    treat the second, real assertion as already resolved -- it vanished from
    the results entirely, no error, nothing. Two different Drive docs (two
    different file_ids) must each get their own outcome even if RBB's
    self-generated assertion_id collides."""
    _wire_state(tmp_path, monkeypatch)
    first_assertion = _add_loop_assertion(aid="rbb-2026-08-24-1")
    first_doc = {"file_id": "doc-A", "title": "[RBB-ASSERTION] first",
                 "owner": "todd@example.com", "text": _assertion_body(first_assertion)}
    second_assertion = _add_loop_assertion(aid="rbb-2026-08-24-1")  # reused id
    second_doc = {"file_id": "doc-B", "title": "[RBB-ASSERTION] second",
                  "owner": "todd@example.com", "text": _assertion_body(second_assertion)}

    with patch.object(bridge, "_fetch_candidate_docs", return_value=[first_doc]), \
         patch.object(bridge, "_call_api", return_value=(True, {"ok": True})):
        bridge.run()

    with patch.object(bridge, "_fetch_candidate_docs", return_value=[first_doc, second_doc]), \
         patch.object(bridge, "_call_api", return_value=(True, {"ok": True})) as mocked:
        second_run = bridge.run()

    assert mocked.call_count == 1  # only doc-B is new; doc-A is correctly skipped
    assert len(second_run["results"]) == 1
    assert second_run["results"][0]["file_id"] == "doc-B"
    assert second_run["results"][0]["status"] == "persisted"


def test_owner_not_self_account_is_rejected(tmp_path, monkeypatch):
    _wire_state(tmp_path, monkeypatch)
    assertion = _add_loop_assertion()
    doc = _doc(owner="someone-else@example.com", text=_assertion_body(assertion))

    with patch.object(bridge, "_fetch_candidate_docs", return_value=[doc]), \
         patch.object(bridge, "_call_api") as mocked:
        result = bridge.run()

    mocked.assert_not_called()
    assert result["results"][0]["status"] == "skipped_duplicate"


def test_unsupported_mutation_type_is_rejected(tmp_path, monkeypatch):
    _wire_state(tmp_path, monkeypatch)
    assertion = _add_loop_assertion()
    assertion["mutation_type"] = "close_opportunity"  # distributed_consolidation_pending, not yet supported
    doc = _doc(text=_assertion_body(assertion))

    with patch.object(bridge, "_fetch_candidate_docs", return_value=[doc]), \
         patch.object(bridge, "_call_api") as mocked:
        result = bridge.run()

    mocked.assert_not_called()
    assert result["results"][0]["status"] == "failed"
    assert "unsupported mutation_type" in result["results"][0]["reason"]


def test_missing_required_field_is_rejected(tmp_path, monkeypatch):
    _wire_state(tmp_path, monkeypatch)
    assertion = _add_loop_assertion()
    del assertion["fields"]["target"]
    doc = _doc(text=_assertion_body(assertion))

    with patch.object(bridge, "_fetch_candidate_docs", return_value=[doc]), \
         patch.object(bridge, "_call_api") as mocked:
        result = bridge.run()

    mocked.assert_not_called()
    assert result["results"][0]["status"] == "failed"


def test_failed_api_call_recorded_not_dropped(tmp_path, monkeypatch):
    _wire_state(tmp_path, monkeypatch)
    assertion = _add_loop_assertion()
    doc = _doc(text=_assertion_body(assertion))

    with patch.object(bridge, "_fetch_candidate_docs", return_value=[doc]), \
         patch.object(bridge, "_call_api", return_value=(False, {"error": "connection refused"})):
        result = bridge.run()

    assert result["applied"] == 0
    assert result["results"][0]["status"] == "failed"


def test_transport_failure_is_retried_not_permanently_dropped(tmp_path, monkeypatch):
    """RB-2026-08-24: a 401 from a misconfigured API key got permanently
    marked processed on first drain, silently discarding a real assertion
    forever. A transport/auth/server failure must stay eligible for the
    next drain -- only a genuine verdict on the assertion's content (success,
    validation failure, 400 conflict) may be deduped."""
    state_path = _wire_state(tmp_path, monkeypatch)
    assertion = _add_loop_assertion()
    doc = _doc(text=_assertion_body(assertion))

    with patch.object(bridge, "_fetch_candidate_docs", return_value=[doc]), \
         patch.object(bridge, "_call_api",
                      return_value=(False, {"status_code": 401, "error": "invalid_x_api_key"})):
        first = bridge.run()

    assert first["results"][0]["status"] == "failed"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert "f1" not in state["processed"]

    with patch.object(bridge, "_fetch_candidate_docs", return_value=[doc]), \
         patch.object(bridge, "_call_api", return_value=(True, {"ok": True})) as mocked:
        second = bridge.run()

    mocked.assert_called_once()
    assert second["results"][0]["status"] == "persisted"


def test_non_assertion_doc_with_tagged_title_is_ignored(tmp_path, monkeypatch):
    """A [RBB-ASSERTION]-titled doc with no parseable JSON block shouldn't
    crash or get treated as a malformed assertion -- it's just not ours."""
    _wire_state(tmp_path, monkeypatch)
    doc = _doc(text="just some other document text, no structured block here")

    with patch.object(bridge, "_fetch_candidate_docs", return_value=[doc]), \
         patch.object(bridge, "_call_api") as mocked:
        result = bridge.run()

    mocked.assert_not_called()
    assert result["results"] == []


def test_dry_run_validates_but_never_calls_api_or_persists_state(tmp_path, monkeypatch):
    state_path = _wire_state(tmp_path, monkeypatch)
    assertion = _add_loop_assertion()
    doc = _doc(text=_assertion_body(assertion))

    with patch.object(bridge, "_fetch_candidate_docs", return_value=[doc]), \
         patch.object(bridge, "_call_api") as mocked:
        result = bridge.run(dry_run=True)

    mocked.assert_not_called()
    assert result["results"][0]["status"] == "would_persist"
    assert not state_path.exists()
