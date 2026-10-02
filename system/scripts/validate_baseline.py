#!/usr/bin/env python3
"""
validate_baseline.py — thin wrapper around `system/schemas/validate.py`.

The schema check (jsonschema) lives in `system/schemas/validate.py`. This
wrapper sits alongside the other scripts so callers don't have to remember
which directory the validator lives in. It also adds a few non-schema
integrity checks the JSON schema can't express cleanly:

    - Duplicate `id` values.
    - RC entries with `last_touch` in the future (clock drift / typo).
    - RC entries with `rc_state == "ACTIVE"` but `rc_tier` not in the canonical set.

Usage:
    python3 validate_baseline.py
    python3 validate_baseline.py --json
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rb_core as core


SCHEMA_VALIDATOR = core.SYSTEM_DIR / "schemas" / "validate.py"


def integrity_checks(baseline: list[dict], today: date) -> dict:
    issues = []
    # Duplicate IDs
    ids = Counter(e.get("id") for e in baseline)
    for k, v in ids.items():
        if v > 1:
            issues.append({"kind": "duplicate_id", "id": k, "count": v})

    # Future last_touch
    for e in baseline:
        lt = e.get("last_touch")
        if lt:
            try:
                if date.fromisoformat(lt) > today:
                    issues.append({
                        "kind": "future_last_touch",
                        "id": e.get("id"),
                        "name": e.get("name"),
                        "last_touch": lt,
                    })
            except ValueError:
                issues.append({
                    "kind": "bad_last_touch_format",
                    "id": e.get("id"),
                    "last_touch": lt,
                })

    # RC tier sanity
    for e in baseline:
        if e.get("signal_class") == "RC" and e.get("rc_state") == "ACTIVE":
            if e.get("rc_tier") not in core.TIER_THRESHOLD_DAYS:
                issues.append({
                    "kind": "rc_active_bad_tier",
                    "id": e.get("id"),
                    "name": e.get("name"),
                    "rc_tier": e.get("rc_tier"),
                })

    return {"issues": issues, "ok": len(issues) == 0}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--json", action="store_true")
    p.add_argument("--cache", action="store_true",
                   help="Write to system/.cache/validate_baseline.json")
    args = p.parse_args()

    # RB-2026-08-28: was a bare "python3" -- see mutations.py's
    # _validate_baseline_or_rollback for the confirmed-live incident this
    # traces back to (production's PYTHONNOUSERSITE=1 + vendored PYTHONPATH
    # meant "python3" on PATH resolved to an interpreter without the
    # vendored jsonschema, silently failing every baseline validation).
    rc = subprocess.run(
        [sys.executable, str(SCHEMA_VALIDATOR)],
        capture_output=True, text=True,
    )
    integrity = integrity_checks(core.load_baseline(), date.today())

    payload = {
        "schema_returncode": rc.returncode,
        "schema_stdout": rc.stdout.strip(),
        "schema_stderr": rc.stderr.strip(),
        "integrity": integrity,
    }
    if args.cache:
        core.write_cache("validate_baseline", payload, source="validate_baseline.py")

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print("== Schema check ==")
        print(rc.stdout, end="")
        if rc.stderr:
            print(rc.stderr, end="", file=sys.stderr)
        print()
        print("== Integrity checks ==")
        if integrity["ok"]:
            print("OK — no integrity issues.")
        else:
            for i in integrity["issues"]:
                print(f"  - {i}")

    if rc.returncode != 0 or not integrity["ok"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
