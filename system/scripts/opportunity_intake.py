#!/usr/bin/env python3
"""Review-first opportunity intake for relationship-led opportunities.

This module turns a high-signal opportunity note into an auditable mutation
plan and, when explicitly confirmed, applies the safe canonical projections:
contact/touch, active thread, loops, company artifact, opportunity artifact,
relationship edge, and append-only interaction brief.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutations  # noqa: E402
import rb_core as core  # noqa: E402


COMPANIES_DIR = core.SYSTEM_DIR / "companies"
OPPORTUNITIES_DIR = core.SYSTEM_DIR / "opportunities"
RELATIONSHIP_EDGES_PATH = core.SYSTEM_DIR / "relationship_edges.json"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-") or "unknown"


def _today_iso() -> str:
    return date.today().isoformat()


def _resolve_contact_id(name: str | None, explicit_id: str | None = None) -> str | None:
    if explicit_id:
        return explicit_id
    if not name:
        return None
    slug = _slug(name)
    baseline = core.load_baseline()
    for entry in baseline:
        if entry.get("id") == slug or (entry.get("name") or "").lower() == name.lower():
            return entry.get("id")
    return slug


def _baseline_has(contact_id: str | None) -> bool:
    if not contact_id:
        return False
    return any(e.get("id") == contact_id for e in core.load_baseline())


def _hits(text: str, terms: list[str]) -> list[str]:
    low = (text or "").lower()
    return [t for t in terms if t.lower() in low]


def _dedupe_tags(tags: list[str]) -> list[str]:
    """Case-insensitive tag dedupe while preserving the stronger display label."""
    preferred: dict[str, str] = {}
    for tag in tags:
        if not tag:
            continue
        key = tag.strip().lower()
        current = preferred.get(key)
        # Prefer the variant with intentional capitalization / acronym detail.
        if current is None or sum(ch.isupper() for ch in tag) > sum(ch.isupper() for ch in current):
            preferred[key] = tag.strip()
    return sorted(preferred.values(), key=lambda t: t.lower())


def _signal_pack(req: dict[str, Any], *, sarah_id: str | None, referrer_id: str | None) -> dict:
    text = req.get("text") or ""
    domain_terms = [
        "McDonald's", "food safety", "traceability", "enterprise farming",
        "U.S. expansion", "Bentonville", "Walmart", "Tyson Foods",
        "Commercial Director", "Commercial Director - North America",
        "North America", "Foods Connected", "Ireland-founded", "Ireland",
        "PAR SureCheck", "SureCheck", "food safety certification",
    ]
    signals = [
        {
            "type": "warm_referral",
            "confidence": 0.9 if referrer_id else 0.7,
            "evidence": req.get("referral_path") or "Jeff Staley -> Sarah McAngus -> Todd",
        },
        {
            "type": "active_opportunity",
            "confidence": 0.9,
            "evidence": req.get("opportunity_title") or "Commercial Director",
        },
    ]
    if req.get("resume_submitted") or "sent resume" in text.lower() or "resume submitted" in text.lower():
        signals.append({
            "type": "resume_submitted",
            "confidence": 0.94,
            "evidence": "Todd tailored resume and sent it back to Sarah",
        })
    else:
        signals.append({
            "type": "resume_requested",
            "confidence": 0.88,
            "evidence": "Todd expected to respond with resume",
        })
    if req.get("materials_received") or "sent jd" in text.lower() or "sent the jd" in text.lower():
        signals.append({
            "type": "materials_received",
            "confidence": 0.9,
            "evidence": "Sarah sent job description and company website",
        })
    else:
        signals.append({
            "type": "materials_pending",
            "confidence": 0.86,
            "evidence": "Sarah said she would send job description and company website",
        })
    if req.get("prior_food_safety_certification") or "food safety certification" in text.lower():
        signals.append({
            "type": "operator_credibility_signal",
            "confidence": 0.86,
            "evidence": "Todd has prior food safety certification",
        })
    if req.get("surecheck_story") or "surecheck" in text.lower():
        signals.append({
            "type": "mcdonalds_food_safety_story",
            "confidence": 0.88,
            "evidence": "Todd worked with McDonald's on PAR SureCheck food safety checklist system",
        })
    domain_hits = _dedupe_tags((req.get("domain_tags") or []) + _hits(text, domain_terms))
    if domain_hits:
        signals.append({
            "type": "strategic_domain_fit",
            "confidence": 0.92,
            "evidence": ", ".join(domain_hits),
        })
    return {
        "signals_detected": signals,
        "domain_tags": domain_hits,
        "momentum": req.get("momentum") or "hot",
        "strategic_fit": req.get("strategic_fit") or "high",
        "relationship_ids": {
            "primary": sarah_id,
            "referrer": referrer_id,
        },
    }


def build_plan(req: dict[str, Any]) -> dict:
    event_at = req.get("event_at") or _today_iso()
    captured_at = req.get("captured_at") or datetime.now(timezone.utc).isoformat(timespec="seconds")
    primary_name = req.get("contact_name") or "Sarah McAngus"
    primary_id = _resolve_contact_id(primary_name, req.get("contact_id"))
    referrer_name = req.get("referrer_name") or "Jeff Staley"
    referrer_id = _resolve_contact_id(referrer_name, req.get("referrer_id"))
    company = req.get("company_name") or "Foods Connected"
    company_id = req.get("company_id") or _slug(company)
    raw_text_lower = (req.get("text") or "").lower()
    opportunity_title = req.get("opportunity_title") or (
        "Commercial Director - North America"
        if "north america" in raw_text_lower else
        "Commercial Director"
    )
    stage = req.get("stage") or ("resume_submitted" if req.get("resume_submitted") else "active")
    opportunity_id = req.get("opportunity_id") or _slug(f"{company}-{opportunity_title}")
    thread_id = req.get("thread_id") or f"T-{event_at[:7]}-{opportunity_id}"[:90]

    signal_pack = _signal_pack(req, sarah_id=primary_id, referrer_id=referrer_id)
    role = req.get("contact_role") or "Recruiter / Commercial Director opportunity contact"
    company_context = req.get("company_context") or (
        "Founded in Ireland; global presence; smaller U.S. footprint with U.S. HQ in Bentonville, Arkansas; "
        "food safety and traceability company with known customers including McDonald's, Tyson Foods, and Walmart."
    )
    opportunity_context = req.get("opportunity_context") or (
        "Commercial Director opportunity: approximately 70% farming the McDonald's account and 30% hunting new business."
    )
    next_expected_action = req.get("next_expected_action") or (
        "Await Sarah response after resume submission."
        if stage == "resume_submitted" else
        "Await Sarah's job description and company website, then tailor and send resume."
    )
    notes = (
        f"[{event_at}] Opportunity RI: {primary_name} / {company} / {opportunity_title}. "
        f"Referral path: {referrer_name} -> {primary_name} -> Todd. {opportunity_context}"
    )

    operations: list[dict[str, Any]] = []
    if primary_id and not _baseline_has(primary_id):
        operations.append({
            "op": "createContact",
            "args": {
                "id": primary_id,
                "name": primary_name,
                "signal_class": "LKI",
                "company": company,
                "role": role,
                "last_touch": event_at,
                "source": "opportunity_intake",
                "notes": notes,
            },
            "artifact": "baseline_index.json",
            "rationale": "Sarah is the active opportunity contact and should be queryable as a relationship.",
        })
    elif primary_id:
        operations.append({
            "op": "touchContact",
            "args": {"id": primary_id, "date": event_at, "source": "opportunity_intake"},
            "artifact": "baseline_index.json/cards",
            "rationale": "Positive call with Sarah is a direct relationship interaction.",
        })

    if referrer_id:
        operations.append({
            "op": "touchContact",
            "args": {"id": referrer_id, "date": event_at, "source": "opportunity_intake_referral"},
            "artifact": "baseline_index.json/cards",
            "rationale": "Jeff created trust transfer by recommending Todd after his own interview.",
        })

    operations.extend([
        {
            "op": "createCompany",
            "args": {
                "id": company_id,
                "name": company,
                "summary": company_context,
                "domain_tags": signal_pack["domain_tags"],
                "source": "opportunity_intake",
            },
            "artifact": f"system/companies/{company_id}.md",
            "rationale": "Company context should be durable beyond the conversation.",
        },
        {
            "op": "createOpportunity",
            "args": {
                "id": opportunity_id,
                "company_id": company_id,
                "company_name": company,
                "title": opportunity_title,
                "status": stage,
                "momentum": signal_pack["momentum"],
                "strategic_fit": signal_pack["strategic_fit"],
                "summary": opportunity_context,
                "next_expected_action": next_expected_action,
                "domain_tags": signal_pack["domain_tags"],
                "referral_source": referrer_id or referrer_name,
                "recruiter_contact": primary_id or primary_name,
            },
            "artifact": f"system/opportunities/{opportunity_id}.md",
            "rationale": "The Commercial Director role is a high-priority opportunity artifact.",
        },
        {
            "op": "createRelationshipEdge",
            "args": {
                "from": referrer_id or referrer_name,
                "to": primary_id or primary_name,
                "via_company": company,
                "type": "warm_referral_trust_transfer",
                "evidence": req.get("referral_path") or "Jeff interviewed with Sarah and recommended Todd as a stronger fit.",
                "event_at": event_at,
            },
            "artifact": "system/relationship_edges.json",
            "rationale": "Referral path and trust transfer must be graph-visible.",
        },
        {
            "op": "openThread",
            "args": {
                "id": thread_id,
                "title": f"{company} / {opportunity_title}",
                "type": "job_opportunity",
                "people": [p for p in [primary_id, referrer_id] if p],
                "companies": [company],
                "context": notes,
                "current_state": (
                    f"Stage {stage}; momentum hot. {opportunity_context} Next action: {next_expected_action}"
                ),
                "target_close": (date.fromisoformat(event_at[:10]) + timedelta(days=45)).isoformat(),
                "boost_for_brief": "high",
                "boost_score": 1.45,
            },
            "artifact": "active_threads.yaml",
            "rationale": "Foods Connected should appear in Who Matters Now via active-thread boost.",
        },
        {
            "op": "writeInteractionBrief",
            "args": {
                "path": f"system/briefs/{event_at[:10]}-{primary_id or 'sarah-mcangus'}-{opportunity_id}.md",
                "person": primary_id,
                "company": company,
                "summary": notes,
                "signals": signal_pack["signals_detected"],
            },
            "artifact": "system/briefs",
            "rationale": "Append-only evidence makes the opportunity durable and auditable.",
        },
        {
            "op": "updateWhoMattersNow",
            "args": {
                "mechanism": "active_thread_boost",
                "thread_id": thread_id,
                "boost_for_brief": "high",
                "boost_score": 1.45,
            },
            "artifact": "today.md/daily_brief projection",
            "rationale": "Who Matters Now is updated through high-boost active thread projection.",
        },
    ])

    loop_specs = _loop_specs_for_stage(
        stage=stage,
        event_at=event_at[:10],
        primary_name=primary_name,
        company=company,
        opportunity_title=opportunity_title,
    )
    insert_at = next(
        (i for i, op in enumerate(operations) if op["op"] == "writeInteractionBrief"),
        len(operations),
    )
    for spec in reversed(loop_specs):
        operations.insert(insert_at, spec)

    return {
        "ri_scan": "found",
        "event_at": event_at,
        "captured_at": captured_at,
        "canonical_status": "pending_confirmation",
        "signals": signal_pack["signals_detected"],
        "strategic_assessment": {
            "opportunity_type": opportunity_title,
            "stage": stage,
            "strategic_fit": signal_pack["strategic_fit"],
            "momentum": signal_pack["momentum"],
            "next_expected_action": next_expected_action,
            "domain_tags": signal_pack["domain_tags"],
        },
        "matched_entities": {
            "contact": {"id": primary_id, "name": primary_name, "exists": _baseline_has(primary_id)},
            "referrer": {"id": referrer_id, "name": referrer_name, "exists": _baseline_has(referrer_id)},
            "company": {"id": company_id, "name": company},
            "opportunity": {"id": opportunity_id, "title": opportunity_title},
            "active_thread": {"id": thread_id},
        },
        "artifact_mutations": operations,
        "mutation_summary": [
            f"{op['op']}: {op['artifact']}" for op in operations
        ],
    }


def _loop_specs_for_stage(*, stage: str, event_at: str, primary_name: str,
                          company: str, opportunity_title: str) -> list[dict[str, Any]]:
    base = date.fromisoformat(event_at)

    def loop(party: str, description: str, days: int, rationale: str) -> dict[str, Any]:
        return {
            "op": "createLoop",
            "args": {
                "party": party,
                "description": description,
                "target": (base + timedelta(days=days)).isoformat(),
                "opened": event_at,
            },
            "artifact": "loop_ledger.md",
            "rationale": rationale,
        }

    if stage == "resume_submitted":
        return [
            loop(
                primary_name,
                f"Await Sarah response after resume submission for {company} {opportunity_title}.",
                5,
                "Resume has been submitted; next external signal is Sarah's response.",
            ),
            loop(
                "Todd",
                f"Prepare for next interview step for {company} {opportunity_title}.",
                3,
                "Hot opportunity should be interview-ready before Sarah replies.",
            ),
            loop(
                "Todd",
                f"Research {company} leadership and U.S. expansion strategy.",
                4,
                "Leadership/U.S. expansion context will improve next-step conversation quality.",
            ),
            loop(
                "Todd",
                "Prepare McDonald's food safety / PAR SureCheck story for Foods Connected interview path.",
                3,
                "Todd has unusually relevant food-safety and McDonald's-adjacent proof that should be ready.",
            ),
            loop(
                "Todd",
                "Prepare enterprise farming and customer-expansion examples for Foods Connected.",
                3,
                "Role mix is heavy McDonald's account farming plus new-business expansion.",
            ),
        ]

    return [
        loop(
            primary_name,
            f"Await Sarah's job description and company website for {company} {opportunity_title}.",
            2,
            "JD and website are pending external materials.",
        ),
        loop(
            "Todd",
            f"Tailor and send resume for {company} {opportunity_title} after Sarah sends JD/company website.",
            3,
            "Resume response is Todd's next expected action.",
        ),
    ]


def _sponsor_mapping_section(company_name: str | None) -> str:
    """RB-9.66-C: at opportunity-intake time, surface RB's existing footprint
    at the target company — confirmed inner-tier RC anchors (sponsors) and
    LKI-tier candidates (warm-path / sponsor-promotion candidates). Written
    into the opportunity artifact so sponsor mapping is captured as part of
    intake, not discovered later."""
    if not company_name:
        return "_No company name provided — sponsor mapping skipped._"
    try:
        rows = core.cluster_inner_anchor_score(core.load_baseline(), min_cluster=1)
    except Exception:  # noqa: BLE001
        return "_Sponsor mapping unavailable (baseline load failed)._"
    row = next((r for r in rows if r["company"].lower() == company_name.lower()), None)
    if not row:
        return f"_No baseline contacts found at {company_name}. No sponsor candidates identified._"
    lines = [f"RB network footprint at **{company_name}**: {row['total']} contact(s)."]
    if row["inner_rcs"]:
        lines.append(f"- Confirmed sponsor(s) (inner-tier RC): {', '.join(row['inner_rcs'])}")
    else:
        lines.append("- No inner-tier RC anchor at this company — sponsor gap.")
    if row["lki_names"]:
        lines.append(
            f"- Warm-path / sponsor-promotion candidates (LKI): {', '.join(row['lki_names'])}. "
            f"Consider re-engaging one of these contacts to anchor this opportunity."
        )
    return "\n".join(lines)


def _write_markdown(path: Path, title: str, sections: list[tuple[str, str]]) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        mutations.snapshot(path, f"pre-opportunity-intake-{datetime.now().strftime('%Y%m%d-%H%M%S')}")
    body = [f"# {title}", ""]
    for heading, content in sections:
        body.extend([f"## {heading}", content.strip(), ""])
    path.write_text("\n".join(body).rstrip() + "\n")
    return {"ok": True, "path": str(path.relative_to(core.PROJECT_DIR))}


def _append_relationship_edge(edge: dict) -> dict:
    if RELATIONSHIP_EDGES_PATH.exists():
        try:
            data = json.loads(RELATIONSHIP_EDGES_PATH.read_text())
        except json.JSONDecodeError:
            data = {"version": 1, "edges": []}
    else:
        data = {"version": 1, "edges": []}
    data.setdefault("edges", [])
    edge_id = _slug(f"{edge.get('from')}-{edge.get('to')}-{edge.get('type')}-{edge.get('event_at')}")
    edge = {"id": edge_id, **edge}
    if any(e.get("id") == edge_id for e in data["edges"]):
        return {"ok": True, "id": edge_id, "status": "already_exists"}
    if RELATIONSHIP_EDGES_PATH.exists():
        mutations.snapshot(RELATIONSHIP_EDGES_PATH, f"pre-edge-add-{edge_id}-{datetime.now().strftime('%Y%m%d-%H%M%S')}")
    data["edges"].append(edge)
    RELATIONSHIP_EDGES_PATH.write_text(json.dumps(data, indent=2) + "\n")
    return {"ok": True, "id": edge_id, "status": "written"}


def apply_plan(plan: dict) -> dict:
    applied: list[dict] = []
    skipped: list[dict] = []
    for op in plan.get("artifact_mutations") or []:
        name = op.get("op")
        args = op.get("args") or {}
        try:
            if name == "createContact":
                if _baseline_has(args["id"]):
                    applied.append({"op": name, "status": "already_exists", "id": args["id"]})
                else:
                    rc = mutations.cmd_contact_add(type("Ns", (), {
                        **args,
                        "linkedin": args.get("linkedin"),
                        "email": args.get("email"),
                        "phone": args.get("phone"),
                        "rc_tier": args.get("rc_tier"),
                        "dry_run": False,
                    })())
                    if rc != 0:
                        raise RuntimeError("contact-add failed")
                    applied.append({"op": name, "status": "written", "id": args["id"]})
            elif name == "touchContact":
                try:
                    result = mutations.touch_contact(args["id"], args.get("date"), args.get("source"))
                    applied.append({"op": name, "status": "written", "result": result})
                except ValueError:
                    skipped.append({"op": name, "status": "skipped_missing_contact", "id": args.get("id")})
            elif name == "openThread":
                data, _ = mutations._read_threads_file()
                if any(t.get("id") == args["id"] for t in data.get("threads") or []):
                    applied.append({"op": name, "status": "already_exists", "id": args["id"]})
                else:
                    rc = mutations.cmd_thread_open(type("Ns", (), {
                        "id": args["id"],
                        "title": args["title"],
                        "type": args["type"],
                        "people": args.get("people") or [],
                        "companies": args.get("companies") or [],
                        "context": args.get("context") or "",
                        "state": args.get("current_state") or "",
                        "target_close": args.get("target_close"),
                        "boost_for_brief": args.get("boost_for_brief") or "medium",
                        "boost_score": args.get("boost_score") or 1.2,
                        "dry_run": False,
                    })())
                    if rc != 0:
                        raise RuntimeError("thread-open failed")
                    applied.append({"op": name, "status": "written", "id": args["id"]})
            elif name == "createLoop":
                existing = [
                    L for L in core.parse_loop_ledger()
                    if not L.closed and L.party == args["party"] and L.description == args["description"]
                ]
                if existing:
                    applied.append({"op": name, "status": "already_exists", "id": existing[0].id})
                else:
                    rc = mutations.cmd_loop_add(type("Ns", (), {
                        "party": args["party"],
                        "description": args["description"],
                        "target": args["target"],
                        "opened": args.get("opened"),
                        "id": args.get("id"),
                        "dry_run": False,
                    })())
                    if rc != 0:
                        raise RuntimeError("loop-add failed")
                    applied.append({"op": name, "status": "written", "party": args["party"]})
            elif name == "createCompany":
                path = COMPANIES_DIR / f"{args['id']}.md"
                result = _write_markdown(path, args["name"], [
                    ("Summary", args.get("summary") or ""),
                    ("Domain Tags", "\n".join(f"- {t}" for t in args.get("domain_tags") or [])),
                    ("Source", args.get("source") or "opportunity_intake"),
                ])
                applied.append({"op": name, "status": "written", **result})
            elif name == "createOpportunity":
                path = OPPORTUNITIES_DIR / f"{args['id']}.md"
                # RB-DEFECT-032 — `thesis` is the working assumption an
                # opportunity rests on (e.g. "Foods Connected has an
                # established McDonald's relationship"). `contradictory_signals`
                # is the structured home for evidence that challenges it —
                # previously there was nowhere for a signal like Jeff
                # Coffland's SMS to land even if it had been extracted.
                contradictions = args.get("contradictory_signals") or []
                contradiction_lines = []
                for sig in contradictions:
                    if not isinstance(sig, dict):
                        continue
                    contradiction_lines.append(
                        f"- **{sig.get('source', 'unknown source')}** "
                        f"(trust: {sig.get('source_trust', 'unknown')}, "
                        f"confidence: {sig.get('confidence', 'unknown')}, "
                        f"detected: {sig.get('detected_at', 'unknown')}): "
                        f"{sig.get('signal', '')}\n"
                        f"  - Interpretation: {sig.get('interpretation', '')}\n"
                        f"  - Alternative explanations: "
                        f"{'; '.join(sig.get('alternative_explanations') or [])}\n"
                        f"  - Recommended validation: "
                        f"{'; '.join(sig.get('recommended_validation') or [])}"
                    )
                result = _write_markdown(path, f"{args['company_name']} / {args['title']}", [
                    ("Status", args.get("status") or "active"),
                    ("Summary", args.get("summary") or ""),
                    ("Thesis", args.get("thesis") or ""),
                    ("Strategic Fit", args.get("strategic_fit") or ""),
                    ("Momentum", args.get("momentum") or ""),
                    ("Referral Source", args.get("referral_source") or ""),
                    ("Recruiter / Contact", args.get("recruiter_contact") or ""),
                    ("Next Expected Action", args.get("next_expected_action") or ""),
                    ("Domain Tags", "\n".join(f"- {t}" for t in args.get("domain_tags") or [])),
                    ("Sponsor Mapping", _sponsor_mapping_section(args.get("company_name"))),
                    ("Contradictory Signals", "\n".join(contradiction_lines)),
                ])
                applied.append({"op": name, "status": "written", **result})
            elif name == "createRelationshipEdge":
                result = _append_relationship_edge(args)
                applied.append({"op": name, **result})
            elif name == "writeInteractionBrief":
                path = core.PROJECT_DIR / args["path"]
                result = _write_markdown(path, Path(args["path"]).stem, [
                    ("Summary", args.get("summary") or ""),
                    ("Signals", json.dumps(args.get("signals") or [], indent=2)),
                ])
                applied.append({"op": name, "status": "written", **result})
            elif name == "updateWhoMattersNow":
                applied.append({
                    "op": name,
                    "status": "projected_via_active_thread",
                    "thread_id": args.get("thread_id"),
                    "mechanism": args.get("mechanism"),
                })
            else:
                skipped.append({"op": name, "status": "unknown_operation"})
        except Exception as exc:  # noqa: BLE001
            skipped.append({"op": name, "status": f"failed:{exc}"})

    status = "persisted" if applied and not any(str(s.get("status", "")).startswith("failed") for s in skipped) else "partial_or_failed"
    return {
        "canonical_status": status,
        "applied": applied,
        "skipped": skipped,
        "validated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def process(req: dict[str, Any], *, confirm: bool = False) -> dict:
    plan = build_plan(req)
    if not confirm:
        return {
            **plan,
            "confirmed": False,
            "persistence_status": "preview_only_no_writes",
            "canonical_output": _canonical_output(plan, None),
        }
    persistence = apply_plan(plan)
    return {
        **plan,
        "confirmed": True,
        "persistence_status": persistence["canonical_status"],
        "persistence": persistence,
        "canonical_output": _canonical_output(plan, persistence),
    }


def _canonical_output(plan: dict, persistence: dict | None) -> str:
    lines = ["RELATIONSHIP INTELLIGENCE PROCESSED", "", "Signals detected:"]
    for s in plan.get("signals") or []:
        lines.append(f"- {s['type']}: {s['evidence']}")
    lines.extend(["", "Artifact mutations:"])
    for m in plan.get("artifact_mutations") or []:
        lines.append(f"- {m['op']}: {m['artifact']}")
    status = (persistence or {}).get("canonical_status") or "pending confirmation"
    lines.extend(["", f"Status: {status}."])
    return "\n".join(lines)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--json-file")
    p.add_argument("--confirm", action="store_true")
    args = p.parse_args()
    if args.json_file:
        req = json.loads(Path(args.json_file).read_text())
    else:
        req = json.loads(sys.stdin.read())
    print(json.dumps(process(req, confirm=args.confirm), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
