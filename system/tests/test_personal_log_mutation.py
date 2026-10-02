"""
test_personal_log_mutation.py — RB-DEFECT-062

Regression coverage for Life Lens's write path. Before this fix,
system/personal_log.json — the data store behind the Daily Brief's Life Lens
section — did not exist as a file at all, and there was no way for the live
Custom GPT to write to it: every manually-tracked goal (morning_prayer,
devotions, mary_connection, sunday_service) showed "0/N — not logged yet" in
every brief since the feature was added. Confirmed live: Todd told RB "Mary
and I prayed together and read the bible together yesterday" and RB narrated
a full list of "mutations" with zero backing writes.

Covers, fully isolated from the real (pre-existing, interactive-check-in)
personal_log.py module and life_goals.yaml — no test here reads or writes
system/personal_log.json or system/life_goals.yaml:

  1. intelligence_triage.classify_executive_declaration() recognizing the new
     personal_practice_logged event type for prayer/devotions/bible/church/
     Mary language.
  2. personal_log.record_personal_practice(): multi-label detection (one
     sentence completing several goals at once), "yesterday" date
     resolution, no-match on unrelated text, dry-run vs. apply.
  3. Integration: POST /ingest/executive_declaration via TestClient(server.app)
     end-to-end, asserting the response includes a personal_log_update
     mutation and the file actually reflects it afterward.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))
sys.path.insert(0, str(ROOT / "system" / "api"))

import intelligence_triage as it  # noqa: E402
import personal_log  # noqa: E402

SAMPLE_GOALS_YAML = """
user_name: Test
domains:
  - id: spiritual
    label: "Spiritual"
    goals:
      - id: morning_prayer
        label: "Morning Prayer"
        data_source: manual
        target: 7
        target_unit: "days/week"
        log_keywords: ["prayed", "praying", "pray together"]
      - id: devotions
        label: "Devotions"
        data_source: manual
        target: 5
        target_unit: "days/week"
        log_keywords: ["devotions", "read the bible", "bible reading"]
  - id: health
    label: "Health"
    goals:
      - id: exercise
        label: "Exercise"
        data_source: strava
        target: 4
        target_unit: "days/week"
  - id: family
    label: "Family"
    goals:
      - id: mary_connection
        label: "Quality time with Mary"
        data_source: manual
        target: 7
        target_unit: "days/week"
        log_keywords: ["with mary", "mary and i", "me and mary"]
      - id: date_night
        label: "Date Night"
        data_source: calendar
        target: 1
        target_unit: "times/week"
        log_keywords: ["date night", "went on a date", "had a date"]
  - id: church
    label: "Church"
    goals:
      - id: sunday_service
        label: "Sunday Service"
        data_source: manual
        target: 4
        target_unit: "times/month"
        log_keywords: ["church", "sunday service"]
