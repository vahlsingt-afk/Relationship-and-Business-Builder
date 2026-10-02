"""Regression tests for the legacy Blue Sheet compatibility reader."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[2] / "blue_sheets" / "_engine" / "common.py"
SPEC = importlib.util.spec_from_file_location("blue_sheet_common_under_test", MODULE_PATH)
assert SPEC and SPEC.loader
common = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(common)


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def test_load_account_allows_missing_optional_actions_file(tmp_path, monkeypatch):
    slug = "churchs-texas-chicken"
    account_dir = tmp_path / "accounts" / slug
    account_dir.mkdir(parents=True)
    _write_json(account_dir / "account.json", {"account_id": f"acct-{slug}"})
    _write_json(account_dir / "brand_profile.json", {})
    _write_json(account_dir / "contradictions.json", {"contradictions": []})
    _write_json(account_dir / "source_index.json", {"sources": []})
    monkeypatch.setattr(common, "CUSTOMERS_PROSPECTS_ROOT", tmp_path)

    dossier = common.load_account(slug)

    assert dossier["actions"] == {
        "account_id": f"acct-{slug}",
        "parent_rbb_loop_id": None,
        "actions": [],
    }
