"""
test_entity_consolidation.py — Sprint J: Entity Consolidation tests.

Tests:
  EC1 — _extract_entities_from_item is callable; returns set
  EC2 — extracts from extras.entities.companies
  EC3 — extracts from extras.companies string (comma-separated)
  EC4 — extracts from extras.contact_name
  EC5 — _compute_entity_spotlight is callable
  EC6 — entity in 3+ sections → spotlight item created
  EC7 — entity in 2 sections → no spotlight item
  EC8 — entity in 3+ sections including opportunity_board → act_today disposition
  EC9 — entity in 3+ sections without high-priority sections → monitor disposition
  EC10 — spotlight item has all required schema fields
  EC11 — contributing items are tagged with extras.entity_spotlight
  EC12 — _compute_entity_spotlight is idempotent (second call returns same)
  EC13 — max 5 spotlight items regardless of how many entities qualify
  EC14 — same entity appearing in same section 5x only counts as 1 section
  EC15 — entity_spotlight extras contain entity, section_count, signal_count, sections
"""
import sys
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from daily_brief import _extract_entities_from_item, _compute_entity_spotlight


def _item(title="Test item", extras=None, disposition="monitor") -> dict:
    return {
        "title": title,
        "summary": "Summary",
        "disposition": disposition,
        "extras": extras or {},
    }


def _item_with_company(co: str, title=None) -> dict:
    return _item(title=title or f"Item about {co}",
                 extras={"companies": co})


def _item_with_contact(contact: str, title=None) -> dict:
    return _item(title=title or f"Item about {contact}",
                 extras={"contact_name": contact})


def _item_with_nested_entities(companies=None, people=None, title=None) -> dict:
    return _item(title=title or "Nested entity item",
                 extras={"entities": {
                     "companies": companies or [],
                     "people": people or [],
                 }})


def _sections_with_entity_in_n_sections(entity: str, n: int,
                                        include_opportunity_board=False) -> dict:
    """Build a sections dict with `entity` appearing in exactly n distinct sections."""
    section_names = [
        "restaurant_industry_headlines",
        "restaurant_technology_headlines",
        "last_24h_relationship_signals",
        "email_intelligence_harvest",
        "watchlist_intelligence",
    ]
    if include_opportunity_board:
        section_names = ["opportunity_board"] + section_names

    sections = {}
    for sec in section_names[:n]:
        sections[sec] = [_item_with_company(entity, title=f"{entity} in {sec}")]
    return sections


class EC1Callable(unittest.TestCase):
    def test_EC1_extract_callable(self):
        self.assertTrue(callable(_extract_entities_from_item))

    def test_EC1_returns_set(self):
        result = _extract_entities_from_item(_item())
        self.assertIsInstance(result, set)

    def test_EC1_compute_spotlight_callable(self):
        self.assertTrue(callable(_compute_entity_spotlight))


class EC2ExtractNestedCompanies(unittest.TestCase):
    def test_EC2_companies_from_nested_entities(self):
        item = _item_with_nested_entities(companies=["Toast", "PAR Technology"])
        entities = _extract_entities_from_item(item)
        self.assertIn("toast", entities)
        self.assertIn("par technology", entities)

    def test_EC2_people_from_nested_entities(self):
        item = _item_with_nested_entities(people=["John Smith"])
        entities = _extract_entities_from_item(item)
        self.assertIn("john smith", entities)


class EC3ExtractCompaniesString(unittest.TestCase):
    def test_EC3_comma_separated_companies(self):
        item = _item(extras={"companies": "Toast, Global Payments, PAR Technology"})
        entities = _extract_entities_from_item(item)
        self.assertIn("toast", entities)
        self.assertIn("global payments", entities)
        self.assertIn("par technology", entities)

    def test_EC3_single_company_string(self):
        item = _item(extras={"companies": "Olo"})
        entities = _extract_entities_from_item(item)
        self.assertIn("olo", entities)


