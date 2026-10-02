#!/usr/bin/env python3
"""
rbb_assertion_bridge.py — drain RBB Project write-bridge assertion documents
and persist them through the existing mutation API.

RB-DEFECT-2026-08-20: the RBB ChatGPT Project has no Actions/API access (a
platform limitation, not a bug here — confirmed against current ChatGPT
docs). So an explicit user statement made inside RBB can be recognized as
authoritative but can't be persisted from inside that same chat.

RB-2026-08-23: v1 of this bridge used Gmail (RBB drafts an email) as the
transport. That failed twice in live testing — RBB claimed to have sent or
drafted an email and neither actually existed. Independently re-verified
2026-08-23 that RBB *can* reliably create a real Google Doc via ChatGPT's
Drive write action (added June 2026, for Docs/Sheets/Slides specifically —
a different underlying capability than whatever backs Gmail drafting for
this account). This version drains Drive instead: RBB creates a Google Doc
titled "[RBB-ASSERTION] <anything>" containing the same structured block
this always used; this script finds it, validates it, and persists it
through the exact same API endpoints the Custom GPT would have called. The
validate/apply/idempotency logic below is unchanged from v1 -- only the
fetch layer (Drive instead of Gmail) is new.

Scope, deliberately narrow for v1 (RB-DEFECT-2026-08-20 mutation_authorization
policy in CANONICAL_REGISTRY.yaml): only mutation types with a single, clear,
already-operational mutation owner are supported. Domains still
distributed_consolidation_pending (accounts, opportunities, theses,
decisions) are NOT included here yet -- extend only after those are
consolidated, not before.

Assertion document contract
----------------------------
Title must contain the literal tag "[RBB-ASSERTION]". Body must contain a
fenced block between the literal marker lines:

    -----RBB-ASSERTION-JSON-----
    { ... }
    -----END-RBB-ASSERTION-JSON-----

JSON shape:
    {
      "assertion_id": "<uuid, RBB generates one per assertion>",
      "mutation_type": "add_loop" | "close_loop",
      "asserted_at": "<ISO8601, when the user said it>",
      "user_statement": "<verbatim quote of what the user said>",
      "fields": { ... mutation-type-specific, see _EXECUTORS below ... }
    }

Safety
------
- Owner must be the user's own Google account (checked against
  core.self_emails()) -- only a document the user's own account owns can
  inject an assertion. This runs unattended so there is no other
  authorization gate at drain time; RBB creating the document at all is
  the only checkpoint (unlike the send-approval click Gmail had -- Drive
  document creation in this account does not appear to require one, see
  the OAuth-setup notes below for the read-only scope this uses in turn).
- assertion_id is deduped against a local state file -- an assertion is
  applied at most once, ever, even if the drain runs again or the
  document is re-scanned.
- mutation_type not in the allowed set is skipped, not guessed at.
- Every outcome (persisted / blocked_conflict / skipped_duplicate / failed)
  is recorded, never silently dropped. A single bad document never stops
  the rest of the batch or the pipeline that calls this script.

One-time setup
--------------
This needs its own OAuth token, broader than cockpit_drive_export.py's
drive.file scope (which only sees files that script itself created) --
this needs to see files RBB creates through ChatGPT's own, separate Drive
connection, so it needs drive.readonly across the whole account:

    python3 rbb_assertion_bridge.py --authorize

Usage:
    python3 rbb_assertion_bridge.py            # scan + apply, print summary
    python3 rbb_assertion_bridge.py --json      # scan + apply, JSON summary
    python3 rbb_assertion_bridge.py --dry-run   # scan + validate only, no writes
    python3 rbb_assertion_bridge.py --authorize # one-time OAuth consent
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import rb_core as core  # noqa: E402

STATE_PATH = core.SYSTEM_DIR / ".cache" / "rbb_assertion_bridge_state.json"
API_BASE = os.environ.get("RB_API_BASE", "http://127.0.0.1:8765")
API_KEY = os.environ.get("RB_API_KEY", "")
DRIVE_TITLE_TAG = "[RBB-ASSERTION]"

CLIENT_PATH = Path(os.environ.get("RB_GOOGLE_CLIENT", str(Path.home() / ".config/rb/google_client.json"))).expanduser()
TOKEN_PATH = Path(os.environ.get("RB_ASSERTION_DRIVE_TOKEN",
                                 str(Path.home() / ".config/rb/rbb_assertion_drive_token.json"))).expanduser()
DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

_JSON_BLOCK_RE = re.compile(
    r"-----RBB-ASSERTION-JSON-----\s*(\{.*?\})\s*-----END-RBB-ASSERTION-JSON-----",
    re.DOTALL,
)

# mutation_type -> (endpoint path, required `fields` keys, optional keys)
_EXECUTORS: dict[str, dict] = {
    "add_loop": {
        "path": "/loops",
        "required": ("party", "description", "target"),
        "optional": ("opened", "id"),
    },
    "close_loop": {
        "path": "/loops/close",
        "required": ("id", "reason"),
        "optional": (),
    },
}


def _drive_credentials(*, authorize: bool):
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as exc:
        raise RuntimeError("Install google-auth, google-auth-oauthlib, google-api-python-client") from exc
    creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), DRIVE_SCOPES) if TOKEN_PATH.exists() else None
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    if creds and creds.valid:
        return creds
    if not authorize:
        raise RuntimeError(f"No valid Drive token at {TOKEN_PATH}; run with --authorize once")
    if not CLIENT_PATH.exists():
        raise RuntimeError(f"OAuth client JSON not found at {CLIENT_PATH}")
    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_PATH), DRIVE_SCOPES)
    creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
    TOKEN_PATH.chmod(0o600)
    return creds


def _load_state() -> dict:
    if not STATE_PATH.exists():
        return {"processed": {}}
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"processed": {}}
    data.setdefault("processed", {})
    return data


def _save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(STATE_PATH.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2, default=str) + "\n", encoding="utf-8")
    os.replace(tmp, STATE_PATH)


def _extract_assertion(body_text: str) -> dict | None:
    match = _JSON_BLOCK_RE.search(body_text or "")
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return None


def _validate(assertion: dict) -> str | None:
    """Returns an error string, or None if valid."""
    if not isinstance(assertion, dict):
        return "not a JSON object"
    for key in ("assertion_id", "mutation_type", "asserted_at", "user_statement", "fields"):
        if key not in assertion:
            return f"missing required field: {key}"
    spec = _EXECUTORS.get(assertion["mutation_type"])
    if not spec:
        return f"unsupported mutation_type: {assertion['mutation_type']!r} (allowed: {sorted(_EXECUTORS)})"
    fields = assertion.get("fields") or {}
    if not isinstance(fields, dict):
        return "fields must be an object"
    missing = [k for k in spec["required"] if k not in fields]
    if missing:
        return f"fields missing required keys: {missing}"
    return None


def _call_api(path: str, body: dict) -> tuple[bool, dict]:
    req = urllib.request.Request(
        API_BASE + path,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-api-key": API_KEY},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return True, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            detail = {"detail": str(exc)}
        return False, {"status_code": exc.code, **detail}
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return False, {"error": str(exc)}


def _apply(assertion: dict, *, dry_run: bool) -> dict:
    spec = _EXECUTORS[assertion["mutation_type"]]
    if dry_run:
        return {"status": "would_persist", "endpoint": spec["path"]}
    ok, resp = _call_api(spec["path"], assertion["fields"])
    if ok:
        return {"status": "persisted", "response": resp}
    if resp.get("status_code") == 400:
        return {"status": "blocked_conflict", "response": resp}
    # Anything else (401/403/5xx, network error, timeout) is a transport or
    # server-config problem, not a verdict on the assertion's content -- it
    # must stay eligible for the next drain instead of being dedup-locked in
    # as if it had been resolved. (Caught live 2026-08-24: a missing API key
    # in this process's env produced a 401 that got permanently marked
    # "processed" on the first pass, silently dropping a real assertion.)
    return {"status": "failed", "retryable": True, "response": resp}


def _fetch_candidate_docs() -> list[dict]:
    """Search Drive for undrained [RBB-ASSERTION] documents. Google Docs
    aren't raw bytes -- export as text/plain to get readable content."""
    from googleapiclient.discovery import build

    creds = _drive_credentials(authorize=False)
    service = build("drive", "v3", credentials=creds, cache_discovery=False)
    resp = service.files().list(
        q=(f"name contains '{DRIVE_TITLE_TAG}' and trashed=false "
           "and mimeType='application/vnd.google-apps.document'"),
        spaces="drive", fields="files(id,name,owners,createdTime)",
        orderBy="createdTime desc", pageSize=25,
    ).execute()

    out = []
    for f in resp.get("files") or []:
        owners = f.get("owners") or []
        owner_email = owners[0].get("emailAddress", "") if owners else ""
        try:
            raw = service.files().export(fileId=f["id"], mimeType="text/plain").execute()
            text = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
        except Exception:  # noqa: BLE001
            text = ""
        out.append({
            "file_id": f["id"], "title": f.get("name", ""),
            "owner": owner_email, "text": text,
        })
    return out


