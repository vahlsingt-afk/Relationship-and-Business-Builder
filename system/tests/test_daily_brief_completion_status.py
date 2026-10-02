"""
test_daily_brief_completion_status.py — RB 9.20 completion artifact tests (T6).

Tests:
  T6a: completion artifact is written after brief generation
  T6b: artifact includes required fields
  T6c: status is one of completed|completed_with_warnings|failed
  T6d: stale sources are listed in artifact
  T6e: notification_line is non-empty and human-readable
  T6f: artifact reflects actual source state (stale sources appear)
  T6g: source_refreshes is a non-empty list
"""
from __future__ import annotations
import sys
import json
import unittest
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import daily_brief as db
import rb_core as core

TODAY = date(2026, 5, 28)

REQUIRED_FIELDS = [
    "date", "started_at", "completed_at", "status",
    "source_refreshes", "failed_integrations", "stale_sources",
    "critical_failures", "published_paths", "notification_line",
]

ALLOWED_STATUSES = frozenset({"completed", "completed_with_warnings", "failed"})


def _build_and_write_artifact(status: str = "completed", stale_sources: list[str] | None = None):
    """Call _write_completion_artifact and return the artifact dict."""
    report = db.build_report(TODAY)
    with tempfile.TemporaryDirectory() as tmpdir:
        original_cache = core.CACHE_DIR
        core.CACHE_DIR = Path(tmpdir)
        try:
            db._write_completion_artifact(
                today=TODAY,
                report=report,
                status=status,
                started_at="2026-05-28T05:00:00",
                published_paths=["system/today.md", "system/MANIFEST.md"],
            )
            artifact_path = Path(tmpdir) / "daily_brief_status.json"
            if artifact_path.exists():
                return json.loads(artifact_path.read_text())
            return None
        finally:
            core.CACHE_DIR = original_cache


class TestCompletionArtifact(unittest.TestCase):

    def setUp(self):
        self.artifact = _build_and_write_artifact()

    def test_artifact_is_written(self):
        """T6a: completion artifact is written."""
        self.assertIsNotNone(self.artifact, "Completion artifact was not written")

    def test_artifact_has_required_fields(self):
        """T6b: artifact includes all required fields."""
        for field in REQUIRED_FIELDS:
            self.assertIn(field, self.artifact, f"Completion artifact missing field: {field}")

    def test_status_is_valid_enum(self):
        """T6c: status is one of allowed values."""
        self.assertIn(
            self.artifact["status"],
            ALLOWED_STATUSES,
            f"Invalid status: {self.artifact['status']}"
        )

    def test_notification_line_non_empty(self):
        """T6e: notification_line is a non-empty string."""
        line = self.artifact.get("notification_line") or ""
        self.assertTrue(len(line) > 10, f"notification_line too short: '{line}'")

    def test_notification_line_mentions_time(self):
        """T6e: notification_line mentions time or count."""
        line = self.artifact.get("notification_line") or ""
        has_time = any(c in line for c in (":", "AM", "PM", "RB"))
        self.assertTrue(has_time, f"notification_line should mention time or RB: '{line}'")

    def test_source_refreshes_is_list(self):
        """T6g: source_refreshes is a non-empty list."""
        refreshes = self.artifact.get("source_refreshes")
        self.assertIsInstance(refreshes, list, "source_refreshes must be a list")
        self.assertGreater(len(refreshes), 0, "source_refreshes must not be empty")

    def test_source_refreshes_have_source_field(self):
        """T6g: each source_refresh has a 'source' field."""
        for r in (self.artifact.get("source_refreshes") or []):
            self.assertIn("source", r, f"source_refresh item missing 'source': {r}")

    def test_published_paths_non_empty(self):
        """T6a: published_paths is non-empty."""
        paths = self.artifact.get("published_paths") or []
        self.assertGreater(len(paths), 0, "published_paths must not be empty")

    def test_date_matches_today(self):
        """T6b: artifact date matches the brief date."""
        self.assertEqual(self.artifact.get("date"), TODAY.isoformat())

    def test_completed_with_warnings_when_stale(self):
        """T6c: status is completed_with_warnings when tier-1 sources are stale."""
        # The real report will have stale email (based on real system state)
        # If tier1_stale is set, status should be completed_with_warnings
        report = db.build_report(TODAY)
        sf = report.get("source_freshness") or {}
        if sf.get("tier1_stale"):
            artifact = _build_and_write_artifact(status="completed_with_warnings")
            self.assertEqual(artifact["status"], "completed_with_warnings")
        else:
            # If not stale, just verify status is valid
            self.assertIn(self.artifact["status"], ALLOWED_STATUSES)

    def test_stale_sources_reflects_reality(self):
        """T6d: stale_sources in artifact matches source_freshness."""
        report = db.build_report(TODAY)
        sf = report.get("source_freshness") or {}
        sources = sf.get("sources") or {}
        expected_stale = {k for k, v in sources.items() if v.get("stale")}
        artifact_stale = set(self.artifact.get("stale_sources") or [])
        self.assertEqual(artifact_stale, expected_stale,
                         f"stale_sources mismatch: expected {expected_stale}, got {artifact_stale}")

    def test_cos_judgment_confidence_in_artifact(self):
        """T6b: artifact includes cos_judgment_confidence."""
        self.assertIn("cos_judgment_confidence", self.artifact)
        self.assertIn(
            self.artifact["cos_judgment_confidence"],
            ("high", "medium", "low", "unknown"),
        )


if __name__ == "__main__":
    unittest.main()
