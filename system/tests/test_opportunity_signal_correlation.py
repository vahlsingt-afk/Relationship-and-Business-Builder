"""
test_opportunity_signal_correlation.py — cross-channel opportunity stakeholder
signal correlation (defect: "Failure to Correlate Global Payments Hiring
Signals Across Email, SMS, and Opportunity Intelligence", 2026-06-10).

Tests:
  OSC1 — load_active_dossiers resolves key_contacts[].aliases into a
         stakeholder index, skipping contacts with no aliases and dossiers
         that aren't active/building.
  OSC2 — scan_email matches an inbound thread by sender email alias and
         classifies "final feedback" + "scheduling request" milestones.
  OSC3 — scan_sms matches an inbound message by phone alias and classifies
         "internal coordination" milestone.
  OSC4 — build_report correlates the email + SMS signals across channels
         within the correlation window and marks the opportunity critical.
  OSC5 — signals outside the scan window are excluded.
  OSC6 — _opportunity_signal_items (daily_brief) renders a critical,
         cross-channel item as act_today and it leads
         morning_command_center.
"""
import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import rb_core as core  # noqa: E402
import opportunity_signal_correlation as osc  # noqa: E402
import daily_brief  # noqa: E402


REGISTRY = {
    "artifacts": [
        {
            "artifact_id": "account_dossier:global_payments",
            "artifact_type": "account_dossier",
            "entity": "Global Payments Inc.",
            "status": "building",
            "data_path": "system/artifacts/data/global_payments.json",
        },
        {
            "artifact_id": "account_dossier:inactive_co",
            "artifact_type": "account_dossier",
            "entity": "Inactive Co",
            "status": "archived",
            "data_path": "system/artifacts/data/inactive_co.json",
        },
    ]
}

DOSSIER = {
    "_schema": "rb_account_dossier_v1",
    "artifact_id": "account_dossier:global_payments",
    "entity": "Global Payments Inc.",
    "opportunity_state": "role_exploration",
    "key_contacts": [
        {
            "contact_id": "ryan-hildebrand",
            "name": "Ryan Hildebrand",
            "aliases": {"emails": ["ryan.hildebrand1@yahoo.com"], "phones": ["+12154509933"]},
        },
        {
            "contact_id": "christian-jackson-gpn",
            "name": "Christian Jackson",
            "aliases": {"emails": ["cj77746@globalpayments.com", "christian.jackson@e-hps.com"], "phones": []},
        },
        {
            "contact_id": "no-aliases",
            "name": "No Aliases Person",
        },
    ],
}


def _iso(dt: datetime) -> str:
    return dt.isoformat()


class _FakeSandbox:
    """Builds a temp system/ tree and monkeypatches module-level paths."""

    def __init__(self, tmp_path: Path):
        self.root = tmp_path
        self.system_dir = tmp_path / "system"
        (self.system_dir / "artifacts" / "data").mkdir(parents=True)
        (self.system_dir / "inbox").mkdir(parents=True)
        (self.system_dir / ".cache").mkdir(parents=True)

        (self.system_dir / "artifacts" / "registry.json").write_text(json.dumps(REGISTRY), encoding="utf-8")
        (self.system_dir / "artifacts" / "data" / "global_payments.json").write_text(json.dumps(DOSSIER), encoding="utf-8")
        # inactive_co.json deliberately not written — archived dossier must not be loaded.

    def write_email(self, threads: list[dict]) -> None:
        (self.system_dir / "inbox" / "email.personal.json").write_text(
            json.dumps({"threads": threads}), encoding="utf-8"
        )

    def write_sms(self, events: list[dict]) -> None:
        (self.system_dir / "inbox" / "messages.json").write_text(
            json.dumps({"events": events}), encoding="utf-8"
        )

    def patch(self, monkeypatch):
        monkeypatch.setattr(core, "SYSTEM_DIR", self.system_dir)
        monkeypatch.setattr(osc, "REGISTRY_PATH", self.system_dir / "artifacts" / "registry.json")
        monkeypatch.setattr(osc, "CACHE_PATH", self.system_dir / ".cache" / "opportunity_signals.json")


def _now():
    return datetime(2026, 6, 10, 12, 0, 0, tzinfo=timezone.utc)


