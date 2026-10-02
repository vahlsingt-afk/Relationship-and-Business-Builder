from __future__ import annotations

import sys
import zipfile
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import linkedin_export_watcher as watcher


def test_full_archive_routes_connections_and_messages(monkeypatch, tmp_path):
    archive = tmp_path / "Complete_LinkedInDataExport.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("Connections.csv", "First Name,Last Name\nJane,Doe\n")
        zf.writestr("messages.csv", "CONVERSATION ID,FROM\n1,Jane Doe\n")

    calls = []

    def fake_connections(path: Path, *, dry_run: bool):
        calls.append(("connections", path.name, dry_run))
        return {"ok": True, "router": "linkedin_ingest.py"}

    def fake_messages(path: Path, *, dry_run: bool):
        calls.append(("messages", path.name, dry_run))
        return {"ok": True, "router": "linkedin_messaging.py"}

    monkeypatch.setattr(watcher, "_run_connections_ingest", fake_connections)
    monkeypatch.setattr(watcher, "_run_messaging_ingest", fake_messages)

    result = watcher._process_candidate({
        "path": str(archive),
        "rel_path": archive.name,
        "hash": "abc123",
        "classification": "linkedin_zip_connections",
    }, dry_run=False)

    assert result["ok"] is True
    assert result["router"] == "linkedin_full_archive"
    assert [call[0] for call in calls] == ["connections", "messages"]
    assert result["connections_ingest"]["ok"] is True
    assert result["messages_ingest"]["ok"] is True
