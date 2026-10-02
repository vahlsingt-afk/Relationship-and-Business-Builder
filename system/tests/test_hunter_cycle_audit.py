from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
import hunter_cycle_audit  # noqa: E402


def test_audit_rejects_legacy_worker(tmp_path):
    repo = tmp_path / "repo"
    automations = tmp_path / "automations"
    for relative in hunter_cycle_audit.REPO_FILES:
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("Hunter system/scripts/hunter_cycle.py")
    for automation_id in hunter_cycle_audit.WORKER_IDS:
        path = automations / automation_id / "automation.toml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('prompt = "Hunter system/scripts/hunter_cycle.py"\n')
    broken = automations / "rb-weekly-surplus-research" / "automation.toml"
    broken.write_text('prompt = "produce one packet and JSON sidecar following old template"\n')
    result = hunter_cycle_audit.audit(repo, automations)
    assert result["valid"] is False
    assert any(error["code"] == "research_automation_not_hunter" for error in result["errors"])
