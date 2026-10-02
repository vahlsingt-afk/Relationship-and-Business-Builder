#!/usr/bin/env python3
"""
legacy_transfer_watch.py — inventory legacy RB transfer artifacts.

This is a watch layer, not an ingestion layer. It scans the git-ignored
`RB 8.0 transfer data/` folder, compares the current file inventory with the
last cached run, and reports new/changed/removed files for review.

Usage:
    python3 legacy_transfer_watch.py
    python3 legacy_transfer_watch.py --json --cache
    python3 legacy_transfer_watch.py --root "RB 8.0 transfer data"
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rb_core as core

CACHE_NAME = "legacy_transfer_watch"
DEFAULT_SOURCE_DIR = core.PROJECT_DIR / "RB 8.0 transfer data"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_prior_payload() -> dict[str, Any] | None:
    """Read prior watch cache without baseline-mtime invalidation.

    Folder deltas should compare against the last watch pass even if the
    baseline changed in between.
    """
    path = core.cache_path(CACHE_NAME)
    if not path.exists():
        return None
    try:
        env = json.loads(path.read_text())
    except json.JSONDecodeError:
        return None
    data = env.get("data")
    return data if isinstance(data, dict) else None


def file_record(path: Path, root: Path) -> dict[str, Any]:
    st = path.stat()
    rel = path.relative_to(root).as_posix()
    return {
        "path": rel,
        "extension": path.suffix.lower() or "(none)",
        "size_bytes": st.st_size,
        "modified_at": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(timespec="seconds"),
        "sha256": sha256_file(path),
    }


def scan(root: Path) -> list[dict[str, Any]]:
    if not root.exists():
        return []
    records = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.name.startswith("."):
            continue
        records.append(file_record(path, root))
    return records


def compare(current: list[dict[str, Any]], prior: dict[str, Any] | None) -> dict[str, list[dict[str, Any]]]:
    current_by_path = {r["path"]: r for r in current}
    prior_files = prior.get("files", []) if prior else []
    prior_by_path = {r["path"]: r for r in prior_files if isinstance(r, dict) and "path" in r}

    new_files = [current_by_path[p] for p in sorted(current_by_path.keys() - prior_by_path.keys())]
    removed_files = [prior_by_path[p] for p in sorted(prior_by_path.keys() - current_by_path.keys())]
    changed_files = []
    unchanged_files = []

    for p in sorted(current_by_path.keys() & prior_by_path.keys()):
        cur = current_by_path[p]
        old = prior_by_path[p]
        if cur.get("sha256") != old.get("sha256"):
            changed_files.append({
                **cur,
                "previous_size_bytes": old.get("size_bytes"),
                "previous_modified_at": old.get("modified_at"),
                "previous_sha256": old.get("sha256"),
            })
        else:
            unchanged_files.append(cur)

    return {
        "new_files": new_files,
        "changed_files": changed_files,
        "removed_files": removed_files,
        "unchanged_files": unchanged_files,
    }


def build_report(root: Path) -> dict[str, Any]:
    root = root.expanduser()
    if not root.is_absolute():
        root = core.PROJECT_DIR / root
    root = root.resolve()

    prior = load_prior_payload()
    files = scan(root)
    deltas = compare(files, prior)
    by_extension = Counter(r["extension"] for r in files)
    needs_review = (
        len(deltas["new_files"])
        + len(deltas["changed_files"])
        + len(deltas["removed_files"])
    )

    return {
        "scanned_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "root": str(root),
        "exists": root.exists(),
        "total_files": len(files),
        "by_extension": dict(sorted(by_extension.items())),
        "new_files": deltas["new_files"],
        "changed_files": deltas["changed_files"],
        "removed_files": deltas["removed_files"],
        "unchanged_files": len(deltas["unchanged_files"]),
        "needs_review": needs_review,
        "files": files,
    }


def print_text(report: dict[str, Any]) -> None:
    rel_root = Path(report["root"])
    try:
        root_display = rel_root.relative_to(core.PROJECT_DIR).as_posix()
    except ValueError:
        root_display = str(rel_root)

    print(f"Legacy transfer watch: {root_display}")
    if not report["exists"]:
        print("  Folder not found.")
        return
    print(f"  Files: {report['total_files']}  Needs review: {report['needs_review']}")
    if report["by_extension"]:
        ext_summary = ", ".join(f"{k}={v}" for k, v in report["by_extension"].items())
        print(f"  Extensions: {ext_summary}")

    for label, key in [
        ("New", "new_files"),
        ("Changed", "changed_files"),
        ("Removed", "removed_files"),
    ]:
        rows = report[key]
        if not rows:
            continue
        print(f"  {label}:")
        for row in rows:
            print(f"    - {row['path']}")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--root", default=str(DEFAULT_SOURCE_DIR), help="Transfer artifact folder to scan.")
    p.add_argument("--json", action="store_true", help="Print JSON report.")
    p.add_argument("--cache", action="store_true", help="Write report to system/.cache/legacy_transfer_watch.json.")
    args = p.parse_args()

    report = build_report(Path(args.root))
    if args.cache:
        core.write_cache(CACHE_NAME, report, source="legacy_transfer_watch.py")
    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print_text(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