class EC4ExtractContactName(unittest.TestCase):
    def test_EC4_contact_name(self):
        item = _item(extras={"contact_name": "Bob Gibson"})
        entities = _extract_entities_from_item(item)
        self.assertIn("bob gibson", entities)

    def test_EC4_contact_field(self):
        item = _item(extras={"contact": "Alice Chen"})
        entities = _extract_entities_from_item(item)
        self.assertIn("alice chen", entities)


class EC6SpotlightForThreeSections(unittest.TestCase):
    def test_EC6_entity_in_3_sections_creates_spotlight(self):
        sections = _sections_with_entity_in_n_sections("Toast", 3)
        result = _compute_entity_spotlight(sections)
        self.assertGreater(len(result), 0)

    def test_EC6_spotlight_title_contains_entity(self):
        sections = _sections_with_entity_in_n_sections("Toast", 3)
        result = _compute_entity_spotlight(sections)
        self.assertIn("Toast", result[0]["title"])

    def test_EC6_four_sections_also_triggers(self):
        sections = _sections_with_entity_in_n_sections("Olo", 4)
        result = _compute_entity_spotlight(sections)
        self.assertGreater(len(result), 0)


class EC7NoSpotlightForTwoSections(unittest.TestCase):
    def test_EC7_entity_in_2_sections_no_spotlight(self):
        sections = _sections_with_entity_in_n_sections("Lightspeed", 2)
        result = _compute_entity_spotlight(sections)
        self.assertEqual(len(result), 0)

    def test_EC7_empty_sections_no_spotlight(self):
        result = _compute_entity_spotlight({})
        self.assertEqual(result, [])


class EC8ActTodayForHighPrioritySections(unittest.TestCase):
    def test_EC8_opportunity_board_triggers_act_today(self):
        sections = _sections_with_entity_in_n_sections(
            "Global Payments", 3, include_opportunity_board=True
        )
        result = _compute_entity_spotlight(sections)
        self.assertTrue(any(i["disposition"] == "act_today" for i in result))

    def test_EC8_last_24h_relationship_signals_triggers_act_today(self):
        sections = {
            "last_24h_relationship_signals": [_item_with_company("Olo", "Olo signal")],
            "restaurant_industry_headlines": [_item_with_company("Olo", "Olo headline")],
            "watchlist_intelligence": [_item_with_company("Olo", "Olo watchlist")],
        }
        result = _compute_entity_spotlight(sections)
        act_items = [i for i in result if "olo" in i["title"].lower()]
        self.assertTrue(any(i["disposition"] == "act_today" for i in act_items))


class EC9MonitorDispositionWithoutHighPriority(unittest.TestCase):
    def test_EC9_no_high_priority_sections_is_monitor(self):
        sections = {
            "restaurant_industry_headlines": [_item_with_company("Toast", "Toast news 1")],
            "restaurant_technology_headlines": [_item_with_company("Toast", "Toast news 2")],
            "watchlist_intelligence": [_item_with_company("Toast", "Toast watchlist")],
        }
        result = _compute_entity_spotlight(sections)
        toast_items = [i for i in result if "toast" in i["title"].lower()]
        self.assertTrue(all(i["disposition"] == "monitor" for i in toast_items))


class EC10SpotlightItemSchema(unittest.TestCase):
    def test_EC10_required_fields_present(self):
        sections = _sections_with_entity_in_n_sections("Olo", 3)
        result = _compute_entity_spotlight(sections)
        self.assertGreater(len(result), 0)
        item = result[0]
        for field in ("title", "summary", "why_it_matters", "recommended_action",
                      "disposition", "grounding", "confidence", "source_refs", "extras"):
            self.assertIn(field, item, f"Missing field: {field}")

    def test_EC10_source_refs_is_list(self):
        sections = _sections_with_entity_in_n_sections("Olo", 3)
        result = _compute_entity_spotlight(sections)
        self.assertIsInstance(result[0]["source_refs"], list)
        self.assertGreater(len(result[0]["source_refs"]), 0)


