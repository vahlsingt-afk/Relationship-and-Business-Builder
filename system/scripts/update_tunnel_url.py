#!/usr/bin/env python3
"""
update_tunnel_url.py - update RB OpenAPI tunnel URLs.

Usage:
    python3 system/scripts/update_tunnel_url.py https://example.trycloudflare.com

Updates both OpenAPI specs and, when currently null, the operational public URL
base in settings.json. Prints a unified diff. Does not commit.
"""
from __future__ import annotations

import argparse
import difflib
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

SYSTEM_DIR = Path(__file__).resolve().parent.parent
FILES = [
    SYSTEM_DIR / "api" / "openapi_gpt.yaml",
    SYSTEM_DIR / "api" / "openapi.yaml",
]
SETTINGS_PATH = SYSTEM_DIR / "settings.json"


def _validate_url(url: str) -> str:
    clean = url.rstrip("/")
    parsed = urlparse(clean)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("new URL must be an https base URL, e.g. https://rb-api.example.com")
    return clean


def _replace_first_server_url(text: str, new_url: str) -> str:
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("- url:") or stripped.startswith("url:"):
            prefix = line.split("url:", 1)[0]
            newline = "\n" if line.endswith("\n") else ""
            lines[index] = f"{prefix}url: {new_url}{newline}"
            return "".join(lines)
    raise ValueError("no servers[0].url line found")


def _settings_update(text: str, new_url: str) -> str:
    # Preserve hand-edited formatting. The repo currently stores this as a JSON
    # line under daily_briefing.operational_layer.
    return re.sub(
        r'"public_url_base": (null|"[^"]*")',
        f'"public_url_base": "{new_url}"',
        text,
        count=1,
    )


def _diff(path: Path, before: str, after: str) -> str:
    if before == after:
        return ""
    return "".join(difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile=str(path),
        tofile=str(path),
    ))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("new_url", help="New tunnel base URL, e.g. https://rb-api.example.com")
    args = parser.parse_args()
    try:
        new_url = _validate_url(args.new_url)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    updates: list[tuple[Path, str, str]] = []
    for path in FILES:
        before = path.read_text(encoding="utf-8")
        after = _replace_first_server_url(before, new_url)
        updates.append((path, before, after))

    settings_before = SETTINGS_PATH.read_text(encoding="utf-8")
    settings_after = _settings_update(settings_before, new_url)
    updates.append((SETTINGS_PATH, settings_before, settings_after))

    diff = "".join(_diff(path, before, after) for path, before, after in updates)
    if not diff:
        print("No changes.")
        return 0

    for path, _before, after in updates:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(after, encoding="utf-8")
        tmp.replace(path)

    print(diff, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
