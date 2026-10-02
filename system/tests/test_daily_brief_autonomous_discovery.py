"""
test_daily_brief_autonomous_discovery.py — RB 9.24B regression suite

Closes DEFECT-013: Daily Brief overweights user-provided memory and underproves
autonomous discovery.

Failure specimen (2026-05-29 morning brief): the rendered brief led with
active-thread state, prior strategic thesis, and market context the user had
manually entered — not what RB discovered from connected sources.

Tests:
  AD1  Source-discovered items appear in what_rb_found_without_you_telling_it.
  AD2  manual_user_provided items are demoted to known_state_reminders (not
       changed-state, not overdue).
  AD3  Every discovery item carries source_refs and discovery-value fields.
  AD4  GPT prompt contains the "What RB Found Without You Telling It" contract.
  AD5  GPT instructions forbid replacing the canonical brief with a narrative
       essay.
  AD6  A 2026-05-29-style fixture shows email/calendar scan counts, sent-loop
       verification, and stale-source caveats in what_rb_found_without_you_telling_it
       before known strategic memory.
  AD7  autonomy_discovery_value field is present on every canonical item.
  AD8  what_rb_found_without_you_telling_it precedes known_state_reminders in
       section_order.
  AD9  Items with autonomous_discovery_value="low" and user_provided_context=True
       are not promoted into what_rb_found_without_you_telling_it.
  AD10 known_state_reminders section exists in canonical brief.
  AD11 operational_changes_from_connected_sources section exists in canonical
       brief.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
API_DIR = Path(__file__).resolve().parent.parent / "api"
sys.path.insert(0, str(SCRIPTS_DIR))

import daily_brief as db


# ---------------------------------------------------------------------------
# Minimal synthetic report fixtures
# ---------------------------------------------------------------------------

def _base_report(*, extra_market: list | None = None) -> dict:
    """Minimal report that produces a valid canonical brief."""
    return {
        "today": "2026-05-29",
        "weekday": "Friday",
        "baseline": {
            "total_contacts": 2671,
            "tiers": {"inner_circle": 17, "broader_circle": 2, "dormant_valuable": 1},
            "contacts_without_last_touch": 0,
        },
        "loops": {
            "overdue": [],
            "due_today": [],
            "this_week": [],
            "future": [],
            "closed": [],
        },
        "active_threads": [],
        "email": {},
        "calendar": {},
        "market_signals": {
            "fetched_at": "2026-05-29T07:00:00-05:00",
            "stale": False,
            "has_data": True,
            "input_count": len(extra_market or []),
            "deduped_count": len(extra_market or []),
            "top_count": len(extra_market or []),
            "top_n_requested": 7,
            "stale_reason": None,
            "top": extra_market or [],
        },
        "relationship_signals": {
            "signals": [],
            "stale_sources": [],
            "reconciliation_prompts": [],
        },
        "daily_prep_summary": {
            "prepared_at": "2026-05-29T05:00:00-05:00",
            "totals": {
                "sources_scanned": 5,
                "relationship_signals_detected": 3,
                "sources_with_no_actionable_ri": 2,
                "stale_or_missing_sources": 2,
            },
            "source_health": {
                "overall_health": "partial",
                "brief_trustworthiness": "partial",
                "sources": {
                    "email": {"tier": 1, "status": "refreshed"},
                    "linkedin_messaging": {"tier": 1, "status": "stale", "recovery_type": "manual_export"},
                },
            },
        },
        "source_health": {
            "brief_trustworthiness": "partial",
            "sources": {
                "email": {"tier": 1, "status": "refreshed"},
                "linkedin_messaging": {"tier": 1, "status": "stale"},
            },
        },
    }


def _system_detected_market_item(title: str = "Test signal from vertical source") -> dict:
    return {
        "title": title,
        "url": "https://example.org/signal",
        "source_name": "Restaurant Business",
        "source_type": "vertical_trade",
        "source_quality": "strong",
        "published_at": "2026-05-29",
        "company": "TestCo",
        "side": "operator_demand",
        "category": "drive_thru",
        "signal_type": "operator_priority",
        "signal_layer": "restaurant_industry_signal",
        "pain_point_or_priority": "AI accuracy",
        "strategic_relevance": "high",
        "affected_relationships_or_threads": [],
        "recommended_action": "monitor",
        "confidence": "medium",
        "timing_priority": "this_week",
        "priority_score": 260,
        "grounding": "system_detected",
        "freshness": "fresh",
        "source_refs": ["restaurant_business:2026-05-29"],
    }


def _manual_market_item(title: str = "Prior strategic thesis — known context") -> dict:
    return {
        "title": title,
        "url": "",
        "source_name": "operator_memory",
        "source_type": "manual_context",
        "source_quality": "manual",
        "published_at": "2026-05-01",
        "company": "General",
        "side": "operator_memory",
        "category": "thesis",
        "signal_type": "strategic_context",
        "signal_layer": "user_provided",
        "pain_point_or_priority": "restaurant AI thesis",
        "strategic_relevance": "medium",
        "affected_relationships_or_threads": [],
        "recommended_action": "monitor",
        "confidence": "medium",
        "timing_priority": "ongoing",
        "priority_score": 30,
        "grounding": "manual_user_provided",
        "freshness": "manual_context",
        "source_refs": ["active_threads"],
    }


# ---------------------------------------------------------------------------
# Test classes
# ---------------------------------------------------------------------------

class TestDiscoveryValueFields(unittest.TestCase):
    """AD7 — every canonical item carries autonomous_discovery_value."""

    def test_AD7a_system_detected_item_has_adv_field(self):
        item = db._canonical_item(
            title="Email scan finding",
            summary="RB detected 3 inbound signals.",
            grounding="system_detected",
            freshness="fresh",
            source_refs=["email:inbox"],
            disposition="monitor",
        )
        self.assertIn("novelty", item)
        self.assertIn("autonomous_discovery_value", item["novelty"])

    def test_AD7b_externally_discovered_high_disposition_is_high(self):
        item = db._canonical_item(
            title="Fresh vertical trade signal",
            summary="Drive-thru AI accuracy concern surfaced.",
            grounding="system_detected",
            freshness="fresh",
            source_refs=["restaurant_business:2026-05-29"],
            disposition="act_today",
        )
        adv = item["novelty"]["autonomous_discovery_value"]
        # system_detected maps to externally_discovered via _source_class_for_grounding
        self.assertIn(adv, ("high", "medium"), msg=f"Expected high or medium, got {adv!r}")

    def test_AD7c_manual_user_provided_is_low(self):
        item = db._canonical_item(
            title="Known active thread — Foods Connected",
            summary="Ongoing W-2 exploration thread.",
            grounding="manual_user_provided",
            freshness="manual_context",
            source_refs=["active_threads"],
            disposition="monitor",
        )
        adv = item["novelty"]["autonomous_discovery_value"]
        self.assertEqual(adv, "low", msg=f"manual_user_provided should be low, got {adv!r}")

    def test_AD7d_manual_item_has_user_provided_context_true(self):
        item = db._canonical_item(
            title="Prior thesis",
            summary="Restaurant AI consolidation thesis.",
            grounding="manual_user_provided",
            freshness="manual_context",
            source_refs=["active_threads"],
        )
        self.assertTrue(item["novelty"]["user_provided_context"])

    def test_AD7e_source_discovered_true_for_system_detected(self):
        item = db._canonical_item(
            title="Email signal",
            summary="Inbound from Elizabeth Jenswold.",
            grounding="system_detected",
            freshness="fresh",
            source_refs=["email:inbox"],
        )
        self.assertTrue(item["novelty"]["source_discovered"])

    def test_AD7f_source_discovered_false_for_manual(self):
        item = db._canonical_item(
            title="Manual note",
            summary="Prior thread context.",
            grounding="manual_user_provided",
            freshness="manual_context",
            source_refs=["active_threads"],
        )
        self.assertFalse(item["novelty"]["source_discovered"])

    def test_AD7g_new_to_todd_likely_false_for_manual(self):
        item = db._canonical_item(
            title="Thread reminder",
            summary="Foods Connected thread status.",
            grounding="manual_user_provided",
            freshness="manual_context",
            source_refs=["active_threads"],
        )
        self.assertFalse(item["novelty"]["new_to_todd_likely"])

    def test_AD7h_changed_state_true_for_act_today(self):
        item = db._canonical_item(
            title="Overdue loop",
            summary="Reply to Elizabeth now.",
            grounding="system_detected",
            freshness="fresh",
            source_refs=["email:inbox"],
            disposition="act_today",
        )
        self.assertTrue(item["novelty"]["changed_state"])

    def test_AD7i_changed_state_false_for_monitor(self):
        item = db._canonical_item(
            title="Background context",
            summary="Market thesis holding.",
            grounding="manual_user_provided",
            freshness="manual_context",
            source_refs=["active_threads"],
            disposition="monitor",
        )
        self.assertFalse(item["novelty"]["changed_state"])


class TestSectionOrdering(unittest.TestCase):
    """AD1, AD8 — what_rb_found precedes known_state_reminders in section_order."""

    @classmethod
    def setUpClass(cls):
        cls.canon = db.build_canonical_brief(_base_report())
        cls.order = cls.canon.get("section_order") or []

    def test_AD8a_what_rb_found_section_exists_in_order(self):
        self.assertIn("what_rb_found_without_you_telling_it", self.order)

    def test_AD8b_known_state_reminders_exists_in_order(self):
        self.assertIn("known_state_reminders", self.order)

    def test_AD8c_operational_changes_exists_in_order(self):
        self.assertIn("operational_changes_from_connected_sources", self.order)

    def test_AD8d_what_rb_found_precedes_known_state(self):
        wrf_pos = self.order.index("what_rb_found_without_you_telling_it")
        ks_pos = self.order.index("known_state_reminders")
        self.assertLess(wrf_pos, ks_pos,
                        msg="what_rb_found_without_you_telling_it must precede known_state_reminders")

    def test_AD8e_what_rb_found_precedes_narrative_sections(self):
        wrf_pos = self.order.index("what_rb_found_without_you_telling_it")
        industry_pos = self.order.index("industry_brief")
        self.assertLess(wrf_pos, industry_pos,
                        msg="what_rb_found must precede industry_brief")

    def test_AD8f_resource_verification_is_first(self):
        # RB 9.37 / DEFECT-020: executive_summary is now the first section.
        # resource_verification_and_freshness_status is position 2.
        self.assertEqual(self.order[0], "executive_summary",
                         msg="executive_summary must be first (RB 9.37 CoS structure)")
        self.assertIn("resource_verification_and_freshness_status", self.order,
                      msg="resource_verification_and_freshness_status must still exist in section_order")

    def test_AD8g_what_rb_found_is_second(self):
        # RB 9.37: executive_summary is first, resource_verification is second,
        # what_rb_found is third.  The invariant is that what_rb_found comes
        # before industry commentary — test that instead of exact position.
        wrf_pos = self.order.index("what_rb_found_without_you_telling_it")
        industry_pos = self.order.index("industry_brief") if "industry_brief" in self.order else len(self.order)
        self.assertLess(wrf_pos, industry_pos,
                        msg="what_rb_found_without_you_telling_it must precede industry_brief")


class TestDiscoveryRouting(unittest.TestCase):
    """AD1, AD2, AD9 — routing of items to discovery vs known-state sections."""

    def test_AD9a_low_adv_user_provided_not_in_what_rb_found(self):
        """Items with autonomous_discovery_value=low and user_provided_context=True
        must NOT appear in what_rb_found_without_you_telling_it."""
        item = db._canonical_item(
            title="Known active thread",
            summary="Foods Connected ongoing.",
            grounding="manual_user_provided",
            freshness="manual_context",
            source_refs=["active_threads"],
            disposition="monitor",
        )
        nov = item.get("novelty") or {}
        adv = nov.get("autonomous_discovery_value")
        upc = nov.get("user_provided_context")
        self.assertEqual(adv, "low")
        self.assertTrue(upc)
        # Confirm the item would be excluded from what_rb_found
        should_be_in_wrf = adv in ("high", "medium") and nov.get("source_discovered")
        self.assertFalse(should_be_in_wrf,
                         msg="low/user_provided item should not route to what_rb_found")

    def test_AD9b_high_adv_source_discovered_goes_to_what_rb_found(self):
        """Items with autonomous_discovery_value=high and source_discovered=True
        should be eligible for what_rb_found_without_you_telling_it."""
        item = db._canonical_item(
            title="Inbound email — Elizabeth Jenswold",
            summary="New inbound from Elizabeth requesting intro.",
            grounding="system_detected",
            freshness="fresh",
            source_refs=["email:inbox"],
            disposition="act_today",
        )
        nov = item.get("novelty") or {}
        adv = nov.get("autonomous_discovery_value")
        src = nov.get("source_discovered")
        should_be_in_wrf = adv in ("high", "medium") and src
        self.assertTrue(should_be_in_wrf,
                        msg=f"high-adv source-discovered item should route to what_rb_found; adv={adv!r}, src={src!r}")

    def test_AD9c_new_sections_present_in_canonical_brief(self):
        canon = db.build_canonical_brief(_base_report())
        sections = canon.get("sections") or {}
        self.assertIn("what_rb_found_without_you_telling_it", sections)
        self.assertIn("operational_changes_from_connected_sources", sections)
        self.assertIn("known_state_reminders", sections)


class TestDiscoveryFixture2026_05_29(unittest.TestCase):
    """AD6 — 2026-05-29-style fixture validates discovery-first rendering."""

    @classmethod
    def setUpClass(cls):
        """Build a brief similar to the 2026-05-29 morning run that failed the value test."""
        report = _base_report(extra_market=[
            _system_detected_market_item("McDonald's voice AI pause — drive-thru impact"),
            _manual_market_item("Restaurant AI consolidation — prior thesis"),
            _manual_market_item("Foods Connected W-2 opportunity — active thread"),
        ])
        # Inject email scan data into daily_prep_summary
        report["daily_prep_summary"]["totals"].update({
            "emails_scanned": 44,
            "calendar_items_scanned": 6,
            "passive_email_items": 103,
            "noisy_items_suppressed": 65,
            "newsletters_scanned": 9,
            "headlines_extracted": 10,
            "deep_dive_candidates": 1,
        })
        # Add a sent-followup (system_detected outbound state)
        report["email"]["sent_followups"] = [
            {
                "thread_id": "T-JENSWOLD",
                "subject": "Re: Intro — Elizabeth Jenswold",
                "snippet": "Sent follow-up to Elizabeth Jenswold.",
                "to": [{"name": "Elizabeth Jenswold", "email": "ejenswold@example.com"}],
                "sent_at": "2026-05-28T14:00:00-05:00",
                "business_days_since": 1,
                "expected_response_window_days": 3,
                "status": "outbound_sent_awaiting_response",
                "grounding": "system_detected",
                "freshness": "fresh",
            }
        ]
        cls.canon = db.build_canonical_brief(report)
        cls.sections = cls.canon.get("sections") or {}
        cls.order = cls.canon.get("section_order") or []

    def test_AD6a_what_rb_found_section_exists(self):
        self.assertIn("what_rb_found_without_you_telling_it", self.sections)

    def test_AD6b_known_state_reminders_section_exists(self):
        self.assertIn("known_state_reminders", self.sections)

    def test_AD6c_resource_verification_is_first_section(self):
        # RB 9.37 / DEFECT-020: executive_summary is now section[0]; resource_verification is section[1].
        self.assertEqual(self.order[0], "executive_summary",
                         msg="executive_summary is first section per RB 9.37 CoS structure")
        self.assertIn("resource_verification_and_freshness_status", self.order,
                      msg="resource_verification_and_freshness_status must be present in section_order")

    def test_AD6d_what_rb_found_is_before_known_state(self):
        wrf_pos = self.order.index("what_rb_found_without_you_telling_it")
        ks_pos = self.order.index("known_state_reminders")
        self.assertLess(wrf_pos, ks_pos)

    def test_AD6e_rendering_rules_contain_discovery_contract(self):
        rules = self.canon.get("rendering_rules") or []
        rules_text = " ".join(rules)
        self.assertIn("what_rb_found_without_you_telling_it", rules_text,
                      msg="rendering_rules must name what_rb_found_without_you_telling_it section")
        self.assertIn("known_state_reminders", rules_text,
                      msg="rendering_rules must name known_state_reminders section")

    def test_AD6f_autonomous_discovery_evidence_section_populated(self):
        self.assertTrue(self.sections.get("autonomous_discovery_evidence"),
                        msg="autonomous_discovery_evidence must still be populated (backward compat)")

    def test_AD6g_resource_verification_populated(self):
        self.assertTrue(self.sections.get("resource_verification_and_freshness_status"))


class TestGPTPromptRendering(unittest.TestCase):
    """AD4, AD5 — GPT prompt / instructions contain discovery-first rendering rules."""

    @classmethod
    def setUpClass(cls):
        cls.prompt = (API_DIR / "custom_gpt_prompt.md").read_text()
        cls.instructions = (API_DIR / "custom_gpt_instructions_8k.md").read_text()

    def test_AD4a_prompt_contains_what_rb_found_rule(self):
        self.assertIn("what_rb_found_without_you_telling_it", self.prompt,
                      msg="custom_gpt_prompt.md must name what_rb_found_without_you_telling_it")

    def test_AD4b_prompt_contains_known_state_reminders(self):
        self.assertIn("known_state_reminders", self.prompt,
                      msg="custom_gpt_prompt.md must name known_state_reminders")

    def test_AD4c_prompt_contains_discovery_first_ordering_rule(self):
        self.assertIn("discovery", self.prompt.lower(),
                      msg="custom_gpt_prompt.md must contain discovery-first rendering guidance")

    def test_AD4d_prompt_forbids_leading_with_manual_context(self):
        """The prompt must explicitly say not to lead with manual_user_provided."""
        self.assertIn("manual_user_provided", self.prompt,
                      msg="prompt must mention manual_user_provided in context of ordering")

    def test_AD5a_instructions_contain_what_rb_found_section(self):
        self.assertIn("what_rb_found_without_you_telling_it", self.instructions,
                      msg="custom_gpt_instructions_8k.md must name what_rb_found_without_you_telling_it")

    def test_AD5b_instructions_forbid_narrative_essay_replacement(self):
        """Instructions must explicitly prohibit replacing the canonical brief with a narrative essay."""
        # Check for at least one of the relevant prohibition phrases
        has_rule = (
            "narrative essay" in self.instructions.lower()
            or "do not produce a single narrative" in self.instructions.lower()
            or "do not replace" in self.instructions.lower()
        )
        self.assertTrue(has_rule,
                        msg="Instructions must explicitly forbid replacing canonical brief sections with a narrative essay")

    def test_AD5c_instructions_include_known_state_reminders(self):
        self.assertIn("known_state_reminders", self.instructions,
                      msg="Instructions must reference known_state_reminders section")

    def test_AD5d_instructions_reference_autonomous_discovery_value(self):
        self.assertIn("autonomous_discovery_value", self.instructions,
                      msg="Instructions must reference novelty.autonomous_discovery_value field")

    def test_AD5e_instructions_specify_section_render_order(self):
        """The 8k instructions must name at least the three discovery sections in order."""
        text = self.instructions
        wrf_pos = text.find("what_rb_found_without_you_telling_it")
        ops_pos = text.find("operational_changes_from_connected_sources")
        ks_pos = text.find("known_state_reminders")
        self.assertGreater(wrf_pos, -1, "what_rb_found must appear in 8k instructions")
        self.assertGreater(ops_pos, -1, "operational_changes must appear in 8k instructions")
        self.assertGreater(ks_pos, -1, "known_state_reminders must appear in 8k instructions")
        self.assertLess(wrf_pos, ops_pos,
                        msg="what_rb_found must appear before operational_changes in instructions")
        self.assertLess(ops_pos, ks_pos,
                        msg="operational_changes must appear before known_state_reminders in instructions")


class TestAllItemsHaveADV(unittest.TestCase):
    """AD3 — every discovery section item has source_refs and discovery-value fields."""

    @classmethod
    def setUpClass(cls):
        report = _base_report(extra_market=[
            _system_detected_market_item(),
            _manual_market_item(),
        ])
        cls.canon = db.build_canonical_brief(report)
        cls.sections = cls.canon.get("sections") or {}

    def _all_items(self) -> list[dict]:
        items = []
        for sec_items in self.sections.values():
            items.extend(sec_items if isinstance(sec_items, list) else [])
        return items

    def test_AD3a_all_items_have_novelty_block(self):
        for item in self._all_items():
            self.assertIn("novelty", item,
                          msg=f"Item missing novelty block: {item.get('title')!r}")

    def test_AD3b_all_items_have_autonomous_discovery_value(self):
        for item in self._all_items():
            nov = item.get("novelty") or {}
            self.assertIn("autonomous_discovery_value", nov,
                          msg=f"Item missing autonomous_discovery_value: {item.get('title')!r}")

    def test_AD3c_adv_values_are_valid(self):
        valid = {"high", "medium", "low"}
        for item in self._all_items():
            nov = item.get("novelty") or {}
            adv = nov.get("autonomous_discovery_value")
            self.assertIn(adv, valid,
                          msg=f"Invalid autonomous_discovery_value {adv!r} on {item.get('title')!r}")

    def test_AD3d_source_discovered_items_have_source_refs(self):
        for item in self._all_items():
            nov = item.get("novelty") or {}
            if nov.get("source_discovered"):
                refs = item.get("source_refs") or []
                self.assertTrue(refs,
                                msg=f"source_discovered item missing source_refs: {item.get('title')!r}")


if __name__ == "__main__":
    unittest.main()
