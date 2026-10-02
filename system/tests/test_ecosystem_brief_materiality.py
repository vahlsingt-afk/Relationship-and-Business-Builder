"""
test_ecosystem_brief_materiality.py

RB-2026-08-23: is_material had zero test coverage anywhere in the repo despite
being the gate between "routine press mention" and "worth surfacing as an
action" -- confirmed by the P0-1 quality-gate exploration pass. Extracted
from an inline expression in ecosystem_brief.py's build_section() to a real
top-level function (_is_material_signal) specifically so it could be tested
and reused by cockpit_context.py (which previously carried its own,
independently-maintained duplicate of the same signal-class set).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import ecosystem_brief as eb


def test_material_class_with_high_confidence_is_material():
    assert eb._is_material_signal("high", "leadership_change") is True


def test_material_class_with_medium_confidence_is_material():
    assert eb._is_material_signal("medium", "rfp_cycle_signal") is True


def test_material_class_with_low_confidence_is_not_material():
    assert eb._is_material_signal("low", "extreme_pain") is False


def test_non_material_class_is_never_material_regardless_of_confidence():
    assert eb._is_material_signal("high", "general_market_context") is False
    assert eb._is_material_signal("high", "expansion_signal") is False


def test_all_five_material_classes_covered():
    for sig_class in ("leadership_change", "rfp_cycle_signal", "extreme_pain",
                       "vendor_displacement", "funding_event"):
        assert eb._is_material_signal("high", sig_class) is True, sig_class


def test_unknown_confidence_value_is_not_material():
    assert eb._is_material_signal(None, "leadership_change") is False
    assert eb._is_material_signal("unverified", "leadership_change") is False


def test_material_signal_classes_constant_matches_function_behavior():
    """Regression guard: MATERIAL_SIGNAL_CLASSES is the single source of truth
    cockpit_context.py also imports -- if someone edits one without the
    other, this catches the drift immediately."""
    for sig_class in eb.MATERIAL_SIGNAL_CLASSES:
        assert eb._is_material_signal("high", sig_class) is True
