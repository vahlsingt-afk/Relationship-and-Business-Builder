#!/usr/bin/env python3
"""Regression coverage for refresh_all.py's campaign-rebuild step (RB
ended-role/current-company cleanup, 2026-08-06, remaining-work item 7).
Before this, campaign artifacts under system/campaigns/*/ only reflected
baseline corrections (like Richard Heyman's) after a manual
`campaign_engine.py --refresh-all` run — refresh_all.py never called it."""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import refresh_all  # noqa: E402


def test_commands_include_campaign_refresh():
    commands = refresh_all.build_commands(py="python3")
    campaign_cmds = [c for c in commands if Path(c[1]).name == "campaign_engine.py"]
    assert len(campaign_cmds) == 1
    assert "--refresh-all" in campaign_cmds[0]


def test_campaign_refresh_runs_before_daily_brief_and_after_passive_ri_ingest():
    """campaign_engine needs the passive_ri_ingest *proposal* pass (its
    --cache step, which runs after relationship_signals) already fresh, and
    must itself finish before daily_brief reads campaign artifacts.

    RB-2026-08-24: confirm_passive_ri defaults to True now (Todd's explicit
    decision), so build_commands() with no override also appends a SECOND,
    later passive_ri_ingest.py invocation (`confirm --all --high-confidence-
    only`) deliberately placed after campaign_engine, right before
    daily_brief -- so this checks the FIRST occurrence (the proposal step
    campaign_engine actually depends on), not the last."""
    commands = refresh_all.build_commands(py="python3")
    names = [Path(c[1]).name for c in commands]
    campaign_idx = names.index("campaign_engine.py")
    daily_brief_idx = names.index("daily_brief.py")
    first_passive_ri_idx = names.index("passive_ri_ingest.py")
    assert first_passive_ri_idx < campaign_idx < daily_brief_idx


def test_confirm_passive_ri_defaults_to_true():
    """RB-2026-08-24: flipped per Todd's explicit decision -- high-confidence
    passive RI projections must apply automatically, not sit pending forever
    unless someone remembers to pass --confirm-passive-ri by hand."""
    commands = refresh_all.build_commands(py="python3")
    confirm_cmds = [c for c in commands
                    if Path(c[1]).name == "passive_ri_ingest.py" and "confirm" in c]
    assert len(confirm_cmds) == 1
    assert "--high-confidence-only" in confirm_cmds[0]


def test_confirm_passive_ri_can_still_be_disabled():
    commands = refresh_all.build_commands(py="python3", confirm_passive_ri=False)
    confirm_cmds = [c for c in commands
                    if Path(c[1]).name == "passive_ri_ingest.py" and "confirm" in c]
    assert confirm_cmds == []


def test_confirm_passive_ri_step_runs_between_campaign_refresh_and_daily_brief():
    commands = refresh_all.build_commands(py="python3")
    names = [Path(c[1]).name for c in commands]
    campaign_idx = names.index("campaign_engine.py")
    daily_brief_idx = names.index("daily_brief.py")
    confirm_idx = next(
        i for i, c in enumerate(commands)
        if Path(c[1]).name == "passive_ri_ingest.py" and "confirm" in c
    )
    assert campaign_idx < confirm_idx < daily_brief_idx
