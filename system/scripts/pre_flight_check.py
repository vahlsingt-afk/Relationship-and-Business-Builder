#!/usr/bin/env python3
"""
pre_flight_check.py — RB deployment readiness validator (RB 9.26).

Verifies the full stack is ready before deploying the API server or
uploading the openapi.yaml to the Custom GPT Actions schema.

Checks:
  1. All intelligence modules importable
  2. All persistence stores present or creatable
  3. API server module importable
  4. OpenAPI spec present and syntactically valid
  5. Ingest trigger rules document present
  6. Required ingest endpoints present in server
  7. KB/schema consistency (RB-2026-08-25 — added after a KB audit found
     a live op missing from the schema and 3 files mandating retired ops,
     none caught until a human happened to read carefully)
  8. Test suite green (optional, slow — pass --skip-tests to omit)

Usage:
    python3 system/scripts/pre_flight_check.py
    python3 system/scripts/pre_flight_check.py --skip-tests
"""
from __future__ import annotations

import argparse
import importlib
import json
import subprocess
import sys
from pathlib import Path

SYSTEM_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = SYSTEM_DIR.parent
SCRIPTS_DIR = SYSTEM_DIR / "scripts"
API_DIR = SYSTEM_DIR / "api"

sys.path.insert(0, str(SCRIPTS_DIR))

PASS = "✓"
FAIL = "✗"
WARN = "⚠"


def _check(label: str, ok: bool, detail: str = "") -> bool:
    icon = PASS if ok else FAIL
    suffix = f"  → {detail}" if detail else ""
    print(f"  {icon}  {label}{suffix}")
    return ok


def check_module_imports() -> bool:
    print("\n[1] Intelligence module imports")
    modules = [
        "experiential_intelligence",
        "macro_intelligence",
        "relationship_intake",
        "insight_intake",
        "ingest_surface",
        "cos_judgment",
        "daily_brief",
        "rb_core",
    ]
    all_ok = True
    for mod in modules:
        try:
            importlib.import_module(mod)
            _check(mod, True)
        except ImportError as e:
            _check(mod, False, str(e))
            all_ok = False
    return all_ok


def check_store_paths() -> bool:
    print("\n[2] Persistence stores")
    stores = [
        (SYSTEM_DIR / "experiential_intelligence.json", "experiential_intelligence.json"),
        (SYSTEM_DIR / "behavioral_intelligence.json", "behavioral_intelligence.json"),
        (SYSTEM_DIR / "entity_intelligence.json", "entity_intelligence.json"),
        (SYSTEM_DIR / "strategic_memory.json", "strategic_memory.json"),
        (SYSTEM_DIR / "interaction_ledger.json", "interaction_ledger.json"),
        (SYSTEM_DIR / "baseline_index.json", "baseline_index.json"),
    ]
    all_ok = True
    for path, label in stores:
        exists = path.exists()
        writable = path.parent.exists() and path.parent.is_dir()
        if exists:
            _check(label, True, "present")
        elif writable:
            _check(label, True, "not yet created — will be created on first ingest")
        else:
            _check(label, False, f"parent directory missing: {path.parent}")
            all_ok = False
    return all_ok


def check_api_server() -> bool:
    print("\n[3] API server module")
    server_path = API_DIR / "server.py"
    if not server_path.exists():
        _check("system/api/server.py", False, "file not found")
        return False
    _check("system/api/server.py", True)

    # Check that the ingest endpoints are present
    server_text = server_path.read_text()
    required_routes = [
        ("/ingest/experience", "POST /ingest/experience"),
        ("/ingest/experience/confirm", "POST /ingest/experience/confirm"),
        ("/ingest/experience/retrieve", "GET /ingest/experience/retrieve"),
        ("/ingest/macro", "POST /ingest/macro"),
        ("/ingest/macro/confirm", "POST /ingest/macro/confirm"),
        ("/ingest/relationship", "POST /ingest/relationship"),
        ("/ingest/relationship/confirm", "POST /ingest/relationship/confirm"),
        ("/ingest/insight", "POST /ingest/insight"),
        ("/ingest/insight/confirm", "POST /ingest/insight/confirm"),
        ("/query/experiences", "GET /query/experiences"),
        ("/query/experiences/hooks", "GET /query/experiences/hooks"),
        ("/query/relationships", "GET /query/relationships"),
        ("/query/relationships/who-matters-now", "GET /query/relationships/who-matters-now"),
        ("/query/macro/signals", "GET /query/macro/signals"),
        ("/query/macro/artifacts", "GET /query/macro/artifacts"),
        ("/query/macro/entities", "GET /query/macro/entities"),
        ("/query/insights", "GET /query/insights"),
    ]
    all_ok = True
    for route, label in required_routes:
        present = route in server_text
        _check(label, present)
        if not present:
            all_ok = False

    # Check that all four intelligence imports are present
    for module in ("experiential_intelligence", "macro_intelligence",
                   "relationship_intake", "insight_intake"):
        present = f"import {module}" in server_text
        _check(f"import {module}", present)
        if not present:
            all_ok = False

    return all_ok


