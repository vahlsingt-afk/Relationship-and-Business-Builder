#!/usr/bin/env python3
"""
brief_repair.py — deterministic repair actions for brief_acceptance_check.py's
structured, safe_to_auto_repair findings (RB-DEFECT-072).

Built after a real, live incident (2026-09-23): the morning pipeline
generated both the intelligence and daily briefs successfully, but two
same-event headline pairs (differently worded, different URLs) both survived
render into the final intelligence-brief.md, tripping
check_no_duplicate_story_clusters and aborting delivery entirely instead of
repairing and continuing. See the RB-DEFECT-072 report for the full incident
and required-fix spec.

Deliberately NOT a re-invocation of render_intelligence_brief.py: that
5900+-line renderer already has its own per-section duplicate-avoidance
logic (topic-cluster dedup within _render_headline_section, title-based
dedup within _render_gp_intel) and STILL let these two pairs through,
because each duplicate's two occurrences came from different sections with
independent dedup state (one in "C: Restaurant Industry" vs "D+: Curated
Trade Reads"; one twice within "K: GP/Genius — Field Intelligence" via two
different upstream pools) — no single per-section dedup pass can catch a
cross-section duplicate. Re-running the full renderer would very likely
reproduce the identical output, since nothing about the underlying
selection logic changed. Instead this repairs the ALREADY-RENDERED markdown
directly: find every real headline entry, cluster same-event entries using
the exact same rule brief_acceptance_check.cluster_duplicate_headlines
already applies (imported, not reimplemented, so repair can never "pass" a
case the gate would still flag), keep the most complete entry per cluster,
and remove the others' full rendered blocks.

This also naturally satisfies the "resume from render stage, not collection"
requirement: no collection, assessment, or render subprocess is invoked at
all — this operates purely on the text already on disk.
"""
from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import brief_acceptance_check as bac  # noqa: E402

BRIEFS_DIR = bac.BRIEFS_DIR

# A line boundary that ends one headline entry's block, wherever it appears
# before the *next* title-link line: a horizontal rule, the next top-level
# section header, or the "*N item(s) this cycle.*" trailer
# _render_headline_section appends after a short section.
_BLOCK_BOUNDARY_RE = re.compile(
    r"^(---\s*$|## |\*\d+ items? this cycle\.\*\s*$)", re.MULTILINE
)


def _entry_positions(md: str) -> list[dict]:
    """Every real headline-link line in `md`, in document order, with its
    (title, url) and the character offset where its line starts."""
    entries = []
    for m in bac._TITLE_LINK_RE.finditer(md):
        entries.append({
            "title": m.group(1),
            "url": m.group(2),
            "line_start": m.start(),
            "line_end": m.end(),
        })
    return entries


def _block_span(md: str, entries: list[dict], idx: int) -> tuple[int, int]:
    """Full char span of entry idx's rendered block: from its title-link
    line start to just before whichever comes first -- the next entry's
    line start, or a section/trailer boundary."""
    start = entries[idx]["line_start"]
    limit = entries[idx + 1]["line_start"] if idx + 1 < len(entries) else len(md)
    boundary = _BLOCK_BOUNDARY_RE.search(md, entries[idx]["line_end"], limit)
    end = boundary.start() if boundary else limit
    return start, end


def _entry_richness(md: str, entries: list[dict], idx: int) -> tuple:
    """Deterministic canonical-selection ranking (RB-DEFECT-072 spec: prefer
    the most complete/direct entry). No upstream source-authority score is
    available at this text-only repair stage, so richness is judged from
    what actually rendered: has a synthesized "why it matters" line, has a
    "Read more" direct-article link, then raw block length (a fuller entry
    carries more real content), then first-occurrence order as the final,
    fully deterministic tie-break (never random, never "last wins")."""
    start, end = _block_span(md, entries, idx)
    block = md[start:end]
    has_why = "**Why it matters:**" in block
    has_read_more = "Read more" in block
    return (has_why, has_read_more, len(block), -idx)


def repair_duplicate_story_clusters(md: str) -> dict:
    """Remove all but the most complete entry from every same-event cluster
    in `md`. Returns {"repaired_markdown", "changed", "removed", "kept"}.

    `removed`: one entry per dropped headline — {title, url, kept_title,
    kept_url} — the repair receipt morning_pipeline.py persists.
    """
    entries = _entry_positions(md)
    headline_pairs = [(e["title"], e["url"]) for e in entries]
    clusters = bac.cluster_duplicate_headlines(headline_pairs)
    dupe_clusters = [c for c in clusters if len(c) > 1]

    if not dupe_clusters:
        return {"repaired_markdown": md, "changed": False, "removed": [], "kept": []}

    removals: list[dict] = []  # (start, end, removed_entry, kept_entry)
    kept_receipts: list[dict] = []
    for cluster in dupe_clusters:
        winner = max(cluster, key=lambda i: _entry_richness(md, entries, i))
        kept_receipts.append({"title": entries[winner]["title"], "url": entries[winner]["url"]})
        for idx in cluster:
            if idx == winner:
                continue
            start, end = _block_span(md, entries, idx)
            removals.append((start, end, entries[idx], entries[winner]))

    # Remove from the end of the document backward so earlier offsets stay valid.
    removals.sort(key=lambda r: r[0], reverse=True)
    repaired = md
    removed_receipts = []
    for start, end, removed_entry, kept_entry in removals:
        repaired = repaired[:start] + repaired[end:]
        removed_receipts.append({
            "title": removed_entry["title"], "url": removed_entry["url"],
            "kept_title": kept_entry["title"], "kept_url": kept_entry["url"],
        })
    # Report in document order, not removal (reverse) order.
    removed_receipts.reverse()

    # Collapse any run of 3+ blank lines the removal may have left behind
    # (two consecutive entries removed back-to-back) down to a normal single
    # blank-line separator, so the repaired document still reads cleanly.
    repaired = re.sub(r"\n{4,}", "\n\n\n", repaired)

    return {
        "repaired_markdown": repaired,
        "changed": True,
        "removed": removed_receipts,
        "kept": kept_receipts,
    }


def _artifact_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def repair_brief_file(target_date, doc_key: str) -> dict:
    """Load system/briefs/{date}-{doc_key}-brief.md, repair duplicate story
    clusters, and write it back in place if anything changed. `doc_key` is
    "intelligence" or "daily", matching brief_acceptance_check's own naming.

    Returns a full repair receipt including before/after artifact hashes
    (RB-DEFECT-072 acceptance criteria: "each repair attempt leaves a
    receipt naming ... before/after artifact hash").
    """
    path = BRIEFS_DIR / f"{target_date.isoformat()}-{doc_key}-brief.md"
    if not path.exists():
        return {"doc": doc_key, "path": str(path), "changed": False,
                "reason": "artifact_missing"}
    before_text = path.read_text(encoding="utf-8")
    before_hash = _artifact_hash(before_text)
    outcome = repair_duplicate_story_clusters(before_text)
    if not outcome["changed"]:
        return {"doc": doc_key, "path": str(path), "changed": False,
                "reason": "no_duplicate_clusters_found",
                "artifact_hash_before": before_hash, "artifact_hash_after": before_hash}
    path.write_text(outcome["repaired_markdown"], encoding="utf-8")
    after_hash = _artifact_hash(outcome["repaired_markdown"])
    return {
        "doc": doc_key, "path": str(path), "changed": True,
        "action": "dedup_story_clusters",
        "removed": outcome["removed"], "kept": outcome["kept"],
        "artifact_hash_before": before_hash, "artifact_hash_after": after_hash,
    }
