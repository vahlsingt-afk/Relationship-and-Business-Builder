#!/usr/bin/env python3
"""
rbb_chat_history.py — persistent conversation storage for rbb_chat.py.

Single-user personal tool: there is one "current" conversation shared across
every device that opens the chat UI, so a brief run from your phone this
morning is still visible from your Mac this afternoon. "New chat" starts a
fresh one and points "current" at it; old conversations are never deleted,
just no longer pointed at.

Storage: one append-only JSONL file per conversation
(system/rbb_chat_history/<conversation_id>.jsonl), plus a small pointer file
(system/rbb_chat_history/_current.json) naming the active one. Deliberately
plain files, matching audit_log.py's convention, rather than a database —
this is single-writer, low-volume, and needs to survive process restarts,
nothing more.
"""
from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import rb_core as core  # noqa: E402

HISTORY_DIR = core.SYSTEM_DIR / "rbb_chat_history"
CURRENT_POINTER_PATH = HISTORY_DIR / "_current.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _conversation_path(conversation_id: str) -> Path:
    return HISTORY_DIR / f"{conversation_id}.jsonl"


def _new_conversation_id() -> str:
    return uuid.uuid4().hex[:16]


def start_new_conversation() -> str:
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    conversation_id = _new_conversation_id()
    _conversation_path(conversation_id).touch()
    CURRENT_POINTER_PATH.write_text(
        json.dumps({"conversation_id": conversation_id, "started_at": _now_iso()}), encoding="utf-8"
    )
    return conversation_id


def get_current_conversation_id() -> str:
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    if CURRENT_POINTER_PATH.exists():
        try:
            data = json.loads(CURRENT_POINTER_PATH.read_text(encoding="utf-8"))
            conversation_id = data.get("conversation_id")
            if conversation_id and _conversation_path(conversation_id).exists():
                return conversation_id
        except (json.JSONDecodeError, OSError):
            pass
    return start_new_conversation()


def append_turn(
    conversation_id: str,
    role: str,
    text: str,
    response_id: Optional[str] = None,
    tool_calls: Optional[list[dict]] = None,
) -> None:
    turn: dict[str, Any] = {"ts": _now_iso(), "role": role, "text": text}
    if response_id:
        turn["response_id"] = response_id
    if tool_calls:
        turn["tool_calls"] = tool_calls
    path = _conversation_path(conversation_id)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(turn) + "\n")


def load_conversation(conversation_id: str) -> list[dict]:
    path = _conversation_path(conversation_id)
    if not path.exists():
        return []
    turns = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                turns.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return turns


def estimate_size_chars(conversation_id: str) -> int:
    """Rough proxy for how many tokens this conversation's accumulated
    history will cost to reprocess on the *next* turn via
    previous_response_id chaining (rbb_chat.py bills the full chain every
    turn, confirmed by its own context_length_exceeded handling) -- exact
    tokenization isn't worth the dependency, char count is a fine
    order-of-magnitude signal for a cost warn-threshold."""
    total = 0
    for turn in load_conversation(conversation_id):
        total += len(turn.get("text", ""))
        if turn.get("tool_calls"):
            total += len(json.dumps(turn["tool_calls"]))
    return total


def get_last_response_id(conversation_id: str) -> Optional[str]:
    turns = load_conversation(conversation_id)
    for turn in reversed(turns):
        if turn.get("role") == "assistant" and turn.get("response_id"):
            return turn["response_id"]
    return None


def list_conversations() -> list[dict]:
    """Every real conversation on disk, newest-touched first. Title is
    auto-derived from the first user turn (no separate rename UI/storage
    for v1 — matches ChatGPT/Claude's own default auto-titling). Backs the
    UI's sidebar (RB-2026-08-28) -- the storage model already supported
    multiple conversations by id; nothing here previously listed them."""
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    out: list[dict] = []
    for path in HISTORY_DIR.glob("*.jsonl"):
        conversation_id = path.stem
        turns = load_conversation(conversation_id)
        if not turns:
            continue  # an empty/just-started conversation isn't worth a sidebar row yet
        first_user_text = next((t.get("text", "") for t in turns if t.get("role") == "user"), "")
        title = (first_user_text or "New chat").strip().replace("\n", " ")
        if len(title) > 48:
            title = title[:48].rstrip() + "…"
        last_ts = turns[-1].get("ts") or ""
        out.append({
            "conversation_id": conversation_id,
            "title": title,
            "last_updated_at": last_ts,
            "turn_count": len(turns),
        })
    out.sort(key=lambda c: c["last_updated_at"], reverse=True)
    return out
