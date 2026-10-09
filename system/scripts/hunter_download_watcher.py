#!/usr/bin/env python3
"""Move completed Hunter packets from Downloads into the Drive inbox.

Deep Research exports land in ~/Downloads as PDF, JSON, TXT or Markdown. This
watcher looks for a genuine rb.hunter_research_packet.v1 envelope inside each
new file, repairs PDF line-wrap damage inside JSON strings, and copies the
parsed packet into the private Drive inbox. Files without a valid envelope are
left in Downloads with the reason recorded. Nothing is deleted, and nothing is
written to canonical records; hunter_cycle.py sweep still does the governed
dry-run validation the next morning.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SYSTEM = ROOT / "system"
DOWNLOADS = Path(os.environ.get("RB_HUNTER_DOWNLOADS", str(Path.home() / "Downloads"))).expanduser()
DRIVE_INBOX = Path(os.environ.get("RB_HUNTER_DRIVE_INBOX", str(Path.home() / "My Drive" / "RBB Hunter Cycle Inbox"))).expanduser()
STATE = SYSTEM / ".cache" / "hunter_download_watcher.json"
PACKET_SCHEMA = "rb.hunter_research_packet.v1"
EXTENSIONS = {".pdf", ".json", ".txt", ".md"}
LOOKBACK = timedelta(days=3)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _text_of(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        import pypdf

        reader = pypdf.PdfReader(str(path))
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    return path.read_text(encoding="utf-8-sig", errors="replace")


def repair_string_newlines(text: str) -> str:
    """Replace raw line breaks inside JSON string literals with spaces.

    PDF extraction wraps long lines, which can split a JSON string across
    lines. Newlines outside strings are structural and are kept.
    """
    out, in_string, escaped = [], False, False
    for ch in text:
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            elif ch in "\r\n":
                out.append(" ")
                continue
        elif ch == '"':
            in_string = True
        out.append(ch)
    return "".join(out)


def find_envelope(text: str) -> dict | None:
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", text):
        try:
            value, _ = decoder.raw_decode(text[match.start():])
        except ValueError:
            continue
        if isinstance(value, dict) and value.get("schema") == PACKET_SCHEMA and value.get("targets"):
            return value
    return None


PROCESSED_RECEIPT_DIRS = (
    SYSTEM / "inbox" / "hunter_packets" / "processed",
    SYSTEM / "inbox" / "chatgpt_intelligence_drop" / "processed",
)


def _already_processed_packet_ids() -> set[str]:
    """Packet IDs that a sweep already finalized. A re-exported copy of one of
    these must never reach the inbox, or it could match a new queued job."""
    ids: set[str] = set()
    for folder in PROCESSED_RECEIPT_DIRS:
        for receipt in folder.glob("*.receipt.json") if folder.is_dir() else []:
            try:
                value = json.loads(receipt.read_text(encoding="utf-8")).get("packet_id")
            except (OSError, ValueError):
                continue
            if value:
                ids.add(str(value))
    return ids


def _load_state() -> dict:
    if STATE.exists():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return {"seen": {}}


def _save_state(state: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def sweep_downloads(*, downloads: Path = DOWNLOADS, drive_inbox: Path = DRIVE_INBOX,
                    now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    state = _load_state()
    copied, rejected, skipped = [], [], []
    if not downloads.is_dir():
        return {"schema": "rb.hunter_download_watcher.v1", "error": f"downloads folder missing: {downloads}",
                "copied": [], "rejected": [], "skipped": []}
    drive_inbox.mkdir(parents=True, exist_ok=True)
    processed_ids = _already_processed_packet_ids()
    for path in sorted(downloads.iterdir()):
        if not path.is_file() or path.suffix.lower() not in EXTENSIONS:
            continue
        modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        if now - modified > LOOKBACK:
            continue
        digest = _digest(path)
        if digest in state["seen"]:
            skipped.append(path.name)
            continue
        try:
            text = _text_of(path)
        except Exception as error:  # unreadable file: record and move on
            rejected.append({"file": path.name, "reason": f"unreadable: {error}"})
            state["seen"][digest] = {"file": path.name, "outcome": "rejected", "at": now.isoformat()}
            continue
        packet = find_envelope(repair_string_newlines(text))
        if packet is None:
            rejected.append({"file": path.name, "reason": "no valid rb.hunter_research_packet.v1 envelope found"})
            state["seen"][digest] = {"file": path.name, "outcome": "rejected", "at": now.isoformat()}
            continue
        packet_id = re.sub(r"[^A-Za-z0-9._-]+", "-", str(packet.get("packet_id") or path.stem))
        # RB-DEFECT-2026-10-09: confirmed live -- hunter_drive_inbox_sync.py's own
        # matcher only considers a Drive file a candidate packet when its filename
        # stem contains "packet", "response", "batch", or "bundle" (it shares this
        # folder with outgoing assignment files and other unrelated content, and
        # uses the name to tell them apart before even opening the file). A real
        # GPT-assigned packet_id has no reason to contain any of those words --
        # "hunter-kfc-work-20261009-hj61a4a8b8535cb0b90448" didn't -- so a packet
        # this watcher successfully recovered from Downloads sat silently
        # stranded in Drive forever, never picked up by the next stage. The
        # "packet-" prefix guarantees the stem always matches, regardless of
        # whatever packet_id the research agent happened to choose.
        dest = drive_inbox / f"hunter-packet-{packet_id}.json"
        if str(packet.get("packet_id")) in processed_ids:
            skipped.append(path.name)
            state["seen"][digest] = {"file": path.name, "outcome": "already_processed", "at": now.isoformat()}
            continue
        if dest.exists():
            skipped.append(path.name)
            state["seen"][digest] = {"file": path.name, "outcome": "duplicate_packet_id", "at": now.isoformat()}
            continue
        dest.write_text(json.dumps(packet, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        copied.append({"file": path.name, "packet_id": packet.get("packet_id"), "dest": str(dest)})
        state["seen"][digest] = {"file": path.name, "outcome": "copied", "dest": str(dest), "at": now.isoformat()}
    _save_state(state)
    return {"schema": "rb.hunter_download_watcher.v1", "copied": copied, "rejected": rejected, "skipped": skipped}


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--downloads", type=Path, default=DOWNLOADS)
    parser.add_argument("--drive-inbox", type=Path, default=DRIVE_INBOX)
    args = parser.parse_args()
    result = sweep_downloads(downloads=args.downloads, drive_inbox=args.drive_inbox)
    print(json.dumps(result, indent=2))
    return 1 if result.get("error") else 0


if __name__ == "__main__":
    sys.exit(main())
