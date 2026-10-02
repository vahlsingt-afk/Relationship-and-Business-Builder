#!/usr/bin/env python3
"""
daily_brief_preflight.py — RB daily brief deployment preflight.

Checks the behavioral-intelligence bridge that must exist before deploying the
daily brief stack:
  1. cos_judgment emits behavioral_intelligence.
  2. daily_brief copies that block into the report.
  3. daily_brief renders a Behavioral Intelligence section.
  4. behavioral_intelligence.json is readable and list-shaped.

The check is intentionally narrow. Broader daily_brief smoke tests can fail for
newer section-order contracts; this script catches the RB 9.26 silent-drop
class directly.
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SYSTEM_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))

import cos_judgment as cj  # noqa: E402
import daily_brief as db  # noqa: E402


def _check(condition: bool, label: str, failures: list[str]) -> None:
    if condition:
        print(f"OK   {label}")
    else:
        print(f"FAIL {label}")
        failures.append(label)


def main() -> int:
    failures: list[str] = []
    today = date.today()

    blocks = cj.build_all(
        {
            "email": {"fetched_at": today.isoformat()},
            "calendar": {"fetched_at": today.isoformat()},
            "social": {"fetched_at": today.isoformat()},
            "loops": {"overdue": [], "due_today": [], "this_week": []},
            "crossings": [],
            "active_threads": [],
            "drr_top": [],
        },
        today,
    )
    _check("behavioral_intelligence" in blocks, "cos_judgment emits behavioral_intelligence", failures)
    _check(
        isinstance(blocks.get("behavioral_intelligence"), dict),
        "behavioral_intelligence block is a dict",
        failures,
    )

    daily_brief_source = (SCRIPT_DIR / "daily_brief.py").read_text()
    _check(
        'report["behavioral_intelligence"] = _cos_blocks.get("behavioral_intelligence")' in daily_brief_source,
        "daily_brief copies behavioral_intelligence from cos_judgment",
        failures,
    )
    _check(
        "_render_behavioral_intelligence_md(report)" in daily_brief_source,
        "render_today_md calls behavioral intelligence renderer",
        failures,
    )
    _check(
        hasattr(db, "_render_behavioral_intelligence_md"),
        "daily_brief exposes behavioral intelligence renderer",
        failures,
    )

    sample = {
        "behavioral_intelligence": {
            "available": True,
            "signal_count": 1,
            "artifact_count": 0,
            "recent_signal_count": 1,
            "source": "behavioral_intelligence.json",
            "signals": [{
                "signal_type": "affordability_stress",
                "confidence": "medium",
                "claim_status": "proposed",
                "persistence_status": "pending confirmation",
                "evidence_sentences": ["Value traffic is rising as diners trade down."],
                "source_type": "linkedin_post",
                "signal_date": today.isoformat(),
            }],
        }
    }
    rendered = "\n".join(db._render_behavioral_intelligence_md(sample))
    _check("## Behavioral Intelligence" in rendered, "renderer includes section heading", failures)
    _check("affordability_stress" in rendered, "renderer includes behavioral signal", failures)

    bi_path = SYSTEM_DIR / "behavioral_intelligence.json"
    if bi_path.exists():
        try:
            raw = json.loads(bi_path.read_text())
            records = raw.get("records") if isinstance(raw, dict) else raw
            _check(isinstance(records, list), "behavioral_intelligence.json has records list", failures)
        except Exception as exc:  # noqa: BLE001
            _check(False, f"behavioral_intelligence.json is readable JSON ({exc})", failures)
    else:
        _check(False, "behavioral_intelligence.json exists", failures)

    print(f"--- daily brief preflight complete: {len(failures)} failure(s) ---")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
