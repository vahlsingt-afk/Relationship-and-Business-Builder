#!/usr/bin/env python3
"""Audit RB research entry points for mandatory Hunter adoption."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

WORKER_IDS = {
    "rb-1500-brand-deep-tech-stack-research-segment",
    "rb-chat-deep-research-dispatcher",
    "rb-two-day-research-burst",
    "rb-weekly-surplus-research",
    "rb-deep-research-usage-experiment",
    "rb-hunter-90-unit-gap-cycle",
    "rb-hunter-top10-category-competitor-cycle",
}
REPO_FILES = (
    "system/INTELLIGENCE_CYCLES.md",
    "system/prompts/chatgpt_deep_research_cycle.md",
    "system/research/brand_company_profile_research_instructions.md",
    "system/research/vendor_research_instructions.md",
    "system/technology_lifecycle/RESEARCH_KICKOFF.md",
)


def _toml_string(text: str, key: str) -> str:
    match = re.search(rf'^{re.escape(key)}\s*=\s*"(.*)"\s*$', text, re.MULTILINE)
    return match.group(1) if match else ""


def audit(repo: Path, automations: Path) -> dict:
    errors = []
    checked = []
    for automation_id in sorted(WORKER_IDS):
        path = automations / automation_id / "automation.toml"
        if not path.exists():
            errors.append({"code": "research_automation_missing", "path": str(path)})
            continue
        text = path.read_text(encoding="utf-8")
        prompt = _toml_string(text, "prompt")
        checked.append(str(path))
        if "Hunter" not in prompt or "hunter_cycle.py" not in prompt:
            errors.append({"code": "research_automation_not_hunter", "path": str(path)})
        if "produce one packet and JSON sidecar following" in prompt:
            errors.append({"code": "legacy_cycle_contract_active", "path": str(path)})
    for relative in REPO_FILES:
        path = repo / relative
        checked.append(str(path))
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        if "Hunter" not in text or "hunter_cycle.py" not in text:
            errors.append({"code": "research_instruction_not_hunter", "path": str(path)})
    return {"schema": "rb.hunter_cycle_adoption_audit.v1", "valid": not errors,
            "checked": checked, "errors": errors}


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit all RB research workers for Hunter adoption")
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parent.parent.parent))
    parser.add_argument("--automations", default=str(Path.home() / ".codex" / "automations"))
    args = parser.parse_args()
    result = audit(Path(args.repo), Path(args.automations))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