class TestOpportunitySignalCorrelation(unittest.TestCase):

    def setUp(self):
        import tempfile
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmpdir.name)
        self.sandbox = _FakeSandbox(self.tmp_path)
        self._patches = []
        for target, attr, value in [
            (core, "SYSTEM_DIR", self.sandbox.system_dir),
            (osc, "REGISTRY_PATH", self.sandbox.system_dir / "artifacts" / "registry.json"),
            (osc, "CACHE_PATH", self.sandbox.system_dir / ".cache" / "opportunity_signals.json"),
        ]:
            old = getattr(target, attr)
            self._patches.append((target, attr, old))
            setattr(target, attr, value)

    def tearDown(self):
        for target, attr, old in self._patches:
            setattr(target, attr, old)
        self._tmpdir.cleanup()

    def test_osc1_load_active_dossiers(self):
        dossiers = osc.load_active_dossiers()
        self.assertEqual(len(dossiers), 1, "archived dossier must be excluded")
        d = dossiers[0]
        self.assertEqual(d["artifact_id"], "account_dossier:global_payments")
        ids = {sh["contact_id"] for sh in d["stakeholders"]}
        self.assertEqual(ids, {"ryan-hildebrand", "christian-jackson-gpn"},
                         "contact with no aliases must be excluded")

    def test_osc2_scan_email_final_feedback(self):
        now = _now()
        self.sandbox.write_email([{
            "thread_id": "19eaebc325e420ee",
            "subject": "Final Feedback- Sales Executive",
            "snippet": ("Hi Todd, I hope all is well! I received final feedback from your "
                        "interviews. Do you have time for a quick call tomorrow? Thanks! "
                        "Christian Jackson Senior Recruiter christian.jackson@e-hps.com"),
            "last_message_at": "Tue, 9 Jun 2026 23:33:44 +0000",
            "last_message_from": {"email": "CJ77746@globalpayments.com", "name": "Christian Jackson"},
        }])
        dossier = osc.load_active_dossiers()[0]
        signals = osc.scan_email(dossier, now - timedelta(days=14))
        self.assertEqual(len(signals), 1)
        sig = signals[0]
        self.assertEqual(sig["stakeholder_contact_id"], "christian-jackson-gpn")
        types = {m["milestone_type"] for m in sig["milestones"]}
        self.assertIn("final_feedback", types)
        self.assertIn("scheduling_request", types)
        self.assertEqual(sig["urgency"], "critical")

    def test_osc3_scan_sms_internal_coordination(self):
        now = _now()
        self.sandbox.write_sms([
            {
                "id": "msg-201516",
                "handle": "+12154509933",
                "direction": "inbound",
                "has_text": True,
                "at": "2026-06-10T00:23:59+00:00",
                "full_text": "Hi Todd. Christian Jackson from our team should be reaching out to you if she has not already.",
            },
            {
                "id": "msg-out",
                "handle": "+12154509933",
                "direction": "outbound",
                "has_text": True,
                "at": "2026-06-10T00:25:00+00:00",
                "full_text": "Perfect. Thank you.",
            },
        ])
        dossier = osc.load_active_dossiers()[0]
        signals = osc.scan_sms(dossier, now - timedelta(days=14))
        self.assertEqual(len(signals), 1, "outbound messages must be excluded")
        sig = signals[0]
        self.assertEqual(sig["stakeholder_contact_id"], "ryan-hildebrand")
        types = {m["milestone_type"] for m in sig["milestones"]}
        self.assertIn("internal_coordination", types)

    def test_osc4_cross_channel_correlation_critical(self):
        now = _now()
        self.sandbox.write_email([{
            "thread_id": "19eaebc325e420ee",
            "subject": "Final Feedback- Sales Executive",
            "snippet": "I received final feedback from your interviews. Do you have time for a quick call tomorrow?",
            "last_message_at": "Tue, 9 Jun 2026 23:33:44 +0000",
            "last_message_from": {"email": "CJ77746@globalpayments.com", "name": "Christian Jackson"},
        }])
        self.sandbox.write_sms([{
            "id": "msg-201516",
            "handle": "+12154509933",
            "direction": "inbound",
            "has_text": True,
            "at": "2026-06-10T00:23:59+00:00",
            "full_text": "Hi Todd. Christian Jackson from our team should be reaching out to you if she has not already.",
        }])
        report = osc.build_report(now=now)
        self.assertEqual(report["opportunity_count"], 1)
        opp = report["opportunities"][0]
        self.assertEqual(opp["entity"], "Global Payments Inc.")
        self.assertEqual(opp["top_urgency"], "critical")
        self.assertTrue(opp["cross_channel_correlated"])
        self.assertEqual(opp["signal_count"], 2)
        for sig in opp["signals"]:
            self.assertTrue(sig["cross_channel_correlated"])
            self.assertTrue(sig["correlated_with"])

    def test_osc5_outside_scan_window_excluded(self):
        now = _now()
        old_date = (now - timedelta(days=30)).isoformat()
        self.sandbox.write_sms([{
            "id": "msg-old",
            "handle": "+12154509933",
            "direction": "inbound",
            "has_text": True,
            "at": old_date,
            "full_text": "Christian Jackson from our team should be reaching out to you.",
        }])
        report = osc.build_report(now=now)
        self.assertEqual(report["opportunity_count"], 0)

    def test_osc6_daily_brief_command_center_elevation(self):
        now = _now()
        self.sandbox.write_email([{
            "thread_id": "19eaebc325e420ee",
            "subject": "Final Feedback- Sales Executive",
            "snippet": "I received final feedback from your interviews. Do you have time for a quick call tomorrow?",
            "last_message_at": "Tue, 9 Jun 2026 23:33:44 +0000",
            "last_message_from": {"email": "CJ77746@globalpayments.com", "name": "Christian Jackson"},
        }])
        self.sandbox.write_sms([{
            "id": "msg-201516",
            "handle": "+12154509933",
            "direction": "inbound",
            "has_text": True,
            "at": "2026-06-10T00:23:59+00:00",
            "full_text": "Hi Todd. Christian Jackson from our team should be reaching out to you if she has not already.",
        }])

        report = {"opportunity_signals": osc.build_report(now=now)}
        items = daily_brief._opportunity_signal_items(report)
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item["disposition"], "act_today")
        self.assertTrue(item["extras"]["cross_channel_correlated"])

        sections = {"opportunity_signals": items}
        cc = daily_brief._build_command_center(sections, {})
        self.assertTrue(cc, "command center must not be empty")
        self.assertEqual(cc[0]["title"], "Opportunity signal — Global Payments Inc.")


if __name__ == "__main__":
    unittest.main()
