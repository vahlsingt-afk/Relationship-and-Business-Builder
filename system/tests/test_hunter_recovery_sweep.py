"""Tests for scripts/hunter_recovery_sweep.py."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import hunter_recovery_sweep as hrs  # noqa: E402


def test_runs_all_four_steps_in_order_and_collects_their_results():
    calls = []

    def _dw():
        calls.append("download_watcher")
        return {"copied": []}

    def _sync():
        calls.append("drive_inbox_sync")
        return {"copied": []}

    def _om(confirm):
        calls.append("office_manager")
        assert confirm is True
        return {"completions": []}

    def _sweep(confirm):
        calls.append("sweep")
        assert confirm is True
        return {"processed": []}

    with patch.object(hrs.hunter_download_watcher, "sweep_downloads", side_effect=_dw), \
         patch.object(hrs.hunter_drive_inbox_sync, "run", side_effect=_sync), \
         patch.object(hrs.hunter_office_manager, "run", side_effect=_om), \
         patch.object(hrs.hunter_cycle, "sweep", side_effect=_sweep):
        out = hrs.run()

    assert calls == ["download_watcher", "drive_inbox_sync", "office_manager", "sweep"]
    assert out["download_watcher"] == {"copied": []}
    assert out["drive_inbox_sync"] == {"copied": []}
    assert out["office_manager"] == {"completions": []}
    assert out["sweep"] == {"processed": []}


def test_sweep_runs_confirmed_not_a_dry_run():
    # RB-2026-10-09 (part 2): a dry-run sweep archives a failed job's file
    # regardless of outcome (hunter_orchestrator.sync_from_sweep only
    # preserves a retryable job's file when confirm=True), which forced
    # manual restoration after every failed validation before this change.
    with patch.object(hrs.hunter_download_watcher, "sweep_downloads", return_value={}), \
         patch.object(hrs.hunter_drive_inbox_sync, "run", return_value={}), \
         patch.object(hrs.hunter_office_manager, "run", return_value={}), \
         patch.object(hrs.hunter_cycle, "sweep") as mock_sweep:
        hrs.run()
    mock_sweep.assert_called_once_with(confirm=True)


def test_one_steps_failure_does_not_block_the_others():
    def boom():
        raise RuntimeError("download watcher blew up")

    with patch.object(hrs.hunter_download_watcher, "sweep_downloads", side_effect=boom), \
         patch.object(hrs.hunter_drive_inbox_sync, "run", return_value={"copied": []}), \
         patch.object(hrs.hunter_office_manager, "run", return_value={"completions": []}), \
         patch.object(hrs.hunter_cycle, "sweep", return_value={"processed": []}):
        out = hrs.run()

    assert out["download_watcher"] == {"error": "download watcher blew up"}
    assert out["drive_inbox_sync"] == {"copied": []}
    assert out["office_manager"] == {"completions": []}
    assert out["sweep"] == {"processed": []}


def test_main_prints_valid_json_and_returns_zero(capsys):
    with patch.object(hrs, "run", return_value={"schema": "rb.hunter_recovery_sweep.v1"}):
        rc = hrs.main()
    assert rc == 0
    import json
    printed = json.loads(capsys.readouterr().out)
    assert printed["schema"] == "rb.hunter_recovery_sweep.v1"