class EC11TaggingContributingItems(unittest.TestCase):
    def test_EC11_contributing_items_tagged(self):
        toast_item = _item_with_company("Toast", "Toast news")
        sections = {
            "restaurant_industry_headlines": [toast_item],
            "restaurant_technology_headlines": [_item_with_company("Toast", "Toast tech")],
            "watchlist_intelligence": [_item_with_company("Toast", "Toast watchlist")],
        }
        _compute_entity_spotlight(sections)
        self.assertIn("entity_spotlight", toast_item.get("extras", {}))

    def test_EC11_tagged_with_entity_name_lowercase(self):
        toast_item = _item_with_company("Toast", "Toast news")
        sections = {
            "restaurant_industry_headlines": [toast_item],
            "restaurant_technology_headlines": [_item_with_company("Toast", "Toast tech")],
            "watchlist_intelligence": [_item_with_company("Toast", "Toast watchlist")],
        }
        _compute_entity_spotlight(sections)
        self.assertEqual(toast_item["extras"]["entity_spotlight"], "toast")


class EC12Idempotent(unittest.TestCase):
    def test_EC12_second_call_same_result(self):
        sections = _sections_with_entity_in_n_sections("Olo", 3)
        result1 = _compute_entity_spotlight(sections)
        result2 = _compute_entity_spotlight(sections)
        self.assertEqual(len(result1), len(result2))
        if result1:
            self.assertEqual(result1[0]["title"], result2[0]["title"])


class EC13MaxFiveItems(unittest.TestCase):
    def test_EC13_caps_at_five_spotlight_items(self):
        # Create 7 entities each appearing in 3 sections
        sections = {}
        entities = ["Toast", "Olo", "PAR Technology", "Revel", "Lightspeed", "SpotOn", "Qu"]
        for entity in entities:
            sections[f"restaurant_industry_headlines_{entity}"] = None  # won't be scanned
        # Use real scan sections for 3 of them per entity
        scan_secs = [
            "restaurant_industry_headlines",
            "restaurant_technology_headlines",
            "watchlist_intelligence",
            "email_intelligence_harvest",
            "last_24h_relationship_signals",
        ]
        # Build a single shared sections dict with multiple entities per section
        for sec in scan_secs:
            sections[sec] = [_item_with_company(e, f"{e} in {sec}") for e in entities]

        result = _compute_entity_spotlight(sections)
        self.assertLessEqual(len(result), 5)


class EC14SameEntitySameSectionCountsOnce(unittest.TestCase):
    def test_EC14_repeated_entity_in_same_section_counts_as_one(self):
        # 5 items all with "Toast" in the same section → still counts as 1 section
        sections = {
            "restaurant_industry_headlines": [
                _item_with_company("Toast", f"Toast {i}") for i in range(5)
            ],
            # Only one other section — not enough for spotlight (need 3+)
            "watchlist_intelligence": [_item_with_company("Toast", "Toast watchlist")],
        }
        result = _compute_entity_spotlight(sections)
        # Only 2 sections → no spotlight
        self.assertEqual(len(result), 0)


class EC15ExtrasFields(unittest.TestCase):
    def test_EC15_extras_has_entity(self):
        sections = _sections_with_entity_in_n_sections("Olo", 3)
        result = _compute_entity_spotlight(sections)
        self.assertIn("entity", result[0]["extras"])
        self.assertEqual(result[0]["extras"]["entity"], "olo")

    def test_EC15_extras_has_section_count(self):
        sections = _sections_with_entity_in_n_sections("Olo", 3)
        result = _compute_entity_spotlight(sections)
        self.assertEqual(result[0]["extras"]["section_count"], 3)

    def test_EC15_extras_has_signal_count(self):
        sections = _sections_with_entity_in_n_sections("Olo", 3)
        result = _compute_entity_spotlight(sections)
        self.assertGreaterEqual(result[0]["extras"]["signal_count"], 3)

    def test_EC15_extras_has_sections_list(self):
        sections = _sections_with_entity_in_n_sections("Olo", 3)
        result = _compute_entity_spotlight(sections)
        self.assertIsInstance(result[0]["extras"]["sections"], list)
        self.assertEqual(len(result[0]["extras"]["sections"]), 3)


if __name__ == "__main__":
    unittest.main()
