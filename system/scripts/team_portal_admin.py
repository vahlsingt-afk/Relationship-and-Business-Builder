#!/usr/bin/env python3
"""team_portal_admin.py — add/revoke teammate credentials for the Team Portal.

Deliberately simple for a 2-5 person tool: no roles/scopes matrix, every
active member gets the same read/write access to the tech-stack + brief
surfaces in system/api/team_portal_api.py.

Two files, two different trust levels:
  - system/team/manifest.yaml   — non-secret roster (id, name, email, role,
    added_at, revoked_at). Git-tracked.
  - team_portal_credentials.json (outside the project workspace, next to
    secrets.env — same trust boundary: chmod 600, not git-tracked) — only
    a SHA-256 hash of each member's token, never the raw value. The raw
    token is generated once, printed once, and never persisted anywhere.

Usage:
    python3 system/scripts/team_portal_admin.py add-member --id jsmith --name "Jane Smith" --email jane@example.com
    python3 system/scripts/team_portal_admin.py revoke --id jsmith
    python3 system/scripts/team_portal_admin.py list
"""
from __future__ import annotations

import argparse
import hashlib
import json
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.stderr.write("PyYAML required: pip install pyyaml\n")
    raise

SCRIPTS_DIR = Path(__file__).resolve().parent
SYSTEM_DIR = SCRIPTS_DIR.parent
MANIFEST_PATH = SYSTEM_DIR / "team" / "manifest.yaml"
CREDENTIALS_PATH = (
    Path.home() / "Library" / "Application Support" / "Relationship Builder"
    / "team_portal_credentials.json"
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _load_manifest() -> dict:
    if not MANIFEST_PATH.exists():
        return {"members": []}
    return yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8")) or {"members": []}


def _save_manifest(manifest: dict) -> None:
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")


def _load_credentials() -> dict:
    if not CREDENTIALS_PATH.exists():
        return {}
    return json.loads(CREDENTIALS_PATH.read_text(encoding="utf-8"))


def _save_credentials(creds: dict) -> None:
    CREDENTIALS_PATH.parent.mkdir(parents=True, exist_ok=True)
    CREDENTIALS_PATH.write_text(json.dumps(creds, indent=2), encoding="utf-8")
    CREDENTIALS_PATH.chmod(0o600)


def add_member(
    member_id: str, name: str, email: str, role: str = "team_member", *,
    is_owner: bool = False, plan: str = "internal",
) -> str:
    manifest = _load_manifest()
    if any(m["id"] == member_id for m in manifest["members"]):
        raise ValueError(f"member '{member_id}' already exists — use revoke + a new id to replace")

    entry = {
        "id": member_id,
        "name": name,
        "email": email,
        "role": role,
        "added_at": _now_iso(),
        "revoked_at": None,
        "suspended_at": None,
        # 2026-09-30: no billing/quota engine reads this yet -- added so a
        # future usage-based plan doesn't need a second manifest schema
        # migration. "internal" for every real member today.
        "plan": plan,
    }
    # 2026-09-25: gates ONE thing in team_portal_api.py's get_current_member
    # -- the canonical-background-brief/competitive-brief/battle-card
    # generate-vs-read-only split. Omitted entirely (not written as False)
    # when not set, matching manifest.yaml's own documented "optional,
    # defaults false" convention -- keeps a plain team_member's entry
    # exactly as compact as it was before this flag existed.
    if is_owner:
        entry["is_owner"] = True
    manifest["members"].append(entry)
    _save_manifest(manifest)

    token = secrets.token_urlsafe(32)
    creds = _load_credentials()
    creds[member_id] = {
        "token_hash": _hash_token(token),
        "created_at": _now_iso(),
        "revoked_at": None,
    }
    _save_credentials(creds)
    return token


def rotate_member(member_id: str) -> str:
    """Reissue a token for an existing, non-revoked member (e.g. lost/forgotten).

    Unlike add_member, this keeps the manifest entry (added_at, role,
    is_owner) untouched and only replaces the credential record's hash --
    the old token stops working the instant this returns, since
    get_current_member() only ever checks the current hash.
    """
    manifest = _load_manifest()
    member = next((m for m in manifest["members"] if m["id"] == member_id), None)
    if member is None:
        raise ValueError(f"no member '{member_id}' in {MANIFEST_PATH}")
    if member.get("revoked_at"):
        raise ValueError(f"member '{member_id}' is revoked — use add-member with a new id instead")

    creds = _load_credentials()
    token = secrets.token_urlsafe(32)
    creds[member_id] = {
        "token_hash": _hash_token(token),
        "created_at": creds.get(member_id, {}).get("created_at", _now_iso()),
        "rotated_at": _now_iso(),
        "revoked_at": None,
        # Preserved from the manifest (the authoritative source
        # get_current_member() also checks independently) -- the old
        # version of this function dropped it here, which didn't create
        # a real access-control gap (the manifest-side suspended_at
        # check still blocks a suspended member regardless), but did
        # leave the credentials file's own mirror of it stale/wrong
        # after a rotate.
        "suspended_at": member.get("suspended_at"),
    }
    _save_credentials(creds)
    return token


def rotate_all_members() -> dict[str, str]:
    """Reissue tokens for EVERY non-revoked member in one pass (Todd,
    2026-09-30: group-wide rotation, e.g. after a suspected leak or as
    routine hygiene) -- every current token in the group stops working
    the instant this returns. Suspended members are included (rotating
    doesn't grant them access back -- the manifest-side suspended_at
    gate in get_current_member() is untouched by this), so their fresh
    token is ready and waiting for whenever they're unsuspended, rather
    than needing its own separate rotate later.

    Returns {member_id: new_token} for every member actually rotated --
    the caller is responsible for distributing each one (same "shown
    once, never persisted in plaintext" discipline as add_member/
    rotate_member)."""
    manifest = _load_manifest()
    active_ids = [m["id"] for m in manifest["members"] if not m.get("revoked_at")]
    return {member_id: rotate_member(member_id) for member_id in active_ids}


def revoke_member(member_id: str) -> None:
    manifest = _load_manifest()
    found = False
    for m in manifest["members"]:
        if m["id"] == member_id:
            m["revoked_at"] = _now_iso()
            found = True
    if not found:
        raise ValueError(f"no member '{member_id}' in {MANIFEST_PATH}")
    _save_manifest(manifest)

    creds = _load_credentials()
    if member_id in creds:
        creds[member_id]["revoked_at"] = _now_iso()
        _save_credentials(creds)


def _find_member(manifest: dict, member_id: str) -> dict:
    member = next((m for m in manifest["members"] if m["id"] == member_id), None)
    if member is None:
        raise ValueError(f"no member '{member_id}' in {MANIFEST_PATH}")
    return member


def suspend_member(member_id: str) -> None:
    """Real 2026-09-30 addition, distinct from revoke_member: a
    reversible pause (suspended_at), for "on leave" rather than "left the
    company" -- unsuspend_member() lifts it without rotating a new key.
    get_current_member() in team_portal_api.py treats a suspended member
    the same as a revoked one (401) while it's set."""
    manifest = _load_manifest()
    member = _find_member(manifest, member_id)
    if member.get("revoked_at"):
        raise ValueError(f"member '{member_id}' is revoked, not just suspended — use add-member with a new id")
    if member.get("suspended_at"):
        raise ValueError(f"member '{member_id}' is already suspended")
    member["suspended_at"] = _now_iso()
    _save_manifest(manifest)

    creds = _load_credentials()
    if member_id in creds:
        creds[member_id]["suspended_at"] = _now_iso()
        _save_credentials(creds)


def unsuspend_member(member_id: str) -> None:
    manifest = _load_manifest()
    member = _find_member(manifest, member_id)
    if not member.get("suspended_at"):
        raise ValueError(f"member '{member_id}' is not suspended")
    member["suspended_at"] = None
    _save_manifest(manifest)

    creds = _load_credentials()
    if member_id in creds:
        creds[member_id]["suspended_at"] = None
        _save_credentials(creds)


def list_members() -> list[dict]:
    return _load_manifest()["members"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_add = sub.add_parser("add-member")
    p_add.add_argument("--id", required=True)
    p_add.add_argument("--name", required=True)
    p_add.add_argument("--email", required=True)
    p_add.add_argument("--role", default="team_member")
    p_add.add_argument("--is-owner", action="store_true",
                        help="Grants the full (non-redacted) canonical-background-brief / "
                             "competitive-brief / battle-card generate path -- see manifest.yaml's own "
                             "doc comment. Only Todd's own entry should ever set this.")

    p_revoke = sub.add_parser("revoke")
    p_revoke.add_argument("--id", required=True)

    p_suspend = sub.add_parser("suspend")
    p_suspend.add_argument("--id", required=True)

    p_unsuspend = sub.add_parser("unsuspend")
    p_unsuspend.add_argument("--id", required=True)

    p_rotate = sub.add_parser("rotate")
    p_rotate.add_argument("--id", required=True)

    sub.add_parser("list")

    args = parser.parse_args()

    if args.cmd == "add-member":
        try:
            token = add_member(args.id, args.name, args.email, args.role, is_owner=args.is_owner)
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        print(f"Added '{args.id}'. Token (shown once, not stored anywhere in plaintext):\n\n  {token}\n")
        print("Give this to them directly (not email/Slack in plaintext, same discipline as any other credential).")
        return 0

    if args.cmd == "revoke":
        try:
            revoke_member(args.id)
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        print(f"Revoked '{args.id}'. Takes effect immediately (credentials are checked fresh per request).")
        return 0

    if args.cmd == "suspend":
        try:
            suspend_member(args.id)
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        print(f"Suspended '{args.id}'. Reversible with 'unsuspend' -- no new key needed to restore access.")
        return 0

    if args.cmd == "unsuspend":
        try:
            unsuspend_member(args.id)
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        print(f"Unsuspended '{args.id}'. Access restored immediately with their existing key.")
        return 0

    if args.cmd == "rotate":
        try:
            token = rotate_member(args.id)
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        print(f"Rotated '{args.id}'. New token (shown once, not stored anywhere in plaintext):\n\n  {token}\n")
        print("The old token stops working immediately. Give this to them directly (not email/Slack in plaintext).")
        return 0

    if args.cmd == "list":
        members = list_members()
        if not members:
            print("No team members yet.")
            return 0
        for m in members:
            status = "REVOKED" if m.get("revoked_at") else ("SUSPENDED" if m.get("suspended_at") else "active")
            owner_tag = " [owner]" if m.get("is_owner") else ""
            print(f"  {m['id']:<15} {m['name']:<25} {m['email']:<30} [{status}]{owner_tag}")
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
