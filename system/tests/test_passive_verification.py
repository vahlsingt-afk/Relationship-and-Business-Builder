"""
test_passive_verification.py

Regression coverage: RB-DEFECT-2026-08-12 -- verify_loop's meeting-prep
auto-closure check required the literal phrase "on YYYY-MM-DD" in a loop's
description to find the meeting date. That only matches smart_loops.py's
own auto-generated template; a loop created through a different path
phrased it "Meeting confirmed for Tuesday, 2026-08-11 at 1:00 PM Central"
instead, so the date never matched and the loop kept re-escalating as
overdue a full day after the meeting happened, was recorded via Just Press
Record, and had a full intelligence rollup written for it (loop
L-2026-08-10-001, Jeff Coffland, confirmed live in system/loop_ledger.md).

The fix prefers the prep artifact's own filename date (meeting_prep.py's
YYYY-MM-DD-<slug>.md convention -- structured and reliable) and only falls
back to scanning free-text for "on"/"for" + a date when there's no artifact
path to anchor on.
"""
from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import rb_core as core  # noqa: E402
import passive_verification as pv  # noqa: E402


def _loop(description: str, target: date = date(2026, 8, 11),
          opened: date = date(2026, 8, 10), closed: bool = False) -> core.Loop:
    return core.Loop(
        id="L-TEST-001",
        opened=opened,
        party="Test Party",
        description=description,
        target=target,
        status_raw="open",
        closed=closed,
    )


_EMPTY_PAYLOADS = dict(baseline=[], email_payload={}, interaction_payload={}, calendar_payload={})


class TestMeetingPrepDateExtraction(unittest.TestCase):
    """Uses the real, live Jeff Coffland prep artifact -- it genuinely
    exists at system/meeting_briefs/2026-08-11-jeff-coffland.md, so this
    doubles as a direct regression check against the actual incident."""

    ARTIFACT = "system/meeting_briefs/2026-08-11-jeff-coffland.md"

    def setUp(self):
        if not (core.PROJECT_DIR / self.ARTIFACT).exists():
            self.skipTest(f"{self.ARTIFACT} not present in this checkout")

    def test_non_template_phrasing_now_auto_closes_after_meeting(self):
        """The exact real-world phrasing that broke: 'confirmed for
        Tuesday, YYYY-MM-DD' instead of the template's 'on YYYY-MM-DD'."""
        loop = _loop(
            f"Meeting confirmed for Tuesday, 2026-08-11 at 1:00 PM Central. "
            f"Prep brief created. Closure evidence: meeting occurs and notes "
            f"capture relationship state. Prep: {self.ARTIFACT}."
        )
        proposal = pv.verify_loop(loop, today=date(2026, 8, 12), **_EMPTY_PAYLOADS)
        self.assertIsNotNone(proposal)
        self.assertEqual(proposal.confidence, "auto_closeable")
        self.assertIn("meeting start has passed", proposal.proposed_close_reason)

    def test_standard_template_phrasing_still_works(self):
        """smart_loops.py's own template ('... on YYYY-MM-DD ...') must
        keep working -- this fix must not be a regression for the common case."""
        loop = _loop(
            f"Meeting prep — Catch up on 2026-08-11 with Test Party. "
            f"Closure: prep brief reviewed. Artifact target: {self.ARTIFACT}."
        )
        proposal = pv.verify_loop(loop, today=date(2026, 8, 12), **_EMPTY_PAYLOADS)
        self.assertIsNotNone(proposal)
        self.assertEqual(proposal.confidence, "auto_closeable")

    def test_future_meeting_not_yet_closeable(self):
        """A meeting-prep loop for a meeting that hasn't happened yet must
        not be flagged. Uses an artifact filename with no date prefix (so
        the fallback text-regex path, not the filename date, is exercised)
        pointed at a real existing file, since a real artifact's filename
        date and its description date can never legitimately disagree."""
        with patch.object(pv, "_artifact_exists", return_value=True):
            loop = _loop(
                "Meeting confirmed for Friday, 2099-01-02 at 1:00 PM Central. "
                "Prep brief created. Prep: system/meeting_briefs/jeff-coffland-notes.md."
            )
            proposal = pv.verify_loop(loop, today=date(2026, 8, 12), **_EMPTY_PAYLOADS)
        self.assertIsNone(proposal)

    def test_missing_artifact_not_closeable_even_if_date_passed(self):
        loop = _loop(
            "Meeting confirmed for Tuesday, 2026-08-11 at 1:00 PM Central. "
            "Prep brief created. Prep: system/meeting_briefs/2026-08-11-does-not-exist.md."
        )
        proposal = pv.verify_loop(loop, today=date(2026, 8, 12), **_EMPTY_PAYLOADS)
        self.assertIsNone(proposal)

    def test_already_closed_loop_returns_none(self):
        loop = _loop(
            f"Meeting confirmed for Tuesday, 2026-08-11. Prep: {self.ARTIFACT}.",
            closed=True,
        )
        self.assertIsNone(pv.verify_loop(loop, today=date(2026, 8, 12), **_EMPTY_PAYLOADS))


if __name__ == "__main__":
    unittest.main()
