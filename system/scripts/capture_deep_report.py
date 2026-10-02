#!/usr/bin/env python3
"""
capture_deep_report.py — on-demand, CoS-level deep report for a single capture.

transcript_summarizer.py's summarize_transcript() is deliberately terse (6
fixed fields, ~800-token output budget) because it feeds the daily brief,
where dozens of items must stay scannable. This module is the opposite:
invoked on demand for a single call the user cares about, it produces a full
narrative Chief-of-Staff report — not a capped extraction — covering what was
actually discussed, who's involved and what RB already knows about them,
decisions/commitments with reasoning, and strategic implications. This is
the "manual trigger + deeper report" workflow requested 2026-07-20 after the
IKEA call: same-day latency is fine for routine recordings, but an important
call needs (a) processing right now, not at the next 4 AM sweep, and (b) a
report with the depth of a manually-read transcript, not the brief's terse
per-item summary.

Usage:
    python3 system/scripts/capture_deep_report.py --file-id cap-xxxx
    python3 system/scripts/capture_deep_report.py --latest
    python3 system/scripts/capture_deep_report.py --path "/path/to/new.m4a" --queue-source just_press_record
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
SYSTEM_DIR = SCRIPTS_DIR.parent
sys.path.insert(0, str(SYSTEM_DIR / "api"))
sys.path.insert(0, str(SCRIPTS_DIR))
import rb_core as core  # noqa: E402
import capture_ingest  # noqa: E402
import ecosystem_intelligence as eco  # noqa: E402

MODEL = "gpt-4o"
# On-demand, single-call, user-requested — no brief-scannability constraint,
# so this budget is generous by design rather than cost-optimized.
MAX_TRANSCRIPT_CHARS = 100_000
MAX_OUTPUT_TOKENS = 4000

_SYSTEM_PROMPT = (
    "You are an exceptional Chief of Staff writing a post-call report for an "
    "executive. You read a raw transcript of a real business conversation "
    "(often messy or informal speech-to-text) and produce a thorough, "
    "substantive report — the kind a sharp human COS would write after "
    "sitting in on the call and doing the homework to connect it to what "
    "the executive already knows. Never invent people, companies, "
    "decisions, or facts that are not in the transcript or the supplied "
    "known-context. Be specific and concrete — quote or closely paraphrase "
    "key moments rather than describing them abstractly. If the call was "
    "genuinely thin on substance, say so plainly rather than padding."
)

_USER_PROMPT_TEMPLATE = """Write a detailed Chief-of-Staff report on this call/recording.

{known_context_block}
Structure the report in Markdown with these sections:

## Overview
2-4 sentences: what this call was, who was on it, and the headline outcome.

## Attendees & Context
For each real person discussed or present, note their role/company and, if
given in Known RB Context above, how they relate to the user's existing
relationships or accounts.

## Detailed Discussion
The substantive body of the report — walk through what was actually
discussed, in enough detail that someone who didn't take the call
understands the real content, not just topic labels. Use sub-headers per
topic if the call covered multiple distinct subjects.

## Decisions & Commitments
Concrete decisions or commitments made, with the reasoning behind them where
stated. Say "None made" if there were none.

## Open Questions / Risks
Anything left unresolved, ambiguous, or that poses a risk if not followed up.

## Strategic Implications
Why this call matters — connections to existing accounts, competitors,
opportunities, or relationships, drawing on Known RB Context where relevant.

## Recommended Next Actions
Specific, owned next steps. Say "None" if genuinely none are warranted.

Title/hint for this recording: {title_hint}

