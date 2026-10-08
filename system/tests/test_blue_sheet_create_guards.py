"""
test_blue_sheet_create_guards.py — RB-2026-08-28.

Real incident: rbb-chat called createBlueSheetAccount for "worldpay" after
the user asked to "update the worldpay account plan" (a portfolio-level,
multi-account document upload -- not a Blue Sheet request at all). The
endpoint silently created a Blue Sheet with every substantive field empty
(opportunities: [], commercial_models: [], buying_influences: [],
bottom_line: "") and hardcoded activation_authorized_by="Todd Vahlsing"
regardless of what was actually said. Two guards added:
  1. create_blue_sheet_account() rejects account_data with no substantive
     content (ValueError, before any file is written).
  2. CreateBlueSheetAccountBody now requires user_authorization_quote.
This tests guard #1 directly (fires before any filesystem write, so it's
safe to test without touching real blue_sheets/ state) and guard #2's tool
schema (required field present in rbb_chat_tools.py's definition).
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "blue_sheets" / "_engine"))
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import create_account  # noqa: E402


class TestEmptyContentGuard(unittest.TestCase):
    """RB-2026-09-07: real production pollution found and fixed during the
    3-store unification cutover -- this class used to derive its cleanup
    path from create_account.common.ROOT, which stayed correct only as
    long as create_blue_sheet_account() also wrote under ROOT. Once
    account_dir() moved to CUSTOMERS_PROSPECTS_ROOT, the two "satisfies the
    guard" tests below silently wrote real fixture accounts into the live
    customers_prospects/ tree and never cleaned them up (the cleanup code
    was checking the wrong, now-stale path). Isolated with a real tmp
    CUSTOMERS_PROSPECTS_ROOT now, matching every other Blue Sheet test in
    this suite -- nothing here touches real project state anymore."""

    def _empty_account_data(self) -> dict:
        return {
            "opportunities": [],
            "qualification": {"criteria": []},
            "commercial_models": [],
            "buying_influences": [],
            "strategic_position": {},
            "latest_review": {},
            "bottom_line": "",
        }

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        tmp_root = Path(self._tmpdir.name)
        self._orig_root = create_account.common.CUSTOMERS_PROSPECTS_ROOT
        create_account.common.CUSTOMERS_PROSPECTS_ROOT = tmp_root
        (tmp_root / "_portfolio").mkdir(parents=True)
        (tmp_root / "_portfolio" / "customers_prospects_registry.json").write_text(
            json.dumps({"registry": []}), encoding="utf-8",
        )
        self.tmp_root = tmp_root

    def tearDown(self):
        create_account.common.CUSTOMERS_PROSPECTS_ROOT = self._orig_root
        self._tmpdir.cleanup()

    def test_all_empty_fields_rejected_before_any_write(self):
        # A slug that could never legitimately exist -- if this guard did
        # NOT fire before the write, it would create a real directory.
        slug = "test-empty-guard-should-never-persist"
        acct_dir = self.tmp_root / "accounts" / slug
        self.assertFalse(acct_dir.exists(), "test slug must not pre-exist")
        with self.assertRaises(ValueError) as ctx:
            create_account.create_blue_sheet_account(
                slug, "Test Empty Guard",
                account_data=self._empty_account_data(),
                brand_profile_data={},
            )
        self.assertIn("no substantive content", str(ctx.exception))
        self.assertFalse(acct_dir.exists(), "guard must fire before any directory is created")

    def test_bottom_line_alone_satisfies_the_guard(self):
        data = self._empty_account_data()
        data["bottom_line"] = "Real assessment text."
        # Should NOT raise the emptiness ValueError (may still fail later
        # in render.py for missing structure -- that's a separate concern
        # from this guard, so accept any exception OTHER than the
        # "no substantive content" ValueError).
        slug = "test-bottom-line-guard-check"
        try:
            create_account.create_blue_sheet_account(
                slug, "Test", account_data=data, brand_profile_data={},
            )
        except ValueError as exc:
            self.assertNotIn("no substantive content", str(exc))
        except Exception:
            pass

    def test_opportunities_alone_satisfies_the_guard(self):
        data = self._empty_account_data()
        data["opportunities"] = [{"opportunity_id": "opp-1"}]
        slug = "test-opportunities-guard-check"
        try:
            create_account.create_blue_sheet_account(
                slug, "Test", account_data=data, brand_profile_data={},
            )
        except ValueError as exc:
            self.assertNotIn("no substantive content", str(exc))
        except Exception:
            pass


class TestActivateResearchShellEngagementTierGuard(unittest.TestCase):
    """RB defect 2026-09-30 (Five Guys/Del Taco): activate_existing_research_
    shell()'s first guard used to reject on `engagement_tier == "active_
    engagement"` alone -- but that is exactly the tag an active engagement
    missing its Blue Sheet already carries on its own shell (the same
    conflated signal behind the Five Guys staleness defect), so the guard
    rejected the very accounts this endpoint exists to activate. The real
    "already has an active canonical workbook" check (registry workbook_path
    + engagement_tier, further down) is unaffected and still fires."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        tmp_root = Path(self._tmpdir.name)
        self._orig_root = create_account.common.CUSTOMERS_PROSPECTS_ROOT
        create_account.common.CUSTOMERS_PROSPECTS_ROOT = tmp_root
        self.tmp_root = tmp_root
        (tmp_root / "_portfolio").mkdir(parents=True)

        self.slug = "test-active-engagement-shell"
        self.acct_dir = tmp_root / "accounts" / self.slug
        self.acct_dir.mkdir(parents=True)
        (self.acct_dir / "account.json").write_text(json.dumps({
            "account_id": f"acct-{self.slug}", "account_slug": self.slug,
            "opportunities": [], "engagement_tier": "active_engagement",
        }), encoding="utf-8")
        (self.acct_dir / "brand_profile.json").write_text(json.dumps({
            "account_id": f"acct-{self.slug}",
        }), encoding="utf-8")
        (tmp_root / "_portfolio" / "customers_prospects_registry.json").write_text(json.dumps({
            "registry": [{
                "account_id": f"acct-{self.slug}", "workbook_path": None,
                "engagement_tier": "active_engagement",
            }],
        }), encoding="utf-8")

    def tearDown(self):
        create_account.common.CUSTOMERS_PROSPECTS_ROOT = self._orig_root
        self._tmpdir.cleanup()

    def test_active_engagement_with_no_workbook_is_not_rejected_as_already_active(self):
        try:
            create_account.activate_existing_research_shell(
                self.slug, "Test Active Engagement Shell",
                account_data={"opportunities": [{"opportunity_id": "opp-1"}], "bottom_line": "Real assessment text."},
                brand_profile_data={},
            )
        except ValueError as exc:
            self.assertNotIn("already has active Blue Sheet content", str(exc))
        except Exception:
            pass  # render.py may still fail on this minimal shape -- not this guard's concern


class TestAuthorizationQuoteRequiredInToolSchema(unittest.TestCase):
    def test_user_authorization_quote_is_a_required_tool_parameter(self):
        import rbb_chat_tools as tools
        create_tool = next(
            (t for t, _op in tools._EXTRA_RBB_CHAT_ONLY_TOOLS if t.get("name") == "createBlueSheetAccount"),
            None,
        )
        self.assertIsNotNone(create_tool, "createBlueSheetAccount tool definition not found")
        self.assertIn("user_authorization_quote", create_tool["parameters"]["required"])
        self.assertIn("user_authorization_quote", create_tool["parameters"]["properties"])

    def test_user_authorization_quote_forwarded_to_request_body(self):
        import rbb_chat_tools as tools
        _tool, op = next(
            (t, o) for t, o in tools._EXTRA_RBB_CHAT_ONLY_TOOLS if t.get("name") == "createBlueSheetAccount"
        )
        self.assertIn("user_authorization_quote", op["body_param_names"])


if __name__ == "__main__":
    unittest.main()