"""


class TestClassifyPersonalPractice(unittest.TestCase):
    def test_prayer_and_bible_recognized(self):
        stream = it.classify_executive_declaration(
            "Mary and I prayed together and read the bible together yesterday"
        )
        self.assertIsNotNone(stream)
        self.assertEqual(stream["event_type"], "personal_practice_logged")
        self.assertEqual(stream["mutation_target"], "personal_log")

    def test_church_recognized(self):
        stream = it.classify_executive_declaration("Went to church this morning")
        self.assertEqual(stream["event_type"], "personal_practice_logged")

    def test_unrelated_text_not_recognized(self):
        self.assertIsNone(it.classify_executive_declaration("Let's grab lunch sometime this week."))


class TestRecordPersonalPractice(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.goals_path = Path(self.tmpdir.name) / "life_goals.yaml"
        self.goals_path.write_text(SAMPLE_GOALS_YAML)
        self.log_path = Path(self.tmpdir.name) / "personal_log.json"

        self._patches = [
            patch.object(personal_log, "GOALS_PATH", self.goals_path),
            patch.object(personal_log, "LOG_PATH", self.log_path),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self.tmpdir.cleanup()

    def test_multi_label_match_logs_all_mentioned_goals(self):
        result = personal_log.record_personal_practice(
            "Mary and I prayed together and read the bible together yesterday", apply=True
        )
        self.assertEqual(result["status"], "applied")
        self.assertEqual(
            set(result["goals_logged"]), {"morning_prayer", "devotions", "mary_connection"}
        )
        # exercise (Strava, no log_keywords) is never conversationally loggable;
        # date_night simply isn't mentioned in this text — see
        # test_calendar_sourced_goal_loggable_via_keywords below for the case
        # where it is.
        self.assertNotIn("exercise", result["goals_logged"])
        self.assertNotIn("date_night", result["goals_logged"])

    def test_calendar_sourced_goal_loggable_via_keywords(self):
        """A calendar-sourced goal (date_night) that opts in via log_keywords
        should still be conversationally loggable — the calendar can miss an
        event the same way it missed Todd's actual date nights."""
        result = personal_log.record_personal_practice("We had a date night yesterday", apply=True)
        self.assertEqual(result["status"], "applied")
        self.assertIn("date_night", result["goals_logged"])

    def test_strava_goal_never_conversationally_loggable(self):
        """exercise has no log_keywords — a mention of it in conversation
        must never flip the Strava-sourced goal, regardless of wording."""
        result = personal_log.record_personal_practice("I did exercise yesterday", apply=True)
        self.assertEqual(result["status"], "no_match")

    def test_single_goal_match(self):
        result = personal_log.record_personal_practice("Went to church this morning", apply=True)
        self.assertEqual(result["status"], "applied")
        self.assertEqual(result["goals_logged"], ["sunday_service"])
        self.assertEqual(result["date"], date.today().isoformat())

    def test_no_match_returns_no_match_status(self):
        result = personal_log.record_personal_practice("The weather is nice today.", apply=True)
        self.assertEqual(result["status"], "no_match")
        self.assertFalse(self.log_path.exists())

    def test_dry_run_does_not_persist(self):
        result = personal_log.record_personal_practice("I prayed this morning", apply=False)
        self.assertEqual(result["status"], "dry_run")
        self.assertEqual(result["goals_logged"], ["morning_prayer"])
        self.assertFalse(self.log_path.exists())

    def test_existing_day_entry_is_preserved_not_overwritten(self):
        self.log_path.write_text(json.dumps({date.today().isoformat(): {"sunday_service": True}}))
        result = personal_log.record_personal_practice("I prayed this morning", apply=True)
        self.assertEqual(result["status"], "applied")
        reloaded = json.loads(self.log_path.read_text())
        today_entry = reloaded[date.today().isoformat()]
        self.assertTrue(today_entry["sunday_service"])  # preserved
        self.assertTrue(today_entry["morning_prayer"])  # newly added