def check_openapi_spec() -> bool:
    print("\n[4] OpenAPI spec")
    spec_path = API_DIR / "openapi.yaml"
    if not spec_path.exists():
        _check("system/api/openapi.yaml", False, "file not found")
        return False
    _check("system/api/openapi.yaml", True)

    spec_text = spec_path.read_text()
    required_paths = [
        "/ingest/experience",
        "/ingest/experience/confirm",
        "/ingest/experience/retrieve",
        "/ingest/macro",
        "/ingest/macro/confirm",
        "/ingest/relationship",
        "/ingest/relationship/confirm",
        "/ingest/insight",
        "/ingest/insight/confirm",
        "/query/experiences",
        "/query/experiences/hooks",
        "/query/relationships",
        "/query/relationships/who-matters-now",
        "/query/macro/signals",
        "/query/macro/artifacts",
        "/query/macro/entities",
        "/query/insights",
    ]
    all_ok = True
    for path in required_paths:
        present = path in spec_text
        _check(path, present)
        if not present:
            all_ok = False

    return all_ok


def check_trigger_rules() -> bool:
    print("\n[5] Ingest trigger rules document")
    rules_path = SYSTEM_DIR / "INGEST_TRIGGER_RULES.md"
    if not rules_path.exists():
        _check("system/INGEST_TRIGGER_RULES.md", False, "file not found")
        return False
    text = rules_path.read_text()
    required_sections = [
        "POST /ingest/experience",
        "POST /ingest/macro",
        "POST /ingest/relationship",
        "POST /ingest/insight",
        "Trust Stats Display Contract",
        "Confirmation Flow",
    ]
    all_ok = True
    for section in required_sections:
        present = section in text
        _check(section, present)
        if not present:
            all_ok = False
    return all_ok


def check_daily_brief_integration() -> bool:
    print("\n[6] Daily brief integration")
    db_path = SCRIPTS_DIR / "daily_brief.py"
    if not db_path.exists():
        _check("system/scripts/daily_brief.py", False, "file not found")
        return False

    db_text = db_path.read_text()
    checks = [
        ("behavioral_intelligence in build_report()", '"behavioral_intelligence": cos_blocks["behavioral_intelligence"]'),
        ("_render_behavioral_intelligence_md defined", "def _render_behavioral_intelligence_md"),
        ("_render_behavioral_intelligence_md called in render_today_md", "_render_behavioral_intelligence_md(report)"),
        ("behavioral_intelligence in canonical_brief", '"behavioral_intelligence": cos_blocks["behavioral_intelligence"]'),
    ]
    all_ok = True
    for label, needle in checks:
        present = needle in db_text
        _check(label, present)
        if not present:
            all_ok = False
    return all_ok


def check_uvicorn_available() -> bool:
    print("\n[7] Deployment dependencies")
    try:
        import uvicorn
        _check("uvicorn", True, uvicorn.__version__)
    except ImportError:
        _check("uvicorn", False, "pip install uvicorn --break-system-packages")
        return False

    try:
        import fastapi
        _check("fastapi", True, fastapi.__version__)
    except ImportError:
        _check("fastapi", False, "pip install fastapi --break-system-packages")
        return False

    start_script = API_DIR / "start_server.sh"
    _check("system/api/start_server.sh", start_script.exists())
    return True


def check_kb_consistency() -> bool:
    """RB-2026-08-25: KB/schema drift check. A FAIL here for
    'unrouted_live_ops: getCockpitContext' as of 2026-08-25 is expected and
    not a regression — that op is deliberately unrouted pending the RBB
    Project consolidation decision. Any OTHER finding is a real bug."""
    print("\n[8] KB/schema consistency")
    result = subprocess.run(
        [sys.executable, str(SYSTEM_DIR / "scripts" / "validate_kb_consistency.py")],
        capture_output=True, text=True, cwd=str(PROJECT_DIR),
    )
    ok = result.returncode == 0
    detail = result.stdout.strip().splitlines()
    _check("validate_kb_consistency.py", ok, detail[-1] if detail else "")
    if not ok:
        for line in detail:
            print(f"      {line}")
    return ok


def run_tests() -> bool:
    print("\n[9] Test suite")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "system/tests/", "-q", "--tb=no"],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_DIR),
    )
    lines = result.stdout.strip().splitlines()
    summary = lines[-1] if lines else "no output"
    passed = result.returncode == 0
    _check("pytest system/tests/", passed, summary)
    return passed


def main() -> int:
    parser = argparse.ArgumentParser(description="RB deployment readiness check")
    parser.add_argument("--skip-tests", action="store_true", help="Skip test suite run")
    args = parser.parse_args()

    print("RB Pre-Flight Deployment Check")
    print("=" * 45)

    results = [
        check_module_imports(),
        check_store_paths(),
        check_api_server(),
        check_openapi_spec(),
        check_trigger_rules(),
        check_daily_brief_integration(),
        check_uvicorn_available(),
        check_kb_consistency(),
    ]

    if not args.skip_tests:
        results.append(run_tests())
    else:
        print("\n[9] Test suite — skipped (--skip-tests)")

    passed = sum(results)
    total = len(results)
    all_ok = all(results)

    print("\n" + "=" * 45)
    if all_ok:
        print(f"READY TO DEPLOY — {passed}/{total} checks passed")
        print("\nNext steps:")
        print("  1. Start server:  ./system/api/start_server.sh")
        print("  2. Upload:        system/api/openapi.yaml → Custom GPT Actions schema")
        print("  3. Add to prompt: system/INGEST_TRIGGER_RULES.md → Custom GPT system prompt")
        print("  4. Verify live:   Share an experience → confirm ingest fires automatically")
    else:
        failed = total - passed
        print(f"NOT READY — {failed} check(s) failed. Resolve above before deploying.")

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
