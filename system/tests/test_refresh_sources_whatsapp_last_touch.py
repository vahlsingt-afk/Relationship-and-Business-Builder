"""
test_refresh_sources_whatsapp_last_touch.py

RB-DEFECT-2026-09-18 follow-up: whatsapp_ingest.py already matches chat
participants to baseline contacts, classifies each chat
(strategic_relationship/strategic_group/personal/unknown), and computes
last_message_at per chat -- but nothing ever consumed any of it. Its own
`mutations` list (one `whatsapp_channel_confirm` entry per matched
participant) was built and then discarded; last_message_at never advanced a
matched contact's last_touch. whatsapp_ingest_scan runs --confirm in the
scheduled pipeline, but --confirm here only gated whether the chat-
intelligence CACHE got written (system/.cache/whatsapp_chats.json), not any
canonical baseline write -- there wasn't one.

Fixed by adding WhatsApp as a 5th candidate source to refresh_sources.
apply_cross_source_last_touch(), reusing that function's already-tested
"most-recent-candidate-wins, never overwrite operator-confirmed" machinery
(system/tests/test_refresh_sources_cross_source_last_touch_gp.py) instead of
duplicating last-touch-reconciliation logic a third time. Reads whatsapp_
ingest.py's already-resolved matched_baseline/last_message_at fields
directly from the chat cache -- no re-implementation of chat-participant
name/phone matching here.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import refresh_sources as rs  # noqa: E402
import rb_core as core  # noqa: E402


def _chat(chat_name, classification, matched_baseline, last_message_at) -> dict:
    return {
        "chat_name": chat_name,
        "classification": classification,
        "matched_baseline": matched_baseline,
        "last_message_at": last_message_at,
    }


class TestWhatsAppLastTouch(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="refresh_sources_whatsapp_test_"))
        self.baseline = [{
            "id": "jane-procurement",
            "name": "Jane Procurement",
            "signal_class": "RC",
            "last_touch": "2026-08-01",
            "notes": "",
        }]

    def _write_whatsapp_cache(self, chats: list[dict]) -> None:
        (self.tmp / "whatsapp_chats.json").write_text(
            json.dumps({"generated_at": "2026-09-18T00:00:00+00:00", "chats": chats}),
            encoding="utf-8",
        )

    def test_strategic_chat_advances_last_touch(self):
        self._write_whatsapp_cache([
            _chat("Jane Procurement", "strategic_relationship",
                  [{"id": "jane-procurement", "name": "Jane Procurement"}], "2026-09-16"),
        ])
        with patch.object(core, "INBOX_DIR", self.tmp), \
             patch.object(core, "CACHE_DIR", self.tmp), \
             patch.object(core, "load_baseline", return_value=self.baseline), \
             patch.object(core, "BASELINE_PATH", self.tmp / "baseline_index.json"):
            result = rs.apply_cross_source_last_touch()

        self.assertEqual(result["applied"], 1, result)
        update = result["updates"][0]
        self.assertEqual(update["id"], "jane-procurement")
        self.assertEqual(update["new_last_touch"], "2026-09-16")
        self.assertIn("whatsapp", update["source"])
        self.assertEqual(self.baseline[0]["last_touch"], "2026-09-16")

    def test_personal_chat_never_touches_baseline(self):
        self._write_whatsapp_cache([
            _chat("Mom", "personal",
                  [{"id": "jane-procurement", "name": "Jane Procurement"}], "2026-09-16"),
        ])
        with patch.object(core, "INBOX_DIR", self.tmp), \
             patch.object(core, "CACHE_DIR", self.tmp), \
             patch.object(core, "load_baseline", return_value=self.baseline), \
             patch.object(core, "BASELINE_PATH", self.tmp / "baseline_index.json"):
            result = rs.apply_cross_source_last_touch()

        self.assertEqual(result["applied"], 0, result)
        self.assertEqual(self.baseline[0]["last_touch"], "2026-08-01")

    def test_unknown_classification_never_touches_baseline(self):
        self._write_whatsapp_cache([
            _chat("Unresolved Group", "unknown_group", [], "2026-09-16"),
        ])
        with patch.object(core, "INBOX_DIR", self.tmp), \
             patch.object(core, "CACHE_DIR", self.tmp), \
             patch.object(core, "load_baseline", return_value=self.baseline), \
             patch.object(core, "BASELINE_PATH", self.tmp / "baseline_index.json"):
            result = rs.apply_cross_source_last_touch()

        self.assertEqual(result["applied"], 0, result)

    def test_older_whatsapp_message_does_not_regress_last_touch(self):
        self._write_whatsapp_cache([
            _chat("Jane Procurement", "strategic_relationship",
                  [{"id": "jane-procurement", "name": "Jane Procurement"}], "2026-07-01"),
        ])
        with patch.object(core, "INBOX_DIR", self.tmp), \
             patch.object(core, "CACHE_DIR", self.tmp), \
             patch.object(core, "load_baseline", return_value=self.baseline), \
             patch.object(core, "BASELINE_PATH", self.tmp / "baseline_index.json"):
            result = rs.apply_cross_source_last_touch()

        self.assertEqual(result["applied"], 0, result)
        self.assertEqual(self.baseline[0]["last_touch"], "2026-08-01")

    def test_operator_confirmed_contact_not_overwritten_by_whatsapp(self):
        self.baseline[0]["notes"] = "confirmed by todd."
        self._write_whatsapp_cache([
            _chat("Jane Procurement", "strategic_relationship",
                  [{"id": "jane-procurement", "name": "Jane Procurement"}], "2026-09-16"),
        ])
        with patch.object(core, "INBOX_DIR", self.tmp), \
             patch.object(core, "CACHE_DIR", self.tmp), \
             patch.object(core, "load_baseline", return_value=self.baseline), \
             patch.object(core, "BASELINE_PATH", self.tmp / "baseline_index.json"):
            result = rs.apply_cross_source_last_touch()

        self.assertEqual(result["applied"], 0, result)
        self.assertEqual(self.baseline[0]["last_touch"], "2026-08-01")

    def test_missing_cache_file_is_a_graceful_no_op(self):
        # No whatsapp_chats.json written at all -- the source is simply skipped.
        with patch.object(core, "INBOX_DIR", self.tmp), \
             patch.object(core, "CACHE_DIR", self.tmp), \
             patch.object(core, "load_baseline", return_value=self.baseline), \
             patch.object(core, "BASELINE_PATH", self.tmp / "baseline_index.json"):
            result = rs.apply_cross_source_last_touch()

        self.assertEqual(result["applied"], 0, result)
        self.assertEqual(result["status"], rs.STATUS_REFRESHED)


if __name__ == "__main__":
    unittest.main()
