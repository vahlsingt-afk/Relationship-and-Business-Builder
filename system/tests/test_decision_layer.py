"""
test_decision_layer.py — Sprint I: Decision Layer tests.

Tests:
  DL1 — attach_decision_layers is callable and exported
  DL2 — act_today items receive a decision field
  DL3 — monitor/ignore items do NOT receive a decision field
  DL4 — decision field is a non-empty string
  DL5 — decision text contains "OR" (binary framing)
  DL6 — convergence_type relationship_activation produces contact-specific decision
  DL7 — convergence_type opportunity_unlock names the blocking condition
  DL8 — attach_decision_layers is idempotent (existing decision not overwritten)
  DL9 — opportunity_board section produces state-specific decisions
  DL10 — connect_the_dots synthesis items receive decisions after compute_synthesis
"""
import sys
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import cos_synthesis as cs


def _item(disposition: str = "act_today", convergence_type: str = "",
          extras=None, **kwargs) -> dict:
    base = {
        "title": "Test item",
        "summary": "Test summary",
        "recommended_action": "Do something",
        "disposition": disposition,
        "extras": {**(extras or {}), "convergence_type": convergence_type},
    }
    base.update(kwargs)
    return base


def _activation_item(contact: str = "Bob Gibson", company: str = "Toast",
                     open_loops: int = 1) -> dict:
    return _item(
        disposition="act_today",
        convergence_type="relationship_activation",
        extras={"convergence_type": "relationship_activation",
                "contact": contact, "company": company,
                "open_loops": open_loops},
    )


def _unlock_item(block: str = "awaiting a response", n: int = 3) -> dict:
    return _item(
        disposition="act_today",
        convergence_type="opportunity_unlock",
        extras={"convergence_type": "opportunity_unlock",
                "block_pattern": block, "opportunity_count": n},
    )


class DL1Export(unittest.TestCase):
    def test_DL1_attach_decision_layers_callable(self):
        self.assertTrue(callable(cs.attach_decision_layers))

    def test_DL1_infer_decision_callable(self):
        self.assertTrue(callable(cs._infer_decision))


class DL2ActTodayReceivesDecision(unittest.TestCase):
    def test_DL2_act_today_item_gets_decision(self):
        items = [_item(disposition="act_today")]
        cs.attach_decision_layers(items)
        self.assertIn("decision", items[0])

    def test_DL2_decision_present_after_attach(self):
        items = [_activation_item()]
        cs.attach_decision_layers(items)
        self.assertIn("decision", items[0])
        self.assertIsInstance(items[0]["decision"], str)


class DL3NonActTodayNoDecision(unittest.TestCase):
    def test_DL3_monitor_item_no_decision(self):
        items = [_item(disposition="monitor")]
        cs.attach_decision_layers(items)
        self.assertNotIn("decision", items[0])

    def test_DL3_ignore_item_no_decision(self):
        items = [_item(disposition="ignore")]
        cs.attach_decision_layers(items)
        self.assertNotIn("decision", items[0])

    def test_DL3_ask_todd_item_no_decision(self):
        items = [_item(disposition="ask_todd")]
        cs.attach_decision_layers(items)
        self.assertNotIn("decision", items[0])


class DL4DecisionIsNonEmptyString(unittest.TestCase):
    def test_DL4_decision_is_string(self):
        items = [_item(disposition="act_today")]
        cs.attach_decision_layers(items)
        self.assertIsInstance(items[0]["decision"], str)

    def test_DL4_decision_is_non_empty(self):
        items = [_item(disposition="act_today")]
        cs.attach_decision_layers(items)
        self.assertGreater(len(items[0]["decision"]), 20)


class DL5DecisionContainsOR(unittest.TestCase):
    def test_DL5_generic_act_today_has_or(self):
        items = [_item(disposition="act_today")]
        cs.attach_decision_layers(items)
        self.assertIn(" OR ", items[0]["decision"])

    def test_DL5_activation_decision_has_or(self):
        items = [_activation_item()]
        cs.attach_decision_layers(items)
        self.assertIn(" OR ", items[0]["decision"])

    def test_DL5_unlock_decision_has_or(self):
        items = [_unlock_item()]
        cs.attach_decision_layers(items)
        self.assertIn(" OR ", items[0]["decision"])


class DL6RelationshipActivationDecision(unittest.TestCase):
    def test_DL6_contact_name_in_decision(self):
        items = [_activation_item(contact="Bob Gibson", company="Toast", open_loops=1)]
        cs.attach_decision_layers(items)
        self.assertIn("Bob Gibson", items[0]["decision"])

    def test_DL6_company_in_decision(self):
        items = [_activation_item(contact="Bob Gibson", company="Toast", open_loops=1)]
        cs.attach_decision_layers(items)
        self.assertIn("Toast", items[0]["decision"])

    def test_DL6_zero_open_loops_different_framing(self):
        items_with = [_activation_item(open_loops=1)]
        items_without = [_activation_item(open_loops=0)]
        cs.attach_decision_layers(items_with)
        cs.attach_decision_layers(items_without)
        # Both should have OR, but different text
        self.assertIn(" OR ", items_with[0]["decision"])
        self.assertIn(" OR ", items_without[0]["decision"])
        # They should be different decisions
        self.assertNotEqual(items_with[0]["decision"], items_without[0]["decision"])


