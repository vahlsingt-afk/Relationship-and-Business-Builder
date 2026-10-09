"""test_hunter_cycle_queue_sweep.py — 2026-10-02.

Covers queue_prepare()/sweep(), built the same day a real scheduled
Hunter cycle hit a confirmed Computer Use browser-safety refusal trying
to push a prepared directive into ChatGPT (both file-attach and a
pasted-text fallback were rejected -- reading a local file and injecting
its content into a web page is exactly the shape of exfiltration a
safety classifier should block, and that is not something to route
around). The fix: automations persist a prepared job (queue_prepare)
instead of submitting it, a human or ChatGPT's own normal file-save drops
the returned packet into a watched inbox, and sweep() matches the two
back together and finalizes -- the same reviewed-not-auto-applied
discipline hunter_cycle.finalize() already has.

Isolated against temp PENDING_JOBS_DIR/PACKETS_INBOX_DIR -- never touches
the real queue or inbox.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import hunter_cycle as hc  # noqa: E402
import hunter_orchestrator as ho  # noqa: E402


def _fake_job(target_key: str, *, research_authorized: bool = True) -> dict:
    return {
        "schema": "rb.hunter_cycle_job.v1",
        "directive": {"packet_requirements": {"target_keys": [target_key], "research_authorized": research_authorized}},
        "before_snapshot": {},
    }


def _fake_packet(target_key: str, packet_id: str = "hunter-test-packet") -> dict:
    return {
        "schema": "rb.hunter_research_packet.v1",
        "packet_id": packet_id,
        "targets": [target_key],
        "payload_schema": "rb.competitor_platform_research.v1",
        "payload": {"findings": []},
    }


class _IsolatedQueueMixin(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        tmp = Path(self._tmpdir.name)
        self._patches = [
            patch.object(hc, "PENDING_JOBS_DIR", tmp / "pending"),
            patch.object(hc, "PACKETS_INBOX_DIR", tmp / "inbox"),
            patch.object(hc, "PROCESSED_JOBS_DIR", tmp / "pending" / "processed"),
            patch.object(hc, "PROCESSED_PACKETS_DIR", tmp / "inbox" / "processed"),
            # 2026-10-03: sweep() now also scans a legacy drop folder --
            # isolate it too, or these tests would scan the real
            # system/inbox/chatgpt_intelligence_drop/ on every run.
            patch.object(hc, "LEGACY_PACKETS_INBOX_DIR", tmp / "legacy_inbox"),
            patch.object(hc, "LEGACY_PROCESSED_PACKETS_DIR", tmp / "legacy_inbox" / "processed"),
            # 2026-10-03 (Defect 1/2 fix): isolate the new quarantine dir too.
            patch.object(hc, "QUARANTINED_JOBS_DIR", tmp / "pending" / "quarantined"),
            # RB-DEFECT-2026-10-09: sweep() now syncs into hunter_orchestrator's
            # own lease ledger -- isolate its ledger the same way every other
            # orchestrator test does, or these tests would touch the real
            # leases.jsonl on every run. CONFIG_PATH is left pointing at the
            # real config (read-only here; its lease.max_attempts governs
            # retryable-vs-terminal_failed, same as production).
            patch.object(ho, "LEDGER_DIR", tmp / "ho_ledger"),
            patch.object(ho, "LEASES_PATH", tmp / "ho_ledger" / "leases.jsonl"),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self._tmpdir.cleanup()


class TestTargetSlug(unittest.TestCase):
    def test_slug_is_filesystem_safe_and_stable(self):
        self.assertEqual(hc._target_slug("competitor:qu"), "competitor-qu")
        self.assertEqual(hc._target_slug("company:brand-charleys-philly-steaks"), "company-brand-charleys-philly-steaks")


class TestQueuePrepare(_IsolatedQueueMixin):
    def test_queues_one_file_per_selected_target(self):
        with patch.object(hc, "prepare", return_value=_fake_job("competitor:qu")):
            result = hc.queue_prepare("competitive_positioning", universe="competitors", target_keys=["competitor:qu"])
        self.assertEqual(result["queued_paths"], [str(hc.PENDING_JOBS_DIR / "competitor-qu.json")])
        self.assertTrue((hc.PENDING_JOBS_DIR / "competitor-qu.json").exists())

    def test_does_not_overwrite_an_already_queued_target(self):
        with patch.object(hc, "prepare", return_value=_fake_job("competitor:qu")):
            hc.queue_prepare("competitive_positioning", universe="competitors", target_keys=["competitor:qu"])
            path = hc.PENDING_JOBS_DIR / "competitor-qu.json"
            path.write_text("SENTINEL: must not be overwritten", encoding="utf-8")
            result = hc.queue_prepare("competitive_positioning", universe="competitors", target_keys=["competitor:qu"])
        self.assertEqual(result["queued_paths"], [])
        self.assertEqual(result["skipped_existing_targets"], ["competitor:qu"])
        self.assertEqual(path.read_text(encoding="utf-8"), "SENTINEL: must not be overwritten")


class TestSweep(_IsolatedQueueMixin):
    def _write_job(self, target_key: str):
        hc.PENDING_JOBS_DIR.mkdir(parents=True, exist_ok=True)
        path = hc.PENDING_JOBS_DIR / f"{hc._target_slug(target_key)}.json"
        path.write_text(json.dumps(_fake_job(target_key)), encoding="utf-8")
        return path

    def _drop_packet(self, target_key: str, filename: str = "dropped.json", **kwargs):
        hc.PACKETS_INBOX_DIR.mkdir(parents=True, exist_ok=True)
        path = hc.PACKETS_INBOX_DIR / filename
        path.write_text(json.dumps(_fake_packet(target_key, **kwargs)), encoding="utf-8")
        return path

    def test_matches_packet_to_queued_job_by_target_and_archives_both(self):
        job_path = self._write_job("competitor:qu")
        packet_path = self._drop_packet("competitor:qu")
        fake_receipt = {"schema": "rb.hunter_cycle_receipt.v1", "ok": False, "packet_id": "hunter-test-packet"}
        with patch.object(hc, "finalize", return_value=fake_receipt) as mock_finalize:
            result = hc.sweep(confirm=False)
        mock_finalize.assert_called_once()
        self.assertEqual(len(result["processed"]), 1)
        self.assertEqual(result["processed"][0]["receipt"], fake_receipt)
        self.assertEqual(result["unmatched"], [])
        self.assertFalse(job_path.exists())
        self.assertFalse(packet_path.exists())
        self.assertTrue((hc.PROCESSED_JOBS_DIR / job_path.name).exists())
        self.assertTrue((hc.PROCESSED_PACKETS_DIR / packet_path.name).exists())

    def test_packet_with_no_queued_job_is_left_in_place_not_dropped(self):
        packet_path = self._drop_packet("competitor:never-queued")
        with patch.object(hc, "finalize") as mock_finalize:
            result = hc.sweep(confirm=False)
        mock_finalize.assert_not_called()
        self.assertEqual(result["processed"], [])
        self.assertEqual(len(result["unmatched"]), 1)
        self.assertEqual(result["unmatched"][0]["targets"], ["competitor:never-queued"])
        self.assertTrue(packet_path.exists())  # never deleted or moved

    def test_malformed_packet_file_is_reported_not_crashed_on(self):
        hc.PACKETS_INBOX_DIR.mkdir(parents=True, exist_ok=True)
        bad_path = hc.PACKETS_INBOX_DIR / "not_json.json"
        bad_path.write_text("this is not valid JSON {{{", encoding="utf-8")
        result = hc.sweep(confirm=False)
        self.assertEqual(result["processed"], [])
        self.assertEqual(len(result["unmatched"]), 1)
        self.assertIn("error", result["unmatched"][0])
        self.assertTrue(bad_path.exists())

    def test_packet_older_than_its_only_matching_job_is_never_matched(self):
        """RB live incident, 2026-10-03: a years-old, wholly unrelated file
        in the legacy drop folder happened to carry a `targets` array that
        coincidentally named a brand-new job's target_key and got finalized
        against it, silently consuming the real job. A reply can't predate
        the question it answers."""
        packet_path = self._drop_packet("competitor:qu")
        old_time = time.time() - 86400
        os.utime(packet_path, (old_time, old_time))
        job_path = self._write_job("competitor:qu")  # queued AFTER the packet already existed
        with patch.object(hc, "finalize") as mock_finalize:
            result = hc.sweep(confirm=False)
        mock_finalize.assert_not_called()
        self.assertEqual(result["processed"], [])
        self.assertTrue(job_path.exists())
        self.assertTrue(packet_path.exists())

    def test_sweep_never_passes_confirm_true_unless_explicitly_asked(self):
        self._write_job("competitor:qu")
        self._drop_packet("competitor:qu")
        with patch.object(hc, "finalize", return_value={"ok": True}) as mock_finalize:
            hc.sweep(confirm=False)
        _, kwargs = mock_finalize.call_args
        self.assertFalse(kwargs.get("confirm"))


class TestSweepLegacyFolder(_IsolatedQueueMixin):
    """2026-10-03: real runs (Tim Hortons, then KFC) confirmed Codex
    doesn't reliably save into PACKETS_INBOX_DIR as instructed -- both
    landed in the pre-existing system/inbox/chatgpt_intelligence_drop/
    folder instead. sweep() now also watches that folder (isolated here
    to LEGACY_PACKETS_INBOX_DIR)."""

    def _write_job(self, target_key: str):
        hc.PENDING_JOBS_DIR.mkdir(parents=True, exist_ok=True)
        path = hc.PENDING_JOBS_DIR / f"{hc._target_slug(target_key)}.json"
        path.write_text(json.dumps(_fake_job(target_key)), encoding="utf-8")
        return path

    def _drop_legacy(self, filename: str, content: str):
        hc.LEGACY_PACKETS_INBOX_DIR.mkdir(parents=True, exist_ok=True)
        path = hc.LEGACY_PACKETS_INBOX_DIR / filename
        path.write_text(content, encoding="utf-8")
        return path

    def test_matching_json_in_legacy_folder_is_finalized_and_archived(self):
        job_path = self._write_job("company:brand-kfc")
        packet_path = self._drop_legacy("Deep Research report(20261003-100423).json",
                                         json.dumps(_fake_packet("company:brand-kfc")))
        fake_receipt = {"schema": "rb.hunter_cycle_receipt.v1", "ok": True}
        with patch.object(hc, "finalize", return_value=fake_receipt) as mock_finalize:
            result = hc.sweep(confirm=False)
        mock_finalize.assert_called_once()
        self.assertEqual(len(result["processed"]), 1)
        self.assertFalse(packet_path.exists())
        self.assertTrue((hc.LEGACY_PROCESSED_PACKETS_DIR / packet_path.name).exists())
        self.assertFalse(job_path.exists())

    def test_unrelated_file_in_legacy_folder_is_silently_ignored_not_reported_unmatched(self):
        """The legacy folder holds ~130+ files from unrelated cycles --
        only PACKETS_INBOX_DIR (a dedicated, Hunter-only inbox) should
        report an unmatched/malformed file as noteworthy; the legacy
        folder's background noise must not spam the unmatched list."""
        self._drop_legacy("RBB_Technology_Economics_Cycle50.zip", "not even text")
        self._drop_legacy("some-other-research-topic.md", "# Unrelated report\n\nNo JSON here at all.")
        result = hc.sweep(confirm=False)
        self.assertEqual(result["processed"], [])
        self.assertEqual(result["unmatched"], [])

    def test_old_legacy_file_coincidentally_matching_a_newer_job_is_not_consumed(self):
        """The exact real incident: reproduces a years-old unrelated file
        in the legacy folder whose `targets` happens to match a job queued
        much later. Must be left alone, not silently finalized against it."""
        packet_path = self._drop_legacy("2026-09-20_1307_candidate-validation_deep-research.json",
                                         json.dumps(_fake_packet("company:brand-chipotle-mexican-grill")))
        old_time = time.time() - 86400 * 13  # ~13 days old, like the real incident
        os.utime(packet_path, (old_time, old_time))
        job_path = self._write_job("company:brand-chipotle-mexican-grill")  # queued today
        with patch.object(hc, "finalize") as mock_finalize:
            result = hc.sweep(confirm=False)
        mock_finalize.assert_not_called()
        self.assertEqual(result["processed"], [])
        self.assertTrue(job_path.exists())
        self.assertTrue(packet_path.exists())

    def test_legacy_file_with_no_matching_job_is_left_in_place_and_not_reported(self):
        self._drop_legacy("some-other-packet.json", json.dumps(_fake_packet("company:brand-never-queued")))
        result = hc.sweep(confirm=False)
        self.assertEqual(result["processed"], [])
        self.assertEqual(result["unmatched"], [])
        self.assertTrue((hc.LEGACY_PACKETS_INBOX_DIR / "some-other-packet.json").exists())

    def test_non_json_non_md_file_in_legacy_folder_is_never_opened(self):
        hc.LEGACY_PACKETS_INBOX_DIR.mkdir(parents=True, exist_ok=True)
        (hc.LEGACY_PACKETS_INBOX_DIR / "archive.zip").write_bytes(b"PK\x03\x04fakezipbytes")
        result = hc.sweep(confirm=False)  # must not raise trying to parse binary content
        self.assertEqual(result["processed"], [])
        self.assertEqual(result["unmatched"], [])

    def test_packets_inbox_dir_still_reports_unmatched_as_before(self):
        """Confirms the dedicated inbox's stricter behavior (every file
        there is reported) is unchanged by adding the legacy scan."""
        hc.PACKETS_INBOX_DIR.mkdir(parents=True, exist_ok=True)
        (hc.PACKETS_INBOX_DIR / "orphan.json").write_text(
            json.dumps(_fake_packet("company:brand-never-queued")), encoding="utf-8")
        result = hc.sweep(confirm=False)
        self.assertEqual(len(result["unmatched"]), 1)