def run(*, dry_run: bool = False) -> dict:
    state = _load_state()
    self_emails = core.self_emails()
    results: list[dict] = []

    try:
        docs = _fetch_candidate_docs()
    except Exception as exc:  # noqa: BLE001
        return {"status": "scan_failed", "error": str(exc), "results": []}

    for doc in docs:
        owner_email = core._normalize_email(doc["owner"])  # noqa: SLF001
        assertion = _extract_assertion(doc["text"])

        if not assertion:
            continue  # tagged title but no parseable block -- not ours to touch

        # Dedup key is the Drive file_id, not RBB's self-reported assertion_id.
        # RB-2026-08-24: RBB restarted its assertion_id numbering mid-session
        # and reused an id from an earlier (already-resolved) test doc. Keying
        # dedup on assertion_id made the drain treat a brand-new, unrelated
        # assertion as already handled and silently skip it -- no error, no
        # result entry, nothing. file_id is assigned by Drive itself and is
        # guaranteed unique per document, so it can't collide this way.
        aid = doc["file_id"]

        if aid in state["processed"]:
            continue  # already resolved, ever -- never re-ask, never re-apply

        entry = {"assertion_id": assertion.get("assertion_id"), "file_id": doc["file_id"],
                  "mutation_type": assertion.get("mutation_type"),
                  "user_statement": assertion.get("user_statement")}

        if owner_email not in self_emails:
            entry["status"] = "skipped_duplicate"
            entry["reason"] = f"owner {owner_email!r} is not a configured self-account"
            results.append(entry)
            state["processed"][aid] = {**entry, "resolved_at": datetime.now(timezone.utc).isoformat()}
            continue

        err = _validate(assertion)
        if err:
            entry["status"] = "failed"
            entry["reason"] = err
            results.append(entry)
            state["processed"][aid] = {**entry, "resolved_at": datetime.now(timezone.utc).isoformat()}
            continue

        outcome = _apply(assertion, dry_run=dry_run)
        entry.update(outcome)
        results.append(entry)
        if not dry_run and not outcome.get("retryable"):
            state["processed"][aid] = {**entry, "resolved_at": datetime.now(timezone.utc).isoformat()}

    if not dry_run:
        _save_state(state)

    return {
        "status": "ok",
        "scanned_docs": len(docs),
        "applied": len([r for r in results if r.get("status") == "persisted"]),
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--authorize", action="store_true", help="One-time OAuth consent for Drive read access")
    args = parser.parse_args()

    if args.authorize:
        _drive_credentials(authorize=True)
        print(f"Authorized. Token stored at {TOKEN_PATH}")
        return 0

    result = run(dry_run=args.dry_run)
    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(f"scanned {result.get('scanned_docs', 0)} doc(s), "
              f"applied {result.get('applied', 0)}")
        for r in result.get("results", []):
            print(f"  [{r.get('status')}] {r.get('mutation_type')} — {r.get('user_statement', '')[:80]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
