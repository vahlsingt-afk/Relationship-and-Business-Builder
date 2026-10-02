"""
Shared helpers for the Master Account Plan engine (parse_workbook.py,
create_plan.py, impact_review.py, render.py).

RB-2026-08-28: a Master Account Plan is vendor/partner-scoped (e.g.
"worldpay"), not one of Todd's own single-account Blue Sheets -- keyed by
vendor_slug throughout, deliberately different vocabulary from
blue_sheets/_engine/common.py's account_slug so the two are harder to
confuse in code and in chat tool parameters. This module intentionally does
NOT import blue_sheets/_engine/common.py -- same reasoning Account Research
got its own independent common.py: an incident earlier this session (an
empty, wrongly-authorized Blue Sheet created from a portfolio-level upload)
showed that blurring these domains is exactly the failure mode to avoid.
"""
from __future__ import annotations

import datetime
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # .../master_account_plans

sys.path.insert(0, str(ROOT.parent / "system" / "scripts"))
import slug_safety  # noqa: E402


def vendor_dir(vendor_slug: str) -> Path:
    # RB-SECURITY-2026-09-05: defense-in-depth -- create_plan.py's own
    # caller-facing entry point validates vendor_slug too, but this is the
    # single choke point every OTHER vendor-path lookup in this engine
    # goes through, so it gets the same guard rather than depending on
    # every future caller remembering to validate first.
    slug_safety.assert_safe_slug(vendor_slug, label="vendor_slug")
    d = ROOT / "vendors" / vendor_slug
    if not d.is_dir():
        raise FileNotFoundError(f"No Master Account Plan folder for vendor slug '{vendor_slug}' at {d}")
    return d


def load_json(path: Path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def load_jsonl(path: Path):
    if not path.exists():
        return []
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def append_jsonl(path: Path, record) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False))
        f.write("\n")


def load_plan(vendor_slug: str) -> dict:
    d = vendor_dir(vendor_slug)
    return {
        "plan": load_json(d / "plan.json"),
        "ranked_portfolio": load_json(d / "ranked_portfolio.json"),
        "rm_portfolios": load_json(d / "rm_portfolios.json"),
        "stack_intelligence": load_json(d / "stack_intelligence.json"),
        "source_snapshot": load_json(d / "source_snapshot.json"),
        "contradictions": load_json(d / "contradictions.json"),
        "evidence": load_jsonl(d / "evidence.jsonl"),
    }


def registry_path() -> Path:
    return ROOT / "_portfolio" / "master_account_plan_registry.json"


def load_registry() -> dict:
    p = registry_path()
    if not p.exists():
        return {"registry": []}
    return load_json(p)


def review_queue_path() -> Path:
    return ROOT / "_portfolio" / "review_queue.json"


def load_review_queue() -> dict:
    p = review_queue_path()
    if not p.exists():
        return {"pending_reviews": []}
    return load_json(p)


def now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def today() -> str:
    return datetime.date.today().isoformat()


def next_evidence_id(vendor_slug: str, evidence: list) -> str:
    existing = [e["evidence_id"] for e in evidence if e.get("evidence_id", "").startswith(f"ev-{vendor_slug}-")]
    n = 0
    for eid in existing:
        try:
            n = max(n, int(eid.rsplit("-", 1)[-1]))
        except ValueError:
            pass
    return f"ev-{vendor_slug}-{n + 1:04d}"