class TestPendingJobCeiling(_IsolatedQueueMixin):
    """2026-10-03 (CLAUDE_HANDOFF_RB_HUNTER_GATHERER_END_TO_END_DEFECTS):
    confirmed live -- 9 jobs piled up pending with zero completions
    because queue_prepare() enforced no ceiling across different targets.

    Pins PENDING_JOB_CEILING_TOTAL to a fixed small value independent of
    whatever the real production ceiling is -- these tests exercise the
    enforcement LOGIC (refuse the Nth job, report the right counts), not
    the production VALUE, so raising the real ceiling for multi-engine
    concurrency (RB-DEFECT-2026-10-09) doesn't require touching this file.
    """

    def setUp(self):
        super().setUp()
        self._ceiling_patch = patch.object(hc, "PENDING_JOB_CEILING_TOTAL", 3)
        self._ceiling_patch.start()
        self.addCleanup(self._ceiling_patch.stop)

    def _queue(self, target_key: str, *, universe: str = "brands"):
        with patch.object(hc, "prepare", return_value=_fake_job(target_key)):
            return hc.queue_prepare("enterprise_account_profile", universe=universe, target_keys=[target_key])

    def test_refuses_a_fourth_total_pending_job(self):
        self._queue("company:brand-one")
        self._queue("competitor:two")
        self._queue("franchisee:three")
        result = self._queue("company:brand-four")
        self.assertEqual(result["queued_paths"], [])
        self.assertTrue(result["ceiling_reached"])
        self.assertEqual(result["skipped_ceiling"][0]["target_key"], "company:brand-four")
        self.assertEqual(result["skipped_ceiling"][0]["pending_total"], 3)
        self.assertFalse((hc.PENDING_JOBS_DIR / "company-brand-four.json").exists())

    def test_refuses_a_second_job_in_the_same_family_even_under_the_total_ceiling(self):
        self._queue("company:brand-one")
        result = self._queue("company:brand-two")
        self.assertEqual(result["queued_paths"], [])
        self.assertTrue(result["ceiling_reached"])
        self.assertEqual(result["skipped_ceiling"][0]["family"], "company")
        self.assertEqual(result["skipped_ceiling"][0]["pending_family"], 1)
        # Total ceiling (3) is not yet reached -- a DIFFERENT family can still queue.
        other = self._queue("competitor:one")
        self.assertEqual(len(other["queued_paths"]), 1)

    def test_multi_target_prepare_partially_succeeds_up_to_the_ceiling(self):
        self._queue("company:brand-one")
        self._queue("competitor:one")
        with patch.object(hc, "prepare", return_value={
            "schema": "rb.hunter_cycle_job.v1",
            "directive": {"packet_requirements": {"target_keys": ["franchisee:one", "franchisee:two"], "research_authorized": True}},
            "before_snapshot": {},
        }):
            result = hc.queue_prepare("franchisee_organization_profile", universe="franchisees",
                                       target_keys=["franchisee:one", "franchisee:two"])
        # Only one more slot is free (2 pending so far, ceiling 3) -- first
        # target fills it, second is reported ceiling_reached, not silently dropped.
        self.assertEqual(len(result["queued_paths"]), 1)
        self.assertEqual(len(result["skipped_ceiling"]), 1)
        self.assertEqual(result["skipped_ceiling"][0]["target_key"], "franchisee:two")


