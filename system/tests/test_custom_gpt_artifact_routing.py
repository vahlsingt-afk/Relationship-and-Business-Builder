#!/usr/bin/env python3
"""Regression coverage for known uploaded artifact routing."""
from __future__ import annotations

import re
from pathlib import Path
import sys
import yaml

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import canonical_response_eval as cre  # noqa: E402


SYSTEM_DIR = Path(__file__).resolve().parent.parent


def _read(rel: str) -> str:
    return (SYSTEM_DIR / rel).read_text(encoding="utf-8")


def test_gpt_action_surface_can_ingest_recognized_linkedin_exports():
    spec = _read("api/openapi_gpt.yaml")

    operation_ids = re.findall(r"operationId:\s*([A-Za-z0-9_]+)", spec)
    # 30-op hard platform cap (Custom GPT Actions); 25 as of the
    # DEFECT-009/011/012 reconciliation (RB 9.6x). See test_wiring_gaps.py
    # TestWG4_GPTYAMLBudget for the authoritative budget test.
    assert len(operation_ids) <= 30
    assert "uploadAndIngestFile" in operation_ids
    assert "/ingest/upload:" in spec
    assert "ingestLinkedInCSV" not in operation_ids
    assert "ingestLinkedInExtended" not in operation_ids


def test_gpt_action_schema_uses_api_key_security_not_manual_header_param():
    spec = yaml.safe_load(_read("api/openapi_gpt.yaml"))
    scheme = ((spec.get("components") or {}).get("securitySchemes") or {}).get("ApiKeyAuth") or {}
    assert scheme == {"type": "apiKey", "in": "header", "name": "x-api-key"}
    assert {"ApiKeyAuth": []} in (spec.get("security") or [])

    for path_item in (spec.get("paths") or {}).values():
        for operation in path_item.values():
            if not isinstance(operation, dict):
                continue
            for param in operation.get("parameters") or []:
                assert not (
                    param.get("name") == "x-api-key" and param.get("in") == "header"
                )


def test_prompts_ban_generic_menu_for_linkedin_export_zip():
    compact = _read("api/custom_gpt_instructions_8k.md")
    full = _read("api/custom_gpt_prompt.md")
    # RB-2026-09-05: this was reading the bare "CANONICAL_RESPONSE_CONTRACT.md"
    # (root), which self-marks itself SUPERSEDED and predates the Part 1/2
    # brief split -- a pre-existing latent bug (testing the wrong file), not
    # something this fix introduced. api/CANONICAL_RESPONSE_CONTRACT.md is the
    # real, current, authoritative contract (see canonical_response_eval.py's
    # own header comment) and contains the same required phrase.
    contract = _read("api/CANONICAL_RESPONSE_CONTRACT.md")

    for text in (compact, full, contract):
        assert "uploadAndIngestFile" in text or "POST /ingest/upload" in text

    assert "Do not ask what to do" in compact
    assert "What would you like to do with it?" in full
    assert '"What would you like to do with it?" for a recognized LinkedIn export ZIP' in contract


def test_exact_bad_linkedin_zip_menu_fails_canonical_eval():
    bad = """I see the LinkedIn export ZIP file uploaded. What would you like done with it?

Examples:

* Analyze your network and relationship intelligence
* Extract company/contact insights for RB
* Generate CRM-ready structured data
* Create a defect report/test case for RB ingestion
"""

    report = cre.evaluate(bad, "known_artifact_ingest")
    assert report.passed is False
    failed = {c.name for c in report.checks if not c.passed}
    assert "no_generic_upload_menu" in failed


def test_narrow_missing_path_response_passes_canonical_eval():
    good = """RI found: LinkedIn export ZIP.
RB recognized this as baseline enhancement input.

Persistence status: not_persisted
Next action: [ask_todd] I need the uploaded file path or attachment handle so RB can ingest it through ingestLinkedInExport.
"""

    report = cre.evaluate(good, "known_artifact_ingest")
    assert report.passed is True