Transcript:
{transcript}
"""


def _load_capture(file_id: str) -> dict | None:
    data = capture_ingest.get_pending(file_id)
    if data is not None:
        return data
    path = SYSTEM_DIR / "captures" / "processed" / f"{file_id}.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def _latest_capture() -> tuple[str, dict] | None:
    """Most recent capture by queued_at, checking pending first then processed."""
    pending = capture_ingest.list_pending(limit=1)
    if pending:
        fid = pending[0]["file_id"]
        return fid, capture_ingest.get_pending(fid)

    processed_dir = SYSTEM_DIR / "captures" / "processed"
    candidates = sorted(
        processed_dir.glob("cap-*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        return None
    fid = candidates[0].stem
    try:
        return fid, json.loads(candidates[0].read_text(encoding="utf-8"))
    except Exception:
        return None


def _known_context(data: dict) -> str:
    """Cross-reference already-extracted people/companies against RB's own
    baseline contacts and ecosystem entities, so the report can say who
    someone already is to the user rather than treating the call in
    isolation. Uses the llm_summary's extracted names (already computed by
    transcript_summarizer) rather than re-scanning the full transcript
    against 3000+ baseline contacts.
    """
    # mark_processed() stores this under "processing_result" (not "result");
    # pending captures (not yet processed) won't have it at all yet.
    llm_summary = (data.get("processing_result") or {}).get("llm_summary") or {}
    people = llm_summary.get("people_mentioned") or []
    companies = llm_summary.get("companies_mentioned") or []
    if not people and not companies:
        return ""

    lines: list[str] = []

    if people:
        baseline = core.load_baseline()
        by_name = {c.get("name", "").lower(): c for c in baseline if c.get("name")}
        for person in people:
            contact = by_name.get(person.lower())
            if not contact:
                # loose match: person's name is a substring of a baseline name or vice versa
                contact = next(
                    (c for c in baseline
                     if c.get("name") and (person.lower() in c["name"].lower() or c["name"].lower() in person.lower())),
                    None,
                )
            if contact:
                lines.append(
                    f"- {person}: existing contact — {contact.get('current_role') or 'role unknown'} "
                    f"at {contact.get('current_company') or 'unknown company'}."
                )

    if companies:
        try:
            graph = eco._read_graph()
        except Exception:
            graph = {}
        entities = graph.get("entities") or []
        by_name = {e.get("name", "").lower(): e for e in entities if e.get("name")}
        for company in companies:
            entity = by_name.get(company.lower())
            if entity:
                rels = [
                    r for r in (graph.get("relationships") or [])
                    if r.get("from_entity_id") == entity.get("id") or r.get("to_entity_id") == entity.get("id")
                ]
                rel_note = f" {len(rels)} known vendor/relationship record(s) in the ecosystem graph." if rels else ""
                lines.append(f"- {company}: known {entity.get('entity_type', 'entity')} in RB's ecosystem graph.{rel_note}")

    if not lines:
        return ""
    return "Known RB Context (use this to ground Attendees & Strategic Implications — do not restate it verbatim):\n" + "\n".join(lines) + "\n\n"


def generate_deep_report(transcript: str, title_hint: str, known_context: str = "") -> str | None:
    """Best-effort deep CoS report. Returns None (never raises) if unavailable."""
    if not transcript or not transcript.strip():
        return None

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None
    try:
        from openai import OpenAI
    except ImportError:
        return None

    text = transcript.strip()
    truncated = len(text) > MAX_TRANSCRIPT_CHARS
    if truncated:
        text = text[:MAX_TRANSCRIPT_CHARS]

    try:
        client = OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": _USER_PROMPT_TEMPLATE.format(
                        known_context_block=known_context,
                        title_hint=title_hint or "(untitled)",
                        transcript=text,
                    ),
                },
            ],
            temperature=0.3,
            max_tokens=MAX_OUTPUT_TOKENS,
        )
        report = response.choices[0].message.content.strip()
    except Exception as exc:
        print(f"[capture_deep_report] LLM call failed: {exc}", file=sys.stderr)
        return None

    if truncated:
        report += (
            "\n\n---\n*Note: the source transcript exceeded "
            f"{MAX_TRANSCRIPT_CHARS:,} characters and was truncated before "
            "this report was generated — the tail of the recording may not "
            "be reflected above.*"
        )
    return report


def _report_path(file_id: str) -> Path:
    return SYSTEM_DIR / "captures" / "processed" / f"{file_id}.deep_report.md"


def run(file_id: str) -> dict:
    data = _load_capture(file_id)
    if data is None:
        return {"status": "error", "error": f"capture not found: {file_id}"}
    transcript = data.get("transcript", "")
    if not transcript:
        return {"status": "error", "error": "no transcript available for this capture"}

    known_context = _known_context(data)
    report = generate_deep_report(transcript, data.get("title_hint", ""), known_context)
    if report is None:
        return {
            "status": "error",
            "error": "deep report generation unavailable (no OPENAI_API_KEY, "
                     "openai package missing, or API call failed)",
        }

    report_path = _report_path(file_id)
    report_path.write_text(report, encoding="utf-8")
    return {"status": "generated", "file_id": file_id, "report_path": str(report_path), "report": report}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--file-id", help="Existing pending/processed capture ID")
    parser.add_argument("--latest", action="store_true", help="Use the most recent capture")
    parser.add_argument("--path", help="Local audio/text file not yet swept — queue it now, process it, then report")
    parser.add_argument("--queue-source", default="just_press_record",
                         help="Source ID to use with --path (default: just_press_record)")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON instead of the report text")
    args = parser.parse_args()

    file_id: str | None = args.file_id

    if args.path:
        queue_result = capture_ingest.queue_file(Path(args.path).expanduser().resolve(), source_id=args.queue_source)
        if queue_result["status"] not in ("queued", "already_queued"):
            print(json.dumps(queue_result, indent=2))
            return 1
        file_id = queue_result["file_id"]
        # Run the same triage/llm_summary/persist step the daily pipeline
        # runs — reused directly, not duplicated, so this manual path stays
        # in lockstep with process_all_pending_captures().
        import server  # noqa: E402 — path set up above
        submit_result = server.post_capture_submit(file_id, x_api_key=None)
        if not args.json:
            print(f"[capture_deep_report] Queued and processed {file_id}: "
                  f"{submit_result.get('triage', {}).get('type_count', 0)} intelligence stream(s).",
                  file=sys.stderr)
    elif args.latest:
        found = _latest_capture()
        if found is None:
            print(json.dumps({"status": "error", "error": "no captures found"}))
            return 1
        file_id = found[0]

    if not file_id:
        parser.error("one of --file-id, --latest, or --path is required")

    result = run(file_id)

    if args.json:
        print(json.dumps(result, indent=2))
    elif result["status"] == "generated":
        print(result["report"])
        print(f"\n[saved to {result['report_path']}]", file=sys.stderr)
    else:
        print(f"ERROR: {result.get('error')}", file=sys.stderr)

    return 0 if result["status"] == "generated" else 1


if __name__ == "__main__":
    sys.exit(main())