class TestQuarantineStaleJobs(_IsolatedQueueMixin):
    def _write_job(self, target_key: str, *, age_hours: float = 0):
        hc.PENDING_JOBS_DIR.mkdir(parents=True, exist_ok=True)
        path = hc.PENDING_JOBS_DIR / f"{hc._target_slug(target_key)}.json"
        path.write_text(json.dumps(_fake_job(target_key)), encoding="utf-8")
        if age_hours:
            old_time = time.time() - age_hours * 3600
            os.utime(path, (old_time, old_time))
        return path

    def test_moves_a_job_older_than_max_age_into_quarantine(self):
        old_path = self._write_job("company:brand-stale", age_hours=72)
        quarantined = hc.quarantine_stale_jobs(max_age_hours=48)
        self.assertEqual(len(quarantined), 1)
        self.assertFalse(old_path.exists())
        self.assertTrue((hc.QUARANTINED_JOBS_DIR / old_path.name).exists())
        note = json.loads((hc.QUARANTINED_JOBS_DIR / f"{old_path.stem}.quarantine.json").read_text())
        self.assertEqual(note["reason"], "transport_blocked_stale")

    def test_leaves_a_recent_job_in_place(self):
        recent_path = self._write_job("company:brand-fresh", age_hours=1)
        quarantined = hc.quarantine_stale_jobs(max_age_hours=48)
        self.assertEqual(quarantined, [])
        self.assertTrue(recent_path.exists())

    def test_sweep_quarantines_stale_jobs_before_matching_and_frees_ceiling_room(self):
        self._write_job("company:brand-stale", age_hours=72)
        self._write_job("competitor:one", age_hours=72)
        self._write_job("franchisee:one", age_hours=72)
        result = hc.sweep(confirm=False, quarantine_max_age_hours=48)
        self.assertEqual(len(result["quarantined"]), 3)
        self.assertEqual(len(hc._pending_job_files()), 0)
        # Ceiling room is freed -- a new target can now be queued.
        with patch.object(hc, "prepare", return_value=_fake_job("company:brand-new")):
            queued = hc.queue_prepare("enterprise_account_profile", universe="brands", target_keys=["company:brand-new"])
        self.assertEqual(len(queued["queued_paths"]), 1)