class DL7OpportunityUnlockDecision(unittest.TestCase):
    def test_DL7_block_pattern_in_decision(self):
        items = [_unlock_item(block="awaiting a response", n=3)]
        cs.attach_decision_layers(items)
        self.assertIn("awaiting a response", items[0]["decision"])

    def test_DL7_opportunity_count_in_decision(self):
        items = [_unlock_item(block="follow-up needed", n=4)]
        cs.attach_decision_layers(items)
        # Should reference the count in some way
        self.assertIn("4", items[0]["decision"])


class DL8Idempotent(unittest.TestCase):
    def test_DL8_existing_decision_not_overwritten(self):
        item = _item(disposition="act_today")
        item["decision"] = "ORIGINAL DECISION"
        cs.attach_decision_layers([item])
        self.assertEqual(item["decision"], "ORIGINAL DECISION")

    def test_DL8_no_crash_on_empty_list(self):
        result = cs.attach_decision_layers([])
        self.assertEqual(result, [])

    def test_DL8_returns_same_list(self):
        items = [_item(disposition="act_today")]
        result = cs.attach_decision_layers(items)
        self.assertIs(result, items)


class DL9OpportunityBoardSectionSpecific(unittest.TestCase):
    def test_DL9_waiting_state_produces_follow_up_framing(self):
        item = _item(
            disposition="act_today",
            extras={"state": "WAITING", "companies": "Global Payments"},
        )
        cs.attach_decision_layers([item], section_name="opportunity_board")
        self.assertIn("decision", item)
        decision = item["decision"].lower()
        # Should mention following up or ending the waiting state
        self.assertTrue(
            "follow" in decision or "waiting" in decision or "close" in decision,
            f"Expected follow-up language in: {item['decision']}"
        )

    def test_DL9_active_state_produces_advance_framing(self):
        item = _item(
            disposition="act_today",
            extras={"state": "ACTIVE", "companies": "PAR Technology"},
        )
        cs.attach_decision_layers([item], section_name="opportunity_board")
        self.assertIn("decision", item)
        # Should mention advancing
        decision = item["decision"].lower()
        self.assertTrue(
            "advance" in decision or "next action" in decision or "waiting" in decision,
            f"Expected advance language in: {item['decision']}"
        )


class DL10ComputeSynthesisItemsGetDecision(unittest.TestCase):
    def test_DL10_relationship_activation_items_get_decision_after_attach(self):
        """Pattern 5 items (act_today) should receive decision when attach_decision_layers runs."""
        from contact_index import ContactIndex
        ci = ContactIndex({"contacts": [
            {"id": "bob-gibson", "name": "Bob Gibson", "company": "Toast",
             "company_aliases": [], "loop_count": 2, "open_loop_count": 1,
             "importance": "high", "sources": [], "email_domain": "",
             "thread_ids": [], "open_loops": [], "all_loops": [],
             "last_loop_date": "2026-05-08", "loop_context": "advance role"}
        ], "contact_count": 1})

        sections = {
            "restaurant_industry_headlines": [
                {"title": "Toast raises $200M",
                 "summary": "Toast funding round",
                 "extras": {"entities": ["Toast"]},
                 "disposition": "monitor"}
            ]
        }
        items = cs._relationship_activation(sections)
        # Attach decision layer (simulating what daily_brief does post-CTD)
        cs.attach_decision_layers(items, "connect_the_dots")
        act_items = [i for i in items if i.get("disposition") == "act_today"]
        for item in act_items:
            self.assertIn("decision", item, f"Missing decision on: {item.get('title')}")
            self.assertIn(" OR ", item["decision"])

    def test_DL10_hotzone_items_get_decision(self):
        sections = {
            "last_24h_relationship_signals": [
                {"title": "Signal: Acme", "summary": "Recent activity",
                 "extras": {"contact_name": "John Acme"}}
            ],
            "opportunity_board": [
                {"title": "[ACTIVE] Acme Corp deal",
                 "summary": "active deal with acme",
                 "disposition": "act_today",
                 "extras": {"state": "ACTIVE", "companies": "Acme Corp",
                            "thread_id": "T-acme"}}
            ],
        }
        items = cs._relationship_opportunity_hotzone(sections)
        cs.attach_decision_layers(items, "connect_the_dots")
        for item in items:
            if item.get("disposition") == "act_today":
                self.assertIn("decision", item)
                self.assertIn(" OR ", item["decision"])


if __name__ == "__main__":
    unittest.main()
