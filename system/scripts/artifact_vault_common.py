"""
artifact_vault_common.py — shared storage/versioning primitives for the
new artifact vault (RB-2026-09-07, per Todd's direct ask: "each of these
artifacts needs to be stored in an appropriate container and indexed for
retrieval... a single vault for all artifacts with subfolders... with
strong naming conventions").

Generalizes account_background_brief.py's proven current/history/
registry.json versioning pattern (archive-before-overwrite, mark prior
versions superseded, never delete) into a reusable helper, parameterized
by artifact type, so the new customer-side artifact types (Account Plan,
Green Sheet, Win Plan, RFP Response Plan) don't each hand-roll the same
logic. account_background_brief.py itself is deliberately NOT changed to
use this -- it's working, live code; staying on its own copy avoids
destabilizing it for a purely-cosmetic consolidation.

Layout: system/artifact_vault/<artifact_type_dir>/<slug>/
    current/<Display-Name>_<Artifact-Type-Title>.<ext>
    history/<UTC-timestamp>_v<N>_<Display-Name>_<Artifact-Type-Title>.<ext>
    registry.json   {subject_id, versions: [{version, generated_at,
                     generated_for, purpose, path, superseded}]}

subject_id is deliberately generic (not account_id) -- RB-2026-09-07's
competitive-side artifacts (battle_card.py, competitive_brief.py) key this
vault by category/competitor slug, not an account slug.

Indexing is NOT handled here -- system/artifacts/registry.json's own live
API (getArtifact/listArtifacts) is deliberately scoped to micro graphs and
account dossiers only (a real, documented boundary from a past incident:
DEFECT-014's own docstring warns it is "NOT a comprehensive inventory").
The actually-comprehensive surface is system/scripts/intelligence_index.py
-- each new artifact type's own module is responsible for registering
there (see account_plan.py for the pattern), not this shared module.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

VAULT_ROOT = Path(__file__).resolve().parent.parent / "artifact_vault"  # .../system/artifact_vault


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def today() -> str:
    return date.today().isoformat()


def filename_slug(display_name: str) -> str:
    """Title-Case-With-Hyphens, for the strong-naming-convention filename
    (e.g. "Pollo Campero" -> "Pollo-Campero"). Deliberately distinct from
    ecosystem_intelligence.py's _slug() (lowercase-hyphen directory-slug
    convention) -- this one is for a human-readable filename meant to
    self-identify the account outside its folder context (e.g. in a flat
    Drive export), not a URL/path segment."""
    cleaned = re.sub(r"[^A-Za-z0-9\s-]", "", display_name).strip()
    words = re.split(r"[\s-]+", cleaned)
    return "-".join(w for w in words if w)


def instance_dir(artifact_type_dir: str, slug: str, *, create: bool = False) -> Path:
    d = VAULT_ROOT / artifact_type_dir / slug
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d


def registry_path(artifact_type_dir: str, slug: str) -> Path:
    return instance_dir(artifact_type_dir, slug) / "registry.json"


def _load_json(path: Path, default):
    if not path.exists():
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_current_version(artifact_type_dir: str, slug: str, *, include_content: bool = False) -> Optional[dict]:
    reg = _load_json(registry_path(artifact_type_dir, slug), None)
    if reg is None:
        return None
    current = [v for v in reg.get("versions", []) if not v.get("superseded")]
    if not current:
        return None
    entry = dict(current[-1])
    if include_content:
        content_path = VAULT_ROOT.parent.parent / entry["path"]
        entry["content"] = content_path.read_text(encoding="utf-8") if content_path.exists() else None
    return entry


def register_version(
    artifact_type_dir: str, artifact_type_title: str, slug: str, display_name: str, content: str,
    *, generated_for: str = "", purpose: str = "", file_extension: str = "md",
) -> dict:
    """Writes `content` to current/, archives the prior current version (if
    one exists) to history/ and marks every registry entry superseded,
    then appends the new version entry. Mirrors account_background_brief
    .py's register_brief_version() exactly -- same archive-before-
    overwrite, same "never delete" discipline.

    No-op guard (2026-09-25): if `content` is byte-identical to the
    current persisted version, returns that existing entry unchanged
    instead of writing a new one. This is what makes routine/scheduled
    regeneration (see refresh_persisted_briefs.py, called daily from
    morning_pipeline.py) safe to run unconditionally every day rather than
    needing its own separate "did anything actually change" logic --
    without this, 365 daily re-renders of an unchanged document would
    each register a new version, burying genuine changes under a year of
    identical noise in history/."""
    d = instance_dir(artifact_type_dir, slug, create=True)
    current_dir = d / "current"
    history_dir = d / "history"
    current_dir.mkdir(exist_ok=True)
    history_dir.mkdir(exist_ok=True)

    filename_base = f"{filename_slug(display_name)}_{artifact_type_title.replace(' ', '-')}"
    current_path = current_dir / f"{filename_base}.{file_extension}"
    reg_path = registry_path(artifact_type_dir, slug)
    reg = _load_json(reg_path, {"subject_id": f"subject-{slug}", "versions": []})

    if current_path.exists() and current_path.read_text(encoding="utf-8") == content:
        current = [v for v in reg["versions"] if not v.get("superseded")]
        if current:
            return current[-1]

    if current_path.exists():
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        prior_version = len(reg["versions"])
        archive_path = history_dir / f"{ts}_v{prior_version}_{filename_base}.{file_extension}"
        archive_path.write_text(current_path.read_text(encoding="utf-8"), encoding="utf-8")
        for v in reg["versions"]:
            v["superseded"] = True

    current_path.write_text(content, encoding="utf-8")
    version_num = len(reg["versions"]) + 1
    entry = {
        "version": version_num,
        "generated_at": _now_iso(),
        "generated_for": generated_for,
        "purpose": purpose,
        "path": str(current_path.relative_to(VAULT_ROOT.parent.parent)),
        "superseded": False,
    }
    reg["versions"].append(entry)
    reg_path.write_text(json.dumps(reg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return entry