class TestTransportGate(_IsolatedQueueMixin):
    """2026-10-06: queue_prepare refuses to persist any job unless Deep
    Research transport is confirmed available (research_authorized), so
    the hourly automation can't pile up assignments it can't execute."""

    def test_unauthorized_prepare_queues_nothing(self):
        with patch.object(hc, "prepare", return_value=_fake_job("company:brand-one", research_authorized=False)):
            result = hc.queue_prepare("enterprise_account_profile", universe="brands", target_keys=["company:brand-one"])
        self.assertTrue(result["transport_blocked"])
        self.assertEqual(result["queued_paths"], [])
        self.assertFalse((hc.PENDING_JOBS_DIR / "company-brand-one.json").exists())


class TestBundleCountsTowardCeiling(_IsolatedQueueMixin):
    """2026-10-06: a single pending file holding several subjobs must count
    per target, or a multi-target bundle silently bypasses the ceiling."""

    def setUp(self):
        super().setUp()  # see TestPendingJobCeiling.setUp for why this is pinned
        self._ceiling_patch = patch.object(hc, "PENDING_JOB_CEILING_TOTAL", 3)
        self._ceiling_patch.start()
        self.addCleanup(self._ceiling_patch.stop)

    def test_three_target_bundle_fills_the_ceiling(self):
        hc.PENDING_JOBS_DIR.mkdir(parents=True, exist_ok=True)
        bundle = {"schema": "rb.hunter_priority_assignment.v1", "target_keys": ["company:a", "company:b", "company:c"],
                  "subjobs": [{"target_key": k, "job": {}} for k in ["company:a", "company:b", "company:c"]]}
        (hc.PENDING_JOBS_DIR / "bundle.json").write_text(json.dumps(bundle), encoding="utf-8")
        with patch.object(hc, "prepare", return_value=_fake_job("competitor:one")):
            result = hc.queue_prepare("competitive_positioning", universe="competitors", target_keys=["competitor:one"])
        self.assertEqual(result["queued_paths"], [])
        self.assertTrue(result["ceiling_reached"])


