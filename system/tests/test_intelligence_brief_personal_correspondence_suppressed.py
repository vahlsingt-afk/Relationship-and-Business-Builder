"""
test_intelligence_brief_personal_correspondence_suppressed.py

RB-DEFECT-2026-07-10: the Intelligence Brief (Part 1 -- a "newspaper," per
the established doctrine that it "does not connect signals to the user
personally") rendered genuinely personal correspondence in "What Changed
Today" -- e.g. a "Personal correspondence" bullet whose snippet was a legal
disclaimer footer ("NOTICE: This email message is for the sole use of...").
_correspondence_label (daily_brief.py) already distinguishes business/
colleague correspondence from genuinely personal mail via a baseline lookup;
this fix makes the Intelligence Brief's renderer respect that distinction --
items labeled "Personal correspondence" (the generic non-business fallback)
are suppressed entirely, while "Business correspondence (...)"/"Colleague
correspondence (...)"/"Introduction" items -- genuinely business-relevant --
stay visible.
"""
from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import render_intelligence_brief as rib  # noqa: E402


def _correspondence_item(title: str, label: str, summary: str = "") -> dict:
    return {
        "title": title,
        "summary": summary,
        "extras": {"delta_source": "personal_correspondence", "correspondence_label": label},
    }


class TestPersonalCorrespondenceSuppressedFromIntelligenceBrief(unittest.TestCase):
    def test_generic_personal_correspondence_suppressed(self):
        sections = {"personal_intelligence_delta": [
            _correspondence_item(
                'Personal correspondence: "Dinner this weekend?" — Mary Vahlsing',
                "Personal correspondence",
                summary="NOTICE: This email message is for the sole use of the intended recipient.",
            ),
        ]}
        out = rib._render_what_changed(sections, date(2026, 7, 10))
        self.assertIn("No material changes", out)
        self.assertNotIn("Mary Vahlsing", out)
        self.assertNotIn("NOTICE: This email message", out)

    def test_business_correspondence_still_shown(self):
        sections = {"personal_intelligence_delta": [
            _correspondence_item(
                'Business correspondence (ZagOps): "Growth round now open" — Saverio Ferraro',
                "Business correspondence (ZagOps)",
                summary="Opening up our growth round.",
            ),
        ]}
        out = rib._render_what_changed(sections, date(2026, 7, 10))
        self.assertIn("Saverio Ferraro", out)

    def test_colleague_correspondence_still_shown(self):
        sections = {"personal_intelligence_delta": [
            _correspondence_item(
                'Colleague correspondence (Global Payments Inc.): "Territory sync" — Ryan Hildebrand',
                "Colleague correspondence (Global Payments Inc.)",
                summary="Let's sync on the territory plan.",
            ),
        ]}
        out = rib._render_what_changed(sections, date(2026, 7, 10))
        self.assertIn("Ryan Hildebrand", out)

    def test_introduction_still_shown(self):
        sections = {"personal_intelligence_delta": [
            _correspondence_item(
                'Introduction: "Meet Jane" — David Drinan',
                "Introduction",
                summary="Introducing you to Jane.",
            ),
        ]}
        out = rib._render_what_changed(sections, date(2026, 7, 10))
        self.assertIn("David Drinan", out)


if __name__ == "__main__":
    unittest.main()
