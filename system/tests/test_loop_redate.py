#!/usr/bin/env python3
"""test_loop_redate.py — RB-2026-08-29.

mutations.cmd_loop_redate() already existed as a working, snapshot-before-
write CLI command but had zero exposure to any API route or chat tool --
confirmed live, a real rbb-chat session correctly declined to claim it had
moved loop target dates it had no actual way to persist ("I only have a
confirmed API action for closing loops, not re-dating/reassigning loop
targets"), rather than fabricate a receipt. Closed the gap: POST
/loops/redate (operationId redateLoop) in server.py, and a redateLoop entry
in rbb_chat_tools.py's _EXTRA_RBB_CHAT_ONLY_TOOLS.

This file covers mutations.cmd_loop_redate() directly (the real logic under
test -- the API route is a thin pass-through, same as closeLoop, which has
no separate route-level test either) plus the chat-tool wiring.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import rb_core as core  # noqa: E402
import mutations  # noqa: E402

_LEDGER_HEADER = (
    "# Loop Ledger\n\n"
    "Open loops awaiting deliberate closure.\n\n"
    "| ID | Opened | Person/Company | Loop | Closure target | Status |\n"
    "|---|---|---|---|---|---|\n"
)


def _ns(**kw):
    kw.setdefault("note", None)
    kw.setdefault("dry_run", False)
    return SimpleNamespace(**kw)


class _IsolatedLedgerMixin:
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp_dir = Path(self._tmp.name)
        self._ledger_path = tmp_dir / "loop_ledger.md"
        self._snap_dir = tmp_dir / "_snapshots"
        self._orig_ledger = core.LOOP_LEDGER_PATH
        self._orig_snap = core.SNAPSHOTS_DIR
        core.LOOP_LEDGER_PATH = self._ledger_path
        core.SNAPSHOTS_DIR = self._snap_dir
        self._ledger_path.write_text(
            _LEDGER_HEADER
            + "| L-2026-07-23-004 | 2026-07-23 | Freddy's / Slim Chickens (Darin) | Connect with Darin. | 2026-07-25 | open |\n"
            + "| L-2026-07-23-005 | 2026-07-23 | Worldpay | Active-account Blue Sheets. | 2026-07-25 | open |\n"
            + "| L-2026-07-23-006 | 2026-07-23 | IKEA (Monica, Ray) | Follow up. | 2026-07-25 | **closed** — done |\n",
            encoding="utf-8",
        )

    def tearDown(self):
        core.LOOP_LEDGER_PATH = self._orig_ledger
        core.SNAPSHOTS_DIR = self._orig_snap
        self._tmp.cleanup()


class TestLoopRedate(_IsolatedLedgerMixin, unittest.TestCase):
    def test_redates_open_loop(self):
        rc = mutations.cmd_loop_redate(_ns(id="L-2026-07-23-004", target="2026-08-01"))
        self.assertEqual(rc, 0)
        text = self._ledger_path.read_text()
        self.assertIn("L-2026-07-23-004", text)
        self.assertIn("2026-08-01", text)
        self.assertNotIn("2026-07-25 | open", text.splitlines()[4] if len(text.splitlines()) > 4 else "")
        row = next(l for l in text.splitlines() if l.startswith("| L-2026-07-23-004"))
        self.assertIn("2026-08-01", row.split(" | ")[4])

    def test_note_appended_to_description_with_old_target(self):
        rc = mutations.cmd_loop_redate(_ns(id="L-2026-07-23-005", target="2026-09-01", note="need to connect for a meeting"))
        self.assertEqual(rc, 0)
        text = self._ledger_path.read_text()
        self.assertIn("need to connect for a meeting", text)
        self.assertIn("from 2026-07-25", text)  # old target preserved in the note

    def test_dry_run_does_not_write(self):
        before = self._ledger_path.read_text()
        rc = mutations.cmd_loop_redate(_ns(id="L-2026-07-23-004", target="2026-08-01", dry_run=True))
        self.assertEqual(rc, 0)
        self.assertEqual(self._ledger_path.read_text(), before)

    def test_closed_loop_cannot_be_redated(self):
        rc = mutations.cmd_loop_redate(_ns(id="L-2026-07-23-006", target="2026-09-15"))
        self.assertEqual(rc, 1)
        text = self._ledger_path.read_text()
        self.assertIn("2026-07-25 | **closed**", text)  # unchanged

    def test_unknown_id_fails(self):
        rc = mutations.cmd_loop_redate(_ns(id="L-2099-01-01-999", target="2026-09-15"))
        self.assertEqual(rc, 1)

    def test_invalid_date_fails(self):
        rc = mutations.cmd_loop_redate(_ns(id="L-2026-07-23-004", target="not-a-date"))
        self.assertEqual(rc, 1)

    def test_snapshot_written_before_real_write(self):
        self.assertEqual(list(self._snap_dir.glob("*")), [])
        mutations.cmd_loop_redate(_ns(id="L-2026-07-23-004", target="2026-08-01"))
        snaps = list(self._snap_dir.glob("loop_ledger.pre-loop-redate-*"))
        self.assertEqual(len(snaps), 1)


class TestRedateLoopChatToolWiring(unittest.TestCase):
    """RB-2026-08-29: the actual gap the real incident exposed -- redateLoop
    must be a real, callable tool in the live chat surface, not just a CLI
    command nobody can reach."""

    def test_redate_loop_is_a_registered_chat_tool(self):
        import rbb_chat_tools as tools
        t, ops = tools.build_tools_and_operations()
        names = [x["name"] for x in t]
        self.assertIn("redateLoop", names)
        op = ops["redateLoop"]
        self.assertEqual(op["method"], "POST")
        self.assertEqual(op["path"], "/loops/redate")
        self.assertIn("id", op["body_param_names"])
        self.assertIn("target", op["body_param_names"])

    def test_redate_loop_schema_requires_id_and_target(self):
        import rbb_chat_tools as tools
        t, _ = tools.build_tools_and_operations()
        tool = next(x for x in t if x["name"] == "redateLoop")
        self.assertEqual(set(tool["parameters"]["required"]), {"id", "target"})


if __name__ == "__main__":
    unittest.main()