class TestSweepOrchestratorSync(_IsolatedQueueMixin):
    """RB-DEFECT-2026-10-09: sweep() and hunter_orchestrator.py's lease
    ledger used to be two separate, non-talking bookkeeping systems --
    confirmed live, a job the ledger still showed "leased" days after
    sweep had already matched, validated (and failed), and archived its
    real packet. Recovering it for a corrected resubmission required
    manually moving the job file back out of processed/ by hand."""

    def _write_job_and_lease_it(self, target_key: str):
        hc.PENDING_JOBS_DIR.mkdir(parents=True, exist_ok=True)
        job_path = hc.PENDING_JOBS_DIR / f"{hc._target_slug(target_key)}.json"
        job = _fake_job(target_key)
        job_path.write_text(json.dumps(job), encoding="utf-8")
        jid = ho.job_id_for(job)
        ho._transition(jid, "leased", engine="chatgpt_work", job_path=str(job_path))
        return job_path, jid

    def _drop_packet(self, target_key: str, filename: str = "dropped.json"):
        hc.PACKETS_INBOX_DIR.mkdir(parents=True, exist_ok=True)
        path = hc.PACKETS_INBOX_DIR / filename
        path.write_text(json.dumps(_fake_packet(target_key)), encoding="utf-8")
        return path

    def test_confirmed_success_marks_the_ledger_completed(self):
        job_path, jid = self._write_job_and_lease_it("competitor:qu")
        self._drop_packet("competitor:qu")
        with patch.object(hc, "finalize", return_value={"ok": True}):
            hc.sweep(confirm=True)
        self.assertEqual(ho.job_states()[jid]["state"], "completed")

    def test_confirmed_failure_marks_the_ledger_retryable_and_keeps_the_job_file(self):
        job_path, jid = self._write_job_and_lease_it("competitor:qu")
        self._drop_packet("competitor:qu")
        with patch.object(hc, "finalize", return_value={"ok": False}):
            result = hc.sweep(confirm=True)
        self.assertEqual(ho.job_states()[jid]["state"], "retryable")
        # The job file must still be there for a corrected resubmission to
        # match against -- archiving it here is exactly the bug that made a
        # real recovery require manually moving the file back by hand.
        self.assertTrue(job_path.exists())
        self.assertFalse((hc.PROCESSED_JOBS_DIR / job_path.name).exists())
        self.assertEqual(len(result["processed"]), 1)

    def test_confirmed_failure_past_max_attempts_marks_terminal_failed(self):
        job_path, jid = self._write_job_and_lease_it("competitor:qu")
        cfg = json.loads(ho.CONFIG_PATH.read_text())
        max_attempts = cfg["lease"]["max_attempts"]
        for _ in range(max_attempts):
            ho._transition(jid, "running")
            ho._transition(jid, "retryable")
            ho._transition(jid, "leased")
        self._drop_packet("competitor:qu")
        with patch.object(hc, "finalize", return_value={"ok": False}):
            hc.sweep(confirm=True)
        self.assertEqual(ho.job_states()[jid]["state"], "terminal_failed")

    def test_dry_run_never_touches_the_ledger_even_though_it_still_archives(self):
        job_path, jid = self._write_job_and_lease_it("competitor:qu")
        self._drop_packet("competitor:qu")
        with patch.object(hc, "finalize", return_value={"ok": False}):
            hc.sweep(confirm=False)
        self.assertEqual(ho.job_states()[jid]["state"], "leased")  # unchanged
        self.assertFalse(job_path.exists())  # dry run still archives -- documented, relied-upon behavior
        self.assertTrue((hc.PROCESSED_JOBS_DIR / job_path.name).exists())

    def test_a_job_never_dispatched_through_the_orchestrator_is_untouched(self):
        # No ho._transition call at all -- this job has no orchestrator
        # history, same as a manually-dropped or Codex-prepared batch.
        job_path = hc.PENDING_JOBS_DIR / "competitor-qu.json"
        hc.PENDING_JOBS_DIR.mkdir(parents=True, exist_ok=True)
        job_path.write_text(json.dumps(_fake_job("competitor:qu")), encoding="utf-8")
        self._drop_packet("competitor:qu")
        with patch.object(hc, "finalize", return_value={"ok": False}):
            result = hc.sweep(confirm=True)
        self.assertEqual(ho.job_states(), {})  # never invented lease history
        # No orchestrator record to preserve a retry for -- falls back to the
        # original always-archive behavior.
        self.assertFalse(job_path.exists())
        self.assertEqual(len(result["processed"]), 1)

    def test_a_sync_error_is_recorded_on_the_receipt_not_raised(self):
        job_path, jid = self._write_job_and_lease_it("competitor:qu")
        self._drop_packet("competitor:qu")
        with patch.object(hc, "finalize", return_value={"ok": True}), \
             patch.object(ho, "sync_from_sweep", side_effect=RuntimeError("boom")):
            result = hc.sweep(confirm=True)  # must not raise
        self.assertEqual(result["processed"][0]["receipt"]["orchestrator_sync_error"], "boom")


if __name__ == "__main__":
    unittest.main()