class TestRecordPersonalRelationshipEvents(unittest.TestCase):
    """Covers the sibling gap to RB-DEFECT-062: a date night or a spouse's
    milestone had no capability to persist at all before — not a Life Lens
    goal, not a professional relationship_intake.py interaction (Mary isn't
    a baseline_index.json contact), not a to-do."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.rel_path = Path(self.tmpdir.name) / "personal_relationship_log.json"
        self.timeline_path = Path(self.tmpdir.name) / "personal_timeline.json"
        self._patches = [
            patch.object(personal_log, "RELATIONSHIP_LOG_PATH", self.rel_path),
            patch.object(personal_log, "TIMELINE_PATH", self.timeline_path),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self.tmpdir.cleanup()

    def test_multi_event_report_produces_relationship_and_life_events(self):
        text = (
            "Mary and I prayed and read the bible together yesterday and we had "
            "a date night, and I supported her at her retirement party."
        )
        result = personal_log.record_personal_relationship_events(text, apply=True)
        self.assertEqual(result["status"], "applied")
        self.assertEqual(
            set(result["relationship_events"]),
            {"spiritual_activity", "date_night", "milestone_support"},
        )
        self.assertEqual(result["life_events_recorded"], 1)

        rel_log = json.loads(self.rel_path.read_text())
        self.assertEqual(len(rel_log), 3)
        self.assertTrue(all(e["person"] == "Mary" for e in rel_log))

        timeline = json.loads(self.timeline_path.read_text())
        self.assertEqual(len(timeline), 1)
        self.assertEqual(timeline[0]["valence"], "positive")

    def test_completed_action_report_creates_no_loop_or_todo(self):
        """The response has no loop/to-do field at all — this function only
        ever writes to the relationship log and timeline files."""
        result = personal_log.record_personal_relationship_events(
            "We had a date night yesterday", apply=True
        )
        self.assertNotIn("loop", result)
        self.assertNotIn("todo", result)

    def test_no_match_returns_no_match_status(self):
        result = personal_log.record_personal_relationship_events(
            "The weather is nice today.", apply=True
        )
        self.assertEqual(result["status"], "no_match")
        self.assertFalse(self.rel_path.exists())
        self.assertFalse(self.timeline_path.exists())

    def test_dry_run_does_not_persist(self):
        result = personal_log.record_personal_relationship_events(
            "We had a date night", apply=False
        )
        self.assertEqual(result["status"], "dry_run")
        self.assertFalse(self.rel_path.exists())


class TestIngestExecutiveDeclarationIntegration(unittest.TestCase):
    """End-to-end: POST /ingest/executive_declaration surfaces a personal_log_update."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.goals_path = Path(self.tmpdir.name) / "life_goals.yaml"
        self.goals_path.write_text(SAMPLE_GOALS_YAML)
        self.log_path = Path(self.tmpdir.name) / "personal_log.json"
        self.rel_path = Path(self.tmpdir.name) / "personal_relationship_log.json"
        self.timeline_path = Path(self.tmpdir.name) / "personal_timeline.json"

        import server  # noqa: E402  (imported lazily so path patches apply first)
        self.server = server
        self._patches = [
            patch.object(personal_log, "GOALS_PATH", self.goals_path),
            patch.object(personal_log, "LOG_PATH", self.log_path),
            patch.object(personal_log, "RELATIONSHIP_LOG_PATH", self.rel_path),
            patch.object(personal_log, "TIMELINE_PATH", self.timeline_path),
            # _execute_executive_declaration's action_completed branch also writes to
            # interaction_ledger.json at SYSTEM_DIR / "interaction_ledger.json" — redirect
            # the whole thing to a tempdir rather than touch the real one (RB-DEFECT-042).
            patch.object(server, "SYSTEM_DIR", Path(self.tmpdir.name)),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self.tmpdir.cleanup()

    def test_declaration_triggers_personal_log_update(self):
        from fastapi.testclient import TestClient
        client = TestClient(self.server.app, headers={"x-api-key": "test-key"})
        resp = client.post(
            "/ingest/executive_declaration",
            json={"text": "Mary and I prayed together and read the bible together yesterday"},
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "ok")
        log_mutations = [m for m in body["mutations_applied"] if m.get("mutation") == "personal_log_update"]
        self.assertEqual(len(log_mutations), 1)
        self.assertEqual(log_mutations[0]["status"], "applied")
        self.assertEqual(
            set(log_mutations[0]["goals_logged"]), {"morning_prayer", "devotions", "mary_connection"}
        )

        reloaded = json.loads(self.log_path.read_text())
        expected_date = (date.today() - timedelta(days=1)).isoformat()
        self.assertTrue(reloaded[expected_date]["morning_prayer"])

    def test_full_report_triggers_both_goal_and_relationship_mutations(self):
        """Reproduces the exact reported gap: prayer+bible+date night+milestone
        support in one message should yield a personal_log_update AND a
        personal_relationship_event mutation, and zero loop/to-do mutations —
        completed-action reports aren't future commitments."""
        from fastapi.testclient import TestClient
        client = TestClient(self.server.app, headers={"x-api-key": "test-key"})
        resp = client.post(
            "/ingest/executive_declaration",
            json={"text": (
                "Mary and I prayed and read the bible together yesterday and we "
                "had a date night, and I supported her at her retirement party."
            )},
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        mutations = {m["mutation"] for m in body["mutations_applied"]}
        self.assertIn("personal_log_update", mutations)
        self.assertIn("personal_relationship_event", mutations)
        self.assertNotIn("eolms_loop_transition", mutations)

        rel_mutation = next(m for m in body["mutations_applied"] if m["mutation"] == "personal_relationship_event")
        self.assertEqual(
            set(rel_mutation["relationship_events"]),
            {"spiritual_activity", "date_night", "milestone_support"},
        )
        self.assertEqual(rel_mutation["life_events_recorded"], 1)

        self.assertTrue(self.rel_path.exists())
        self.assertTrue(self.timeline_path.exists())


if __name__ == "__main__":
    unittest.main()
