#!/usr/bin/env python3
"""Export RBB cockpit context to one dedicated Google Drive file.

Normal failures are recorded and return zero so this never blocks the pipeline.
One-time setup: python3 system/scripts/cockpit_drive_export.py --authorize
"""
from __future__ import annotations
import argparse, json, os, sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[2]
SYSTEM = ROOT / "system"
CONTEXT_PATH = SYSTEM / "cockpit/context.json"
STATE_PATH = SYSTEM / ".cache/cockpit_drive_export_state.json"
CLIENT_PATH = Path(os.environ.get("RB_COCKPIT_DRIVE_CLIENT_JSON", os.environ.get(
    "RB_GOOGLE_CLIENT", str(Path.home() / ".config/rb/google_client.json")))).expanduser()
TOKEN_PATH = Path(os.environ.get("RB_COCKPIT_DRIVE_TOKEN_JSON",
                                 str(Path.home() / ".config/rb/cockpit_drive_token.json"))).expanduser()
SCOPES = ["https://www.googleapis.com/auth/drive.file"]
FOLDER_NAME, FILE_NAME = "RBB Cockpit Export", "context.json"


def _write_state(state: dict, path: Path = STATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _credentials(*, authorize: bool):
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as exc:
        raise RuntimeError("Install google-auth, google-auth-oauthlib, and google-api-python-client") from exc
    creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES) if TOKEN_PATH.exists() else None
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    if creds and creds.valid:
        return creds
    if not authorize:
        raise RuntimeError(f"No valid Drive token at {TOKEN_PATH}; run with --authorize once")
    if not CLIENT_PATH.exists():
        raise RuntimeError(f"OAuth client JSON not found at {CLIENT_PATH}")
    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_PATH), SCOPES)
    creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
    TOKEN_PATH.chmod(0o600)
    return creds


def _find_or_create_target(service) -> tuple[str, str]:
    configured = os.environ.get("RB_COCKPIT_DRIVE_FILE_ID", "").strip()
    if configured:
        return configured, "configured_env"
    if STATE_PATH.exists():
        try:
            prior = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            if prior.get("file_id"):
                return str(prior["file_id"]), "state_file"
        except (OSError, ValueError):
            pass
    folders = service.files().list(
        q=(f"name='{FOLDER_NAME}' and mimeType='application/vnd.google-apps.folder' "
           "and 'root' in parents and trashed=false"),
        spaces="drive", fields="files(id,name)", pageSize=10).execute().get("files", [])
    folder_id = folders[0]["id"] if folders else service.files().create(
        body={"name": FOLDER_NAME, "mimeType": "application/vnd.google-apps.folder"},
        fields="id").execute()["id"]
    files = service.files().list(
        q=f"name='{FILE_NAME}' and '{folder_id}' in parents and trashed=false",
        spaces="drive", fields="files(id,name)", pageSize=10).execute().get("files", [])
    if files:
        return files[0]["id"], "dedicated_folder_existing"
    file_id = service.files().create(
        body={"name": FILE_NAME, "parents": [folder_id], "mimeType": "application/json"},
        fields="id").execute()["id"]
    return file_id, "dedicated_folder_created"


def export_context(*, service, file_id: str, context_path: Path = CONTEXT_PATH,
                   media_factory: Callable | None = None) -> dict:
    if not context_path.exists():
        raise FileNotFoundError(f"Cockpit context not found: {context_path}")
    json.loads(context_path.read_text(encoding="utf-8"))
    if media_factory is None:
        from googleapiclient.http import MediaFileUpload
        media_factory = MediaFileUpload
    media = media_factory(str(context_path), mimetype="application/json", resumable=False)
    return service.files().update(
        fileId=file_id, media_body=media, fields="id,modifiedTime,size").execute() or {"id": file_id}


def run(*, authorize: bool = False, strict: bool = False) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    state = {"status": "failed", "attempted_at": now, "source": "system/cockpit/context.json"}
    try:
        from googleapiclient.discovery import build
        service = build("drive", "v3", credentials=_credentials(authorize=authorize), cache_discovery=False)
        file_id, source = _find_or_create_target(service)
        result = export_context(service=service, file_id=file_id)
        state.update(status="success", last_export_at=now, file_id=file_id, target_source=source,
                     drive_modified_time=result.get("modifiedTime"), uploaded_size=result.get("size"), error=None)
    except Exception as exc:  # boundary is deliberately non-fatal
        state["error"] = f"{type(exc).__name__}: {exc}"
        _write_state(state)
        if strict:
            raise
        return state
    _write_state(state)
    return state


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--authorize", action="store_true")
    p.add_argument("--strict", action="store_true")
    args = p.parse_args()
    try:
        print(json.dumps(run(authorize=args.authorize, strict=args.strict)))
        return 0
    except Exception as exc:
        print(f"cockpit Drive export failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
