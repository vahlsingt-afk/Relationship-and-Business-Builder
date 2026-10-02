#!/usr/bin/env python3
"""
source_watch.py — inventory local watched folders for RB ingestion.

This is intentionally conservative. It does not parse transcripts or mutate RB
state. It only records matching files, hashes, freshness, and routing hints so
the morning pipeline can tell whether Zoom/Fathom artifacts are available.

Usage:
    python3 system/scripts/source_watch.py --json
    python3 system/scripts/source_watch.py --cache --json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "system" / "source_watch.yaml"
CACHE_PATH = ROOT / "system" / ".cache" / "source_watch.json"


def parse_scalar(value: str) -> Any:
    value = value.strip()
    if value in {"true", "True"}:
        return True
    if value in {"false", "False"}:
        return False
    if value in {"[]", ""}:
        return [] if value == "[]" else ""
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [part.strip().strip("\"'") for part in inner.split(",")]
    try:
        return int(value)
    except ValueError:
        return value.strip("\"'")


def load_watch_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    """Parse the small YAML subset used by system/source_watch.yaml.

    Avoiding a PyYAML dependency keeps refresh_all usable on a clean Mac.
    """
    if not path.exists():
        raise FileNotFoundError(f"Missing source watch config: {path}")

    lines = path.read_text(encoding="utf-8").splitlines()
    config: dict[str, Any] = {"watch_folders": {}}
    section: str | None = None
    current_name: str | None = None
    current_list_key: str | None = None

    for raw in lines:
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()

        if indent == 0 and stripped.endswith(":"):
            section = stripped[:-1]
            current_name = None
            current_list_key = None
            config.setdefault(section, {})
            continue

        if section != "watch_folders":
            if indent == 0 and ":" in stripped:
                key, value = stripped.split(":", 1)
                config[key.strip()] = parse_scalar(value)
            elif indent == 2 and ":" in stripped:
                key, value = stripped.split(":", 1)
                config.setdefault(section or "root", {})[key.strip()] = parse_scalar(value)
            continue

        if indent == 2 and stripped.endswith(":"):
            current_name = stripped[:-1]
            config["watch_folders"][current_name] = {}
            current_list_key = None
            continue

        if current_name is None:
            continue

        if indent == 4 and ":" in stripped:
            key, value = stripped.split(":", 1)
            key = key.strip()
            value = value.strip()
            if value == "":
                config["watch_folders"][current_name][key] = []
                current_list_key = key
            else:
                config["watch_folders"][current_name][key] = parse_scalar(value)
                current_list_key = None
            continue

        if indent == 6 and stripped.startswith("- ") and current_list_key:
            config["watch_folders"][current_name][current_list_key].append(parse_scalar(stripped[2:]))

    return config


def resolve_path(raw_path: str) -> Path:
    path = Path(os.path.expanduser(raw_path))
    if not path.is_absolute():
        path = ROOT / path
    return path


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def artifact_kind(path: Path) -> str:
    suffix = path.suffix.lower()
    name = path.name.lower()
    if suffix in {".mp3", ".m4a", ".wav"}:
        return "audio"
    if suffix in {".mp4", ".mov", ".webm"}:
        return "video"
    if suffix in {".vtt", ".srt"}:
        return "transcript"
    if "chat" in name:
        return "chat"
    if suffix in {".txt", ".md", ".pdf", ".docx"}:
        return "transcript_or_notes"
    if suffix in {".csv", ".json"}:
        return "metadata_or_chat"
    return "unknown"


def iter_files(base: Path, recursive: bool) -> list[Path]:
    if not base.exists() or not base.is_dir():
        return []
    pattern = "**/*" if recursive else "*"
    return sorted(p for p in base.glob(pattern) if p.is_file())


def scan(config: dict[str, Any]) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    defaults = config.get("defaults", {})
    rows: list[dict[str, Any]] = []
    source_states: list[dict[str, Any]] = []

    for name, entry in config.get("watch_folders", {}).items():
        enabled = bool(entry.get("enabled", defaults.get("enabled", True)))
        raw_path = str(entry.get("path", ""))
        base = resolve_path(raw_path) if raw_path else ROOT
        recursive = bool(entry.get("recursive", defaults.get("recursive", True)))
        suffixes = {str(s).lower() for s in entry.get("file_types", [])}
        hints = [str(h).lower() for h in entry.get("filename_hints", [])]
        excludes = [str(e).lower() for e in entry.get("filename_excludes", [])]
        require_hint = bool(entry.get("require_filename_hint", False))

        source_rows: list[dict[str, Any]] = []
        exists = base.exists() and base.is_dir()
        if enabled and exists:
            for path in iter_files(base, recursive):
                if suffixes and path.suffix.lower() not in suffixes:
                    continue
                lowered = path.name.lower()
                if excludes and any(exclude in lowered for exclude in excludes):
                    continue
                if require_hint and hints and not any(hint in lowered for hint in hints):
                    continue
                stat = path.stat()
                mtime = datetime.fromtimestamp(stat.st_mtime, timezone.utc)
                row = {
                    "watch_id": name,
                    "label": entry.get("label", name),
                    "source_type": entry.get("source_type", name),
                    "artifact_kind": artifact_kind(path),
                    "path": str(path),
                    "route_to": str(resolve_path(str(entry.get("route_to", "")))) if entry.get("route_to") else None,
                    "size_bytes": stat.st_size,
                    "modified_at": mtime.isoformat(),
                    "age_hours": round((now - mtime).total_seconds() / 3600, 2),
                    "sha256": sha256_file(path),
                }
                rows.append(row)
                source_rows.append(row)

        newest = min((r["age_hours"] for r in source_rows), default=None)
        source_states.append(
            {
                "watch_id": name,
                "label": entry.get("label", name),
                "source_type": entry.get("source_type", name),
                "enabled": enabled,
                "path": str(base),
                "exists": exists,
                "file_count": len(source_rows),
                "newest_age_hours": newest,
                "notes": entry.get("notes", ""),
            }
        )

    return {
        "generated_at": now.isoformat(),
        "config_path": str(CONFIG_PATH),
        "total_files": len(rows),
        "by_source": source_states,
        "files": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", action="store_true", help="Write system/.cache/source_watch.json")
    parser.add_argument("--json", action="store_true", help="Print JSON")
    args = parser.parse_args()

    report = scan(load_watch_config())
    if args.cache:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.json or not args.cache:
        print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
