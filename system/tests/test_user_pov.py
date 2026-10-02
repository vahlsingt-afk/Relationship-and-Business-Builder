"""
test_user_pov.py — User POV Registry Phase 1 governed storage module.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import user_pov as up  # noqa: E402


class _IsolatedRootMixin:
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        tmp = Path(self._tmpdir.name)
        self._orig = {
            "ROOT": up.ROOT, "REGISTRY_PATH": up.REGISTRY_PATH,
            "EVENTS_PATH": up.EVENTS_PATH, "EVIDENCE_LINKS_PATH": up.EVIDENCE_LINKS_PATH,
        }
        up.ROOT = tmp
        up.REGISTRY_PATH = tmp / "registry.json"
        up.EVENTS_PATH = tmp / "events.jsonl"
        up.EVIDENCE_LINKS_PATH = tmp / "evidence_links.jsonl"

    def tearDown(self):
        for name, path in self._orig.items():
            setattr(up, name, path)
        self._tmpdir.cleanup()


class TestAddPovEntry(_IsolatedRootMixin, unittest.TestCase):
    def test_creates_entry_with_defaults(self):
        entry = up.add_pov_entry("Test statement", "principle", "test_scope")
        self.assertEqual(entry["status"], "active")
        self.assertEqual(entry["conviction"], "informed_belief")
        self.assertEqual(entry["authorship"], "user_authored")
        self.assertFalse(entry["needs_review"])
        self.assertIsNone(entry["supersedes"])

    def test_rbb_inferred_starts_needs_review(self):
        entry = up.add_pov_entry("Inferred statement", "hypothesis", "test_scope", authorship="rbb_inferred")
        self.assertTrue(entry["needs_review"])

    def test_empty_statement_rejected(self):
        with self.assertRaises(up.UserPovError):
            up.add_pov_entry("", "principle", "test_scope")

    def test_invalid_type_rejected(self):
        with self.assertRaises(up.UserPovError):
            up.add_pov_entry("x", "made_up_type", "test_scope")

    def test_invalid_conviction_rejected(self):
        with self.assertRaises(up.UserPovError):
            up.add_pov_entry("x", "principle", "test_scope", conviction="made_up")

    def test_writes_event(self):
        up.add_pov_entry("x", "principle", "test_scope")
        events = up._load_jsonl(up.EVENTS_PATH)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event"], "created")


class TestGetAndListPovEntries(_IsolatedRootMixin, unittest.TestCase):
    def test_get_unknown_raises(self):
        with self.assertRaises(up.UserPovError):
            up.get_pov_entry("pov-does-not-exist")

    def test_list_filters_by_scope_type_status(self):
        up.add_pov_entry("a", "principle", "scope_a")
        up.add_pov_entry("b", "hard_boundary", "scope_b")
        up.add_pov_entry("c", "principle", "scope_a", status="testing")
        self.assertEqual(len(up.list_pov_entries(scope="scope_a")), 2)
        self.assertEqual(len(up.list_pov_entries(entry_type="hard_boundary")), 1)
        self.assertEqual(len(up.list_pov_entries(status="testing")), 1)
        self.assertEqual(len(up.list_pov_entries()), 3)


class TestRevisePovEntry(_IsolatedRootMixin, unittest.TestCase):
    def test_revision_creates_new_entry_and_supersedes_original(self):
        original = up.add_pov_entry("original statement", "principle", "test_scope")
        revised = up.revise_pov_entry(original["pov_id"], "revised statement", reason="clarify")
        self.assertEqual(revised["supersedes"], original["pov_id"])
        self.assertEqual(revised["statement"], "revised statement")
        reloaded_original = up.get_pov_entry(original["pov_id"])
        self.assertEqual(reloaded_original["status"], "superseded")
        self.assertEqual(reloaded_original["superseded_by"], revised["pov_id"])
        # original's statement itself was never edited
        self.assertEqual(reloaded_original["statement"], "original statement")

    def test_revision_inherits_conviction_when_not_overridden(self):
        original = up.add_pov_entry("x", "principle", "test_scope", conviction="strong_conviction")
        revised = up.revise_pov_entry(original["pov_id"], "y")
        self.assertEqual(revised["conviction"], "strong_conviction")

    def test_revision_overrides_conviction_when_given(self):
        original = up.add_pov_entry("x", "principle", "test_scope", conviction="working_hypothesis")
        revised = up.revise_pov_entry(original["pov_id"], "y", conviction="foundational_principle")
        self.assertEqual(revised["conviction"], "foundational_principle")

    def test_revising_unknown_entry_raises(self):
        with self.assertRaises(up.UserPovError):
            up.revise_pov_entry("pov-fake", "x")

    def test_revising_already_superseded_entry_raises(self):
        original = up.add_pov_entry("x", "principle", "test_scope")
        up.revise_pov_entry(original["pov_id"], "y")
        with self.assertRaises(up.UserPovError):
            up.revise_pov_entry(original["pov_id"], "z")

    def test_revising_retired_entry_raises(self):
        entry = up.add_pov_entry("x", "principle", "test_scope")
        up.retire_pov_entry(entry["pov_id"], reason="done")
        with self.assertRaises(up.UserPovError):
            up.revise_pov_entry(entry["pov_id"], "y")


class TestRetirePovEntry(_IsolatedRootMixin, unittest.TestCase):
    def test_retire_sets_status(self):
        entry = up.add_pov_entry("x", "principle", "test_scope")
        retired = up.retire_pov_entry(entry["pov_id"], reason="no longer applies")
        self.assertEqual(retired["status"], "retired")

    def test_retire_requires_reason(self):
        entry = up.add_pov_entry("x", "principle", "test_scope")
        with self.assertRaises(up.UserPovError):
            up.retire_pov_entry(entry["pov_id"], reason="")

    def test_retire_unknown_raises(self):
        with self.assertRaises(up.UserPovError):
            up.retire_pov_entry("pov-fake", reason="x")


class TestAttachPovEvidence(_IsolatedRootMixin, unittest.TestCase):
    def test_attach_supports_updates_entry_field(self):
        entry = up.add_pov_entry("x", "principle", "test_scope")
        ev = up.attach_pov_evidence(entry["pov_id"], "supports", "real evidence", source_url="https://example.com")
        reloaded = up.get_pov_entry(entry["pov_id"])
        self.assertIn(ev["evidence_id"], reloaded["supporting_evidence_ids"])

    def test_attach_challenges_updates_correct_field(self):
        entry = up.add_pov_entry("x", "principle", "test_scope")
        ev = up.attach_pov_evidence(entry["pov_id"], "challenges", "counter-evidence")
        reloaded = up.get_pov_entry(entry["pov_id"])
        self.assertIn(ev["evidence_id"], reloaded["challenging_evidence_ids"])
        self.assertEqual(reloaded["supporting_evidence_ids"], [])

    def test_attach_never_changes_statement(self):
        entry = up.add_pov_entry("original statement", "principle", "test_scope")
        up.attach_pov_evidence(entry["pov_id"], "qualifies", "some nuance")
        reloaded = up.get_pov_entry(entry["pov_id"])
        self.assertEqual(reloaded["statement"], "original statement")

    def test_invalid_relation_rejected(self):
        entry = up.add_pov_entry("x", "principle", "test_scope")
        with self.assertRaises(up.UserPovError):
            up.attach_pov_evidence(entry["pov_id"], "made_up_relation", "e")

    def test_attach_to_unknown_entry_raises(self):
        with self.assertRaises(up.UserPovError):
            up.attach_pov_evidence("pov-fake", "supports", "e")

    def test_list_evidence_for(self):
        entry = up.add_pov_entry("x", "principle", "test_scope")
        up.attach_pov_evidence(entry["pov_id"], "supports", "e1")
        up.attach_pov_evidence(entry["pov_id"], "challenges", "e2")
        self.assertEqual(len(up.list_evidence_for(entry["pov_id"])), 2)


if __name__ == "__main__":
    unittest.main()
