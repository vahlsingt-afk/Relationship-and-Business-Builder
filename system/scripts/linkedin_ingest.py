#!/usr/bin/env python3
"""
linkedin_ingest.py - canonical LinkedIn export ZIP ingestion.

This is the deterministic wrapper for P-002. It recognizes LinkedIn archive
structure, parses Connections.csv plus companion activity files when present,
enhances baseline_index.json without replacing operator-owned fields, computes
trust-building deltas, writes a CoS-level delta report, and returns the same
summary shape to API callers.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import shutil
import sys
import zipfile
from collections import Counter
from copy import deepcopy
from datetime import date
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rb_core as core  # noqa: E402
import identity_matcher as im  # noqa: E402
import mutation_report as mr  # noqa: E402
import post_ingest_intelligence as pii  # noqa: E402
import audit_log  # noqa: E402
from employment_state import is_employment_state_protected  # noqa: E402


DELTAS_DIR = core.SYSTEM_DIR / "deltas"
LINKEDIN_CACHE_PATH = core.SYSTEM_DIR / ".cache" / "linkedin_ingest_latest.json"
KNOWN_LINKEDIN_FILES = {
    "connections.csv",
    "messages.csv",
    "invitations.csv",
    "comments.csv",
    "shares.csv",
    "reactions.csv",
}
TITLE_PROMOTION_RX = re.compile(
    r"\b(chief|ceo|cfo|coo|cro|cto|cio|cmo|president|vp|vice president|"
    r"svp|evp|head of|director|general manager|managing director|partner)\b",
    re.I,
)
TARGET_DOMAIN_RX = re.compile(
    r"\b(restaurant|hospitality|qsr|foodservice|food service|franchise|"
    r"pos|point of sale|payments?|ai|automation|kitchen|drive[- ]?thru)\b",
    re.I,
)
RECRUITER_RX = re.compile(
    r"\b(recruit|recruiter|talent acquisition|talent partner|headhunter|"
    r"executive search|sourcer|people partner|hr business partner)\b",
    re.I,
)
ENTERPRISE_BUYER_RX = re.compile(
    r"\b(procurement|sourcing|purchasing|operations|operator|franchise|"
    r"technology|digital|transformation|innovation|it|information technology|"
    r"strategy|growth|revenue|commercial|sales|customer success|partnerships)\b",
    re.I,
)
SENIORITY_PATTERNS = [
    (100, re.compile(r"\b(chief|ceo|cfo|coo|cro|cto|cio|cmo|president|founder|owner|partner)\b", re.I)),
    (85, re.compile(r"\b(evp|svp|executive vice president|senior vice president)\b", re.I)),
    (75, re.compile(r"\b(vp|vice president|head of|general manager|managing director)\b", re.I)),
    (60, re.compile(r"\b(director|principal|lead)\b", re.I)),
    (45, re.compile(r"\b(manager|senior)\b", re.I)),
]
MCDONALDS_RX = re.compile(
    r"\b(mcdonald|mcd|mcds|mcdonald\'s|golden arches|arcos dorados|"
    r"mcd operator|mcd franchisee|mcdonalds)\b",
    re.I,
)
SEGMENTS = {
    "restaurant_technology": TARGET_DOMAIN_RX,
    "recruiters": RECRUITER_RX,
}
# RB 9.86 — RB-DEFECT-041 Enhancement #3 "Strategic Account Mapping": when a
# LinkedIn connection's company matches one of these accounts, the move gets
# elevated handling (priority bump + account-map note) in _recommend_actions
# and is collected into delta_intelligence.strategic_relationship_changes
# .account_map_signals.
STRATEGIC_ACCOUNTS: dict[str, re.Pattern[str]] = {
    "Global Payments / Worldpay": re.compile(r"\b(global payments|worldpay)\b", re.I),
    "Foods Connected": re.compile(r"\bfoods connected\b", re.I),
    "PAR Technology": re.compile(r"\bpar technology\b", re.I),
    "Toast": re.compile(r"\btoast\b", re.I),
    "Olo": re.compile(r"\bolo\b", re.I),
    "NCR Voyix": re.compile(r"\bncr voyix\b", re.I),
    "Oracle Hospitality": re.compile(r"\boracle hospitality\b", re.I),
    "Agilysys": re.compile(r"\bagilysys\b", re.I),
    "Restaurant365": re.compile(r"\brestaurant\s*365\b", re.I),
    "Crunchtime": re.compile(r"\bcrunchtime\b", re.I),
    "Paytronix": re.compile(r"\bpaytronix\b", re.I),
    "DoorDash": re.compile(r"\bdoordash\b", re.I),
    "Uber Eats": re.compile(r"\buber eats\b", re.I),
    "Shift4": re.compile(r"\bshift4\b", re.I),
}


def _strategic_account_match(text: str | None) -> str | None:
    """Return the strategic-account name `text` (e.g. a company field)
    matches, or None. Used to elevate LinkedIn moves at accounts RB tracks
    for account mapping (Global Payments/Worldpay, Foods Connected, the
    restaurant-tech ecosystem)."""
    if not text:
        return None
    for account, pattern in STRATEGIC_ACCOUNTS.items():
        if pattern.search(text):
            return account
    return None
# Segment checkers that need a function rather than a single regex
def _in_mcdonalds_ecosystem(entry: dict) -> bool:
    text = " ".join(str(entry.get(k) or "") for k in ("current_company", "current_role", "notes", "tags"))
    return bool(MCDONALDS_RX.search(text))


def _relationship_strength_label(entry: dict) -> str:
    sc = entry.get("signal_class") or "VC"
    tier = entry.get("rc_tier") or ""
    if sc == "RC" and tier in ("1", "2", 1, 2):
        return "Inner Circle"
    if sc == "RC":
        return "RC Tier"
    if sc == "LKI":
        return "Known Influence"
    return "Visible Contact"


def _change_significance_label(move: dict) -> str:
    if _move_is_promotion(move):
        return "High"
    if move.get("company_changed") and move.get("role_changed"):
        return "High"
    if move.get("company_changed"):
        return "Medium"
    return "Low"


def _career_activation_priority(move: dict) -> float:
    """0–10 priority score for career-change outreach activation."""
    score = 0.0
    sc = move.get("signal_class") or "VC"
    if sc == "RC":
        score += 5.0
    elif sc == "LKI":
        score += 3.5
    else:
        score += 1.0
    if _move_is_promotion(move):
        score += 3.0
    elif move.get("company_changed") and move.get("role_changed"):
        score += 2.0
    elif move.get("company_changed"):
        score += 1.5
    else:
        score += 0.5
    if TARGET_DOMAIN_RX.search(" ".join(str(move.get(k) or "") for k in ("new_company", "new_role"))):
        score += 1.5
    return min(round(score, 1), 10.0)


def _compute_segment_deltas(baseline_before: list[dict], merged: list[dict]) -> list[dict]:
    """Return Previous / Current / Delta rows for strategic contact segments."""
    checks = [
        ("Total Connections", lambda e: True),
        ("Restaurant Technology", _is_target_adjacent),
        ("Recruiters", _is_recruiter),
        ("Executives (VP+)", _is_executive),
        ("Enterprise Buyers", _is_enterprise_buyer),
        ("McDonald's Ecosystem", _in_mcdonalds_ecosystem),
    ]
    rows = []
    for label, fn in checks:
        prev = sum(1 for e in baseline_before if fn(e))
        curr = sum(1 for e in merged if fn(e))
        rows.append({"segment": label, "previous": prev, "current": curr, "delta": curr - prev})
    return rows


def _build_trust_stats(
    activity_counts: dict[str, int],
    rows: list[dict],
    merged: list[dict],
) -> dict[str, Any]:
    """Compute confidence, source coverage, and evidence counts for this ingest."""
    sources_present = ["Connections.csv"]
    for key in ("messages", "invitations", "comments", "shares", "reactions"):
        if activity_counts.get(key, 0) > 0:
            sources_present.append(f"{key}.csv")
    source_coverage = round(len(sources_present) / len(KNOWN_LINKEDIN_FILES) * 100)
    connection_coverage = (
        round(len(rows) / len(merged) * 100) if merged else 0
    )
    # Higher confidence when more sources present and coverage is high
    base = 70
    base += min(20, (len(sources_present) - 1) * 5)
    base += min(10, connection_coverage // 10)
    return {
        "confidence_score": min(base, 98),
        "sources_present": sources_present,
        "source_coverage_pct": source_coverage,
        "connections_analyzed": len(rows),
        "baseline_record_count": len(merged),
        "connection_coverage_pct": connection_coverage,
        "notes": "Confidence reflects source breadth and connection coverage relative to baseline.",
    }


def _build_career_activation_list(moves: list[dict], new_highlights: list[dict]) -> list[dict]:
    """Produce a priority-scored career-change activation list."""
    results: list[dict] = []
    for move in moves:
        priority = _career_activation_priority(move)
        sig = _change_significance_label(move)
        strength = _relationship_strength_label(move.get("entry_snapshot") or {})
        if _move_is_promotion(move):
            action = "Send a congratulations note and use it to reopen the relationship."
        elif move.get("company_changed"):
            action = "Reconnect — new employer is a natural pretext for a light-touch check-in."
        else:
            action = "Note title change; confirm accuracy and update relationship context."
        results.append({
            "person": move["name"],
            "relationship_strength": strength,
            "change_type": _movement_type(move),
            "change_significance": sig,
            "old_company": move.get("old_company"),
            "new_company": move.get("new_company"),
            "old_role": move.get("old_role"),
            "new_role": move.get("new_role"),
            "strategic_domain": bool(
                TARGET_DOMAIN_RX.search(" ".join(str(move.get(k) or "") for k in ("new_company", "new_role")))
            ),
            "recommended_action": action,
            "priority_score": priority,
        })
    for item in new_highlights:
        results.append({
            "person": item["name"],
            "relationship_strength": "New Connection",
            "change_type": "new_connection",
            "change_significance": "Medium",
            "old_company": None,
            "new_company": item.get("company"),
            "old_role": None,
            "new_role": item.get("role"),
            "strategic_domain": bool(
                TARGET_DOMAIN_RX.search(f"{item.get('company') or ''} {item.get('role') or ''}")
            ),
            "recommended_action": "Review as a warm-entry candidate before the connection goes cold.",
            "priority_score": 4.0,
        })
    results.sort(key=lambda x: x["priority_score"], reverse=True)
    return results


def _slug(s: str, *, max_len: int = 80) -> str:
    out = re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")
    return (out[:max_len].strip("-") or "unknown")


def _linkedin_slug(url: str | None) -> str | None:
    return im.linkedin_slug(url)


def _norm(s: str | None) -> str:
    return im.norm_name(s)


def _display_name(row: dict[str, str]) -> str:
    first = (row.get("First Name") or "").strip()
    last = (row.get("Last Name") or "").strip()
    full = " ".join(x for x in (first, last) if x).strip()
    if full:
        return full
    url_slug = _linkedin_slug(row.get("URL"))
    return url_slug.replace("-", " ").title() if url_slug else ""


def _count_new_companies(baseline: list[dict], new_entries: list[dict]) -> int:
    """Distinct companies among new connections not already present anywhere
    in baseline — same "Companies Added" semantics as hubspot_ingest.py."""
    existing = {_norm(e.get("current_company")) for e in baseline if e.get("current_company")}
    seen = {_norm(e.get("current_company")) for e in new_entries if e.get("current_company")}
    return len(seen - existing)


def _safe_count_csv(zf: zipfile.ZipFile, name: str) -> int:
    try:
        raw = zf.read(name).decode("utf-8-sig", errors="replace")
    except Exception:
        return 0
    lines = [ln for ln in raw.splitlines() if ln.strip()]
    if not lines:
        return 0
    header_idx = _find_header_index(lines)
    return max(0, len(lines) - header_idx - 1)


def _find_header_index(lines: list[str]) -> int:
    for i, line in enumerate(lines):
        lower = line.lower()
        if "first name" in lower and "last name" in lower and "url" in lower:
            return i
    return 0


def _read_csv_from_zip(zf: zipfile.ZipFile, member: str) -> list[dict[str, str]]:
    raw = zf.read(member).decode("utf-8-sig", errors="replace")
    lines = raw.splitlines()
    header_idx = _find_header_index(lines)
    reader = csv.DictReader(io.StringIO("\n".join(lines[header_idx:])))
    return [{k: (v or "").strip() for k, v in row.items()} for row in reader]


def classify_archive(path: str | Path) -> dict[str, Any]:
    """Return a lightweight artifact classification for upload routing."""
    p = Path(path)
    if not p.exists():
        return {"artifact_type": "missing", "confidence": 0.0, "reason": "path_not_found"}
    if p.suffix.lower() != ".zip":
        return {"artifact_type": "unknown", "confidence": 0.0, "reason": "not_zip"}
    try:
        with zipfile.ZipFile(p) as zf:
            names = zf.namelist()
    except zipfile.BadZipFile:
        return {"artifact_type": "unknown", "confidence": 0.0, "reason": "bad_zip"}

    lower = {Path(n).name.lower() for n in names if not n.endswith("/")}
    hits = sorted(lower & KNOWN_LINKEDIN_FILES)
    has_connections = "connections.csv" in lower
    confidence = 0.95 if has_connections else (0.55 if len(hits) >= 2 else 0.0)
    return {
        "artifact_type": "linkedin_export_zip" if confidence >= 0.55 else "unknown",
        "confidence": confidence,
        "reason": "linkedin_export_structure" if confidence >= 0.55 else "no_known_structure",
        "matched_files": hits,
        "has_connections_csv": has_connections,
        "file_count": len(lower),
    }


def _build_matchers(baseline: list[dict]) -> dict[str, dict]:
    return {
        "by_url": im.build_linkedin_url_index(baseline),
        "by_email": im.build_email_index(baseline),
        "by_name": im.build_name_index(baseline),
    }


def _match_row(row: dict[str, str], matchers: dict[str, dict]) -> dict | None:
    url_slug = _linkedin_slug(row.get("URL"))
    if url_slug and url_slug in matchers["by_url"]:
        return matchers["by_url"][url_slug]

    match, _ambiguous = im.match_unique_name(_display_name(row), matchers["by_name"])
    if match is not None:
        return match

    email = (row.get("Email Address") or "").strip().lower()
    if email and email in matchers["by_email"]:
        return matchers["by_email"][email]
    return None


def _unique_id(base: str, existing: set[str]) -> str:
    candidate = _slug(base)
    if candidate == "unknown":
        candidate = "linkedin-contact"
    if candidate not in existing:
        existing.add(candidate)
        return candidate
    i = 2
    while f"{candidate}-{i}" in existing:
        i += 1
    out = f"{candidate}-{i}"
    existing.add(out)
    return out


def _append_note(entry: dict, text: str) -> None:
    notes = (entry.get("notes") or "").strip()
    entry["notes"] = (notes + "\n" + text).strip() if notes else text


def _add_tag(entry: dict, tag: str) -> None:
    tags = entry.setdefault("tags", [])
    if tag and tag not in tags:
        tags.append(tag)


def _is_operator_confirmed(entry: dict) -> bool:
    notes = (entry.get("notes") or "").lower()
    return "confirmed by todd" in notes or "confirmed by operator" in notes or "conflict resolved" in notes


def _same_title(a: str | None, b: str | None, company: str | None = None) -> bool:
    aa = _norm(a)
    bb = _norm(b)
    if aa == bb:
        return True
    company_norm = _norm(company)
    if company_norm:
        for marker in (f" at {company_norm}", f" @ {company_norm}"):
            aa = aa.replace(marker, "")
            bb = bb.replace(marker, "")
    return aa == bb


def _activity_counts(zf: zipfile.ZipFile) -> dict[str, int]:
    lower_to_name = {Path(n).name.lower(): n for n in zf.namelist() if not n.endswith("/")}
    counts: dict[str, int] = {}
    for key in ("messages.csv", "invitations.csv", "comments.csv", "shares.csv", "reactions.csv"):
        counts[key.replace(".csv", "")] = _safe_count_csv(zf, lower_to_name[key]) if key in lower_to_name else 0
    return counts


def _top_counts(rows: list[dict], field: str, limit: int = 10) -> list[dict]:
    c = Counter((r.get(field) or "").strip() or "(blank)" for r in rows)
    return [{"value": k, "count": v} for k, v in c.most_common(limit)]


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(core.PROJECT_DIR))
    except ValueError:
        return str(path)


def _seniority_score(title: str | None) -> int:
    text = title or ""
    for score, rx in SENIORITY_PATTERNS:
        if rx.search(text):
            return score
    return 25 if text else 0


def _is_recruiter(entry: dict) -> bool:
    return bool(RECRUITER_RX.search(" ".join(str(entry.get(k) or "") for k in ("current_role", "current_company", "notes"))))


def _is_executive(entry: dict) -> bool:
    return _seniority_score(entry.get("current_role")) >= 75


def _is_enterprise_buyer(entry: dict) -> bool:
    text = " ".join(str(entry.get(k) or "") for k in ("current_role", "current_company"))
    return bool(ENTERPRISE_BUYER_RX.search(text)) and _seniority_score(entry.get("current_role")) >= 45


def _is_target_adjacent(entry: dict) -> bool:
    return bool(TARGET_DOMAIN_RX.search(" ".join(str(entry.get(k) or "") for k in ("current_role", "current_company"))))


def _is_dormant(entry: dict) -> bool:
    return not bool(entry.get("last_touch"))


def _strategic_value_score(entry: dict) -> int:
    score = 0
    signal_class = entry.get("signal_class")
    if signal_class == "RC":
        score += 80
    elif signal_class == "LKI":
        score += 55
    elif signal_class == "LMI":
        score += 35
    else:
        score += 15
    if _is_executive(entry):
        score += 20
    if _is_target_adjacent(entry):
        score += 20
    if _is_recruiter(entry):
        score += 15
    if _is_enterprise_buyer(entry):
        score += 15
    return min(score, 100)


def _move_is_promotion(move: dict) -> bool:
    old_score = _seniority_score(move.get("old_role"))
    new_score = _seniority_score(move.get("new_role"))
    if new_score >= 75 and new_score > old_score:
        return True
    old_norm = _norm(move.get("old_role"))
    new_norm = _norm(move.get("new_role"))
    return bool(new_norm and new_norm != old_norm and TITLE_PROMOTION_RX.search(new_norm) and not TITLE_PROMOTION_RX.search(old_norm))


def _movement_type(move: dict) -> str:
    if _move_is_promotion(move):
        return "promotion"
    if move.get("company_changed") and move.get("role_changed"):
        return "company_and_role_change"
    if move.get("company_changed"):
        return "company_change"
    if move.get("role_changed"):
        return "title_change"
    return "movement"


def _network_visualization_payload(baseline: list[dict], new_entries: list[dict]) -> dict[str, Any]:
    classes = Counter(e.get("signal_class") or "unknown" for e in baseline)
    seniority = Counter()
    industries = Counter()
    active = Counter()
    for e in baseline:
        title = e.get("current_role") or ""
        company = e.get("current_company") or ""
        if TITLE_PROMOTION_RX.search(title):
            seniority["leadership"] += 1
        elif re.search(r"\b(manager|lead|principal|senior)\b", title, re.I):
            seniority["manager_or_senior_ic"] += 1
        else:
            seniority["other_or_unknown"] += 1
        if TARGET_DOMAIN_RX.search(f"{title} {company}"):
            industries["restaurant_ai_hospitality_adjacent"] += 1
        else:
            industries["other"] += 1
        active["active_or_touched"] += 1 if e.get("last_touch") else 0
        active["dormant_or_no_touch"] += 0 if e.get("last_touch") else 1
    return {
        "network_segmentation": dict(classes),
        "title_seniority_distribution": dict(seniority),
        "industry_clustering": dict(industries),
        "active_vs_dormant": dict(active),
        "new_connection_role_distribution": _top_counts(new_entries, "current_role"),
        "new_connection_company_distribution": _top_counts(new_entries, "current_company"),
    }


def _recommend_actions(delta: dict) -> list[dict]:
    actions: list[dict] = []
    for move in delta["rc_moves"][:8]:
        action = "Confirm the LinkedIn-visible move and use it as a reconnect pretext."
        if move.get("role_changed") and TITLE_PROMOTION_RX.search(move.get("new_role") or ""):
            action = "Send a congratulations note; leadership movement is a timing signal."
        account = _strategic_account_match(move.get("new_company"))
        if account:
            action += f" Add to {account} account map."
        actions.append({
            "priority": "high",
            "person": move["name"],
            "action": action,
            "why": move["reason"],
        })
    for move in delta["lki_moves"][:8]:
        account = _strategic_account_match(move.get("new_company"))
        if account:
            actions.append({
                "priority": "high",
                "person": move["name"],
                "action": f"Add to {account} account map; light-touch reconnect on the new role.",
                "why": f"Move places this contact at {account}, a tracked strategic account.",
            })
        elif TARGET_DOMAIN_RX.search(" ".join(str(move.get(k) or "") for k in ("new_company", "new_role"))):
            actions.append({
                "priority": "medium",
                "person": move["name"],
                "action": "Light-touch reconnect; ask what changed and what they are building now.",
                "why": "Move intersects restaurant, hospitality, AI, payments, or operator terrain.",
            })
    for person in delta["new_highlights"][:6]:
        account = _strategic_account_match(person.get("company"))
        if account:
            actions.append({
                "priority": "high",
                "person": person["name"],
                "action": f"Add to {account} account map; review as a warm-entry candidate before the connection goes cold.",
                "why": f"New connection is at {account}, a tracked strategic account.",
            })
        else:
            actions.append({
                "priority": "medium",
                "person": person["name"],
                "action": "Review as a warm-entry candidate before the connection goes cold.",
                "why": person["reason"],
            })
    return actions[:15]


def _build_delta_intelligence(*, baseline_before: list[dict], merged: list[dict], delta: dict, rows: list[dict], activity_counts: dict[str, int], source_tag: str, ingest_date: str, dry_run: bool) -> dict[str, Any]:  # noqa: E501
    moves = delta["rc_moves"] + delta["lki_moves"]
    changed_entries = [m.get("entry_snapshot") for m in moves if m.get("entry_snapshot")]
    new_entries = delta["new"]
    disconnections = delta["disconnections"]
    promotions = [m for m in moves if _move_is_promotion(m)]
    restaurant_adjacent_new = [e for e in new_entries if _is_target_adjacent(e)]
    restaurant_adjacent_moves = [
        m for m in moves
        if TARGET_DOMAIN_RX.search(str(m.get("new_company") or "") + " " + str(m.get("new_role") or ""))
        and not TARGET_DOMAIN_RX.search(str(m.get("old_company") or "") + " " + str(m.get("old_role") or ""))
    ]
    recruiter_additions = [e for e in new_entries if _is_recruiter(e)]
    executive_additions = [e for e in new_entries if _is_executive(e)]
    enterprise_buyer_additions = [e for e in new_entries if _is_enterprise_buyer(e)]
    dormant_reactivation = [
        m for m in moves
        if m.get("entry_snapshot") and _is_dormant(m["entry_snapshot"])
    ]
    materially_increased = []
    for move in moves:
        score = 0
        if move.get("signal_class") == "RC":
            score += 50
        elif move.get("signal_class") == "LKI":
            score += 35
        if _move_is_promotion(move):
            score += 30
        if TARGET_DOMAIN_RX.search(" ".join(str(move.get(k) or "") for k in ("new_company", "new_role"))):
            score += 25
        if score >= 50:
            materially_increased.append({**move, "strategic_value_delta": score})
    for entry in new_entries:
        score = _strategic_value_score(entry)
        if score >= 55:
            materially_increased.append({
                "name": entry.get("name"),
                "id": entry.get("id"),
                "signal_class": entry.get("signal_class"),
                "new_company": entry.get("current_company"),
                "new_role": entry.get("current_role"),
                "movement_type": "new_connection",
                "strategic_value_delta": score,
                "reason": "New connection has leadership, recruiter, enterprise-buyer, or restaurant-tech adjacency.",
            })

    congrats = [
        {
            "person": m["name"],
            "action": "Send a concise congratulations note.",
            "why": f"LinkedIn export shows {m.get('old_role') or '-'} -> {m.get('new_role') or '-'}.",
        }
        for m in promotions[:12]
    ]
    reconnect = [
        {
            "person": m["name"],
            "action": "Reconnect using the visible move as the pretext.",
            "why": "Dormant or high-value relationship changed role/company.",
        }
        for m in dormant_reactivation[:12]
    ]
    recruiter = [
        {
            "person": e.get("name"),
            "action": "Review as recruiter or talent-market signal.",
            "why": f"{e.get('current_role') or '-'} at {e.get('current_company') or '-'}.",
        }
        for e in recruiter_additions[:12]
    ]
    warm_path = [
        {
            "person": (item.get("name") or item.get("person")),
            "action": "Review as a warm-path or market-adjacency candidate.",
            "why": item.get("reason") or "Restaurant/AI/hospitality adjacency increased.",
        }
        for item in materially_increased[:12]
    ]
    action_queue = congrats + reconnect + recruiter + warm_path

    relationship_strength_mutations = len(delta["reconnections"]) + len(delta["disconnections"])
    strategic_importance_mutations = len(materially_increased)
    opportunity_graph_mutations = len(action_queue)
    who_matters_now = materially_increased[:15]

    # RB 9.86 — RB-DEFECT-041 Enhancement #3: collect moves/new connections
    # landing at a tracked strategic account (Global Payments/Worldpay, Foods
    # Connected, restaurant-tech ecosystem) for elevated account-map handling.
    account_map_signals = []
    for move in moves:
        account = _strategic_account_match(move.get("new_company"))
        if account:
            account_map_signals.append({
                "person": move.get("name"),
                "account": account,
                "company": move.get("new_company"),
                "role": move.get("new_role"),
                "movement_type": _movement_type(move),
            })
    for entry in new_entries:
        account = _strategic_account_match(entry.get("current_company"))
        if account:
            account_map_signals.append({
                "person": entry.get("name"),
                "account": account,
                "company": entry.get("current_company"),
                "role": entry.get("current_role"),
                "movement_type": "new_connection",
            })

    segment_deltas = _compute_segment_deltas(baseline_before, merged)
    trust_stats = _build_trust_stats(activity_counts, rows, merged)
    career_activation = _build_career_activation_list(moves, delta["new_highlights"])

    return {
        "trust_statistics": trust_stats,
        "segment_deltas": segment_deltas,
        "career_activation": career_activation,
        "relationship_delta_metrics": {
            "prior_baseline_record_count": len(baseline_before),
            "current_baseline_record_count": len(merged),
            "connections_in_export": len(rows),
            "matched_existing": len(rows) - len(new_entries),
            "net_new_relationships": len(new_entries),
            "lost_relationships": len(disconnections),
            "reconnections": len(delta["reconnections"]),
            "dormant_relationships_in_changed_set": len(dormant_reactivation),
            "recruiter_additions": len(recruiter_additions),
            "executive_additions": len(executive_additions),
            "enterprise_buyer_additions": len(enterprise_buyer_additions),
            "restaurant_tech_adjacency_expansion": len(restaurant_adjacent_new) + len(restaurant_adjacent_moves),
            "strategic_industry_cluster_changes": len(materially_increased),
        },
        "professional_change_detection": {
            "title_changes": len(delta["role_changes"]),
            "company_changes": len(delta["company_changes"]),
            "promotions": len(promotions),
            "lateral_moves": len([m for m in moves if _movement_type(m) == "company_change"]),
            "industry_transitions": len(restaurant_adjacent_moves),
            "hiring_or_recruiting_indicators": len(recruiter_additions),
            "sample": [
                {
                    "person": m.get("name"),
                    "movement_type": _movement_type(m),
                    "old_company": m.get("old_company"),
                    "new_company": m.get("new_company"),
                    "old_role": m.get("old_role"),
                    "new_role": m.get("new_role"),
                    "priority": "high" if m in materially_increased or m.get("signal_class") == "RC" else "medium",
                }
                for m in moves[:20]
            ],
        },
        "strategic_relationship_changes": {
            "materially_increased_value_count": len(materially_increased),
            "who_matters_now": who_matters_now,
            "account_map_signals": account_map_signals[:20],
            "dormant_reactivation_candidates": dormant_reactivation[:12],
            "newly_relevant_contacts": [
                {
                    "name": e.get("name"),
                    "company": e.get("current_company"),
                    "role": e.get("current_role"),
                    "reason": "New relationship has recruiter, executive, enterprise-buyer, or restaurant-tech adjacency.",
                }
                for e in (executive_additions + recruiter_additions + enterprise_buyer_additions + restaurant_adjacent_new)[:20]
            ],
        },
        "opportunity_detection": {
            "congratulations_opportunities": congrats,
            "reconnect_opportunities": reconnect,
            "recruiter_engagement_opportunities": recruiter,
            "warm_path_candidates": warm_path,
            "suggested_outreach_queue": action_queue[:20],
        },
        "graph_mutations": {
            "persistent_graph_mutated": not dry_run,
            "baseline_entries_added": len(new_entries),
            "baseline_entries_updated": len(delta["company_changes"]) + len(delta["role_changes"]) + len(delta["reconnections"]) + len(disconnections),
            "relationship_strength_mutations": relationship_strength_mutations,
            "trust_score_mutations": 0,
            "strategic_importance_mutations": strategic_importance_mutations,
            "opportunity_graph_mutations": opportunity_graph_mutations,
            "who_matters_now_mutations": len(who_matters_now),
            "mutation_tag": f"linkedin_delta_{ingest_date}",
            "notes": [
                "Updated company/title/source/note/tag fields where LinkedIn created deterministic baseline deltas.",
                "Did not auto-promote relationship tier or trust score from LinkedIn edge alone.",
                "Generated Who Matters Now and outreach queues as review-first CoS mutations.",
            ],
        },
        "daily_brief_mutations": {
            "cache_path": _display_path(LINKEDIN_CACHE_PATH),
            "section": "LinkedIn Relationship Delta",
            "items_available_for_brief": len(who_matters_now) + len(action_queue),
            "top_lines": [
                f"{len(materially_increased)} strategic relationships materially increased in value.",
                f"{len(promotions)} promotion events detected.",
                f"{len(action_queue[:20])} suggested outreach queue items generated.",
            ],
        },
        "persistence_verification": {
            "status": "dry_run_not_persisted" if dry_run else "persisted",
            "source_tag": source_tag,
            "activity_counts": activity_counts,
            "post_write_validation": "pending" if dry_run else "baseline_json_written_and_delta_artifacts_created",
        },
    }


def _render_markdown(rep: dict) -> str:
    h = rep["headline_counts"]
    intel = rep.get("delta_intelligence") or {}
    metrics = intel.get("relationship_delta_metrics") or {}
    professional = intel.get("professional_change_detection") or {}
    strategic = intel.get("strategic_relationship_changes") or {}
    opportunity = intel.get("opportunity_detection") or {}
    graph = intel.get("graph_mutations") or {}
    daily = intel.get("daily_brief_mutations") or {}
    persistence = intel.get("persistence_verification") or {}
    trust = intel.get("trust_statistics") or {}
    segments = intel.get("segment_deltas") or []
    activation = intel.get("career_activation") or []

    mutation_report = mr.MutationReport(
        source_label="LinkedIn Connections Export",
        date=rep["ingest_date"],
        people_imported=h["connections_in_export"],
        existing_people_updated=h["matched_existing"],
        new_people_created=h["new_connections"],
        duplicate_candidates=None,
        companies_added=h.get("new_connection_companies_added"),
        relationship_links_created=h["company_changes"] + h["role_changes"],
        knowledge_mutations_applied=h["matched_existing"] + h["new_connections"],
        confidence=f"{trust.get('confidence_score', '—')}%",
        not_computed_reasons=[
            "Duplicate Candidates not computed — LinkedIn URL/email match is deterministic, "
            "with no ambiguous-name bucket the way HubSpot's/Apple Contacts' name-only matches have.",
        ],
    )

    out = [
        f"# LinkedIn Relationship Intelligence - {rep['ingest_date']}",
        "",
        f"Source: `{rep['source_file']}` · Confidence: **{trust.get('confidence_score', '—')}%** · "
        f"Sources: {', '.join(trust.get('sources_present') or ['Connections.csv'])} · "
        f"Connections analyzed: {trust.get('connections_analyzed', h['connections_in_export']):,}",
        "",
        mutation_report.render_markdown().split("\n", 2)[2].strip("\n"),
        "",
    ]

    if rep.get("warnings"):
        out.extend(["## ⚠ Safeguard warnings", ""])
        out.extend(f"- {w}" for w in rep["warnings"])
        out.append("")

    # --- Action Queue (intelligence-first) ---
    out.extend(["## What Should Todd Do Now?", ""])
    high_activation = [a for a in activation if a["priority_score"] >= 7.0]
    med_activation = [a for a in activation if 4.0 <= a["priority_score"] < 7.0]
    queue = opportunity.get("suggested_outreach_queue") or []

    if high_activation:
        out.extend(["### High Priority", ""])
        for a in high_activation[:10]:
            domain_tag = " 🎯" if a.get("strategic_domain") else ""
            out.append(
                f"- **{a['person']}** ({a['relationship_strength']} · {a['change_significance']} significance · "
                f"Priority {a['priority_score']}/10){domain_tag}  \n"
                f"  Change: {a['old_role'] or '-'} @ {a['old_company'] or '-'} → "
                f"{a['new_role'] or '-'} @ {a['new_company'] or '-'}  \n"
                f"  Action: {a['recommended_action']}"
            )
    if med_activation:
        out.extend(["", "### Medium Priority", ""])
        for a in med_activation[:10]:
            out.append(
                f"- **{a['person']}** ({a['relationship_strength']} · Priority {a['priority_score']}/10)  \n"
                f"  {a['change_type'].replace('_', ' ').title()}: {a['new_role'] or '-'} @ {a['new_company'] or '-'}  \n"
                f"  Action: {a['recommended_action']}"
            )
    if not high_activation and not med_activation and queue:
        for o in queue[:12]:
            out.append(f"- **{o.get('person')}** — {o.get('action')} _{o.get('why')}_")
    if not high_activation and not med_activation and not queue:
        out.append("No outreach candidates exceeded priority thresholds for this export delta.")

    # --- Network Segment Delta ---
    out.extend(["", "## Network Segment Comparison", ""])
    out.extend(["| Segment | Previous | Current | Delta |", "|---|---:|---:|---:|"])
    for seg in segments:
        delta_str = f"+{seg['delta']}" if seg['delta'] > 0 else str(seg['delta'])
        out.append(f"| {seg['segment']} | {seg['previous']:,} | {seg['current']:,} | {delta_str} |")

    # --- Baseline Comparison ---
    out.extend(["", "## Baseline Comparison", ""])
    out.extend(["| Metric | Count |", "|---|---:|"])
    out.extend([
        f"| Prior baseline records | {metrics.get('prior_baseline_record_count', h['baseline_before']):,} |",
        f"| Current baseline records | {metrics.get('current_baseline_record_count', h['baseline_after']):,} |",
        f"| Connections in export | {metrics.get('connections_in_export', h['connections_in_export']):,} |",
        f"| Matched existing | {h['matched_existing']:,} |",
        f"| Net-new relationships | {metrics.get('net_new_relationships', h['new_connections']):,} |",
        f"| Lost relationships | {metrics.get('lost_relationships', h['disconnections']):,} |",
        f"| Reconnections | {metrics.get('reconnections', h['reconnections']):,} |",
        f"| Conflicts held | {h['conflicts']:,} |",
    ])

    # --- Employment Status Reconciliation (RB ended-role cleanup, 2026-08-06) ---
    recon = rep.get("employment_reconciliation") or {}
    out.extend(["", "## Employment Status Reconciliation", ""])
    out.extend(["| Category | Count |", "|---|---:|"])
    out.extend([
        f"| New current role | {len(recon.get('new_current_role') or []):,} |",
        f"| Ended role, no stated successor | {len(recon.get('ended_no_successor') or []):,} |",
        f"| Stale export conflict suppressed | {len(recon.get('stale_conflict_suppressed') or []):,} |",
        f"| Employment dates unavailable | {len(recon.get('dates_unavailable') or []):,} |",
        f"| Needs operator review | {len(recon.get('needs_operator_review') or []):,} |",
    ])
    if recon.get("note"):
        out.extend(["", f"_{recon['note']}_"])
    if recon.get("stale_conflict_suppressed"):
        out.extend(["", "### Stale Export Conflicts Suppressed"])
        for s in recon["stale_conflict_suppressed"][:12]:
            out.append(
                f"- **{s['name']}**: export still shows "
                f"{s.get('stale_export_company') or '-'} / {s.get('stale_export_role') or '-'}; "
                f"canonical state is {s.get('canonical_status')} since "
                f"{s.get('canonical_since') or 'unknown date'}."
            )

    # --- Professional Change Detection ---
    out.extend(["", "## Professional Change Detection", ""])
    out.extend(["| Signal | Count |", "|---|---:|"])
    out.extend([
        f"| Title changes | {professional.get('title_changes', h['role_changes']):,} |",
        f"| Company changes | {professional.get('company_changes', h['company_changes']):,} |",
        f"| Promotions | {professional.get('promotions', 0):,} |",
        f"| Recruiter additions | {metrics.get('recruiter_additions', 0):,} |",
        f"| Executive additions | {metrics.get('executive_additions', 0):,} |",
        f"| Enterprise buyer additions | {metrics.get('enterprise_buyer_additions', 0):,} |",
        f"| Restaurant-tech adjacency expansion | {metrics.get('restaurant_tech_adjacency_expansion', 0):,} |",
        f"| Strategic cluster changes | {metrics.get('strategic_industry_cluster_changes', 0):,} |",
    ])
    if rep["rc_moves"]:
        out.extend(["", "### RC Tier Moves"])
        for m in rep["rc_moves"][:12]:
            out.append(
                f"- **{m['name']}** ({_movement_type(m)}): "
                f"{m['old_company'] or '-'} / {m['old_role'] or '-'} → "
                f"{m['new_company'] or '-'} / {m['new_role'] or '-'}"
            )
    if rep["lki_moves"]:
        out.extend(["", "### LKI Tier Moves"])
        for m in rep["lki_moves"][:15]:
            out.append(
                f"- **{m['name']}** ({_movement_type(m)}): "
                f"{m['old_company'] or '-'} / {m['old_role'] or '-'} → "
                f"{m['new_company'] or '-'} / {m['new_role'] or '-'}"
            )

    # --- Strategic Relationship Changes ---
    out.extend(["", "## Strategic Relationship Changes", ""])
    out.append(f"{strategic.get('materially_increased_value_count', 0)} strategic relationships materially increased in value.")
    if strategic.get("who_matters_now"):
        out.extend(["", "### Who Matters Now"])
        for item in strategic["who_matters_now"][:12]:
            out.append(
                f"- **{item.get('name')}**: {item.get('new_company') or '-'} / "
                f"{item.get('new_role') or '-'} — {item.get('reason') or 'LinkedIn delta raised strategic value.'}"
            )
    if strategic.get("newly_relevant_contacts"):
        out.extend(["", "### Newly Relevant Contacts"])
        for item in strategic["newly_relevant_contacts"][:12]:
            out.append(
                f"- **{item.get('name')}**: {item.get('company') or '-'} / "
                f"{item.get('role') or '-'} — {item.get('reason')}"
            )
    if strategic.get("account_map_signals"):
        out.extend(["", "### Account Map Signals"])
        for item in strategic["account_map_signals"][:12]:
            out.append(
                f"- **{item.get('person')}** → {item.get('account')} "
                f"({item.get('company') or '-'} / {item.get('role') or '-'}, "
                f"{item.get('movement_type')})"
            )

    # --- Post-Ingest Intelligence (RB-DEFECT-064 Phase 5) ---
    pii_intel = intel.get("post_ingest_intelligence") or {}
    dormant = pii_intel.get("dormant_relationships_resurfaced") or []
    warm_intros = pii_intel.get("warm_intro_candidates") or []
    out.extend(["", "## Post-Ingest Intelligence", ""])
    if dormant:
        out.append(f"**Dormant relationships resurfaced ({len(dormant)}):**")
        out.append("")
        for d_entry in dormant:
            days = d_entry["days_since_last_touch"]
            age = "no last_touch on file" if days is None else f"last touch {days} days ago"
            out.append(f"- {d_entry['name']} — {age}")
        out.append("")
    else:
        out.append("- No dormant relationships among the contacts this import touched.")
        out.append("")
    if warm_intros:
        out.append(f"**Warm introduction candidates ({len(warm_intros)}):**")
        out.append("")
        for w in warm_intros:
            out.append(f"- {w['new_contact']} ({w['company']}) — via {w['broker_name']}: {w['reason']}")
        out.append("")
    else:
        out.append("- No warm-intro broker found for any newly created contact's company.")
        out.append("")

    # --- Trust Statistics ---
    out.extend(["", "## Intelligence Trust Statistics", ""])
    out.extend([
        f"- Confidence score: **{trust.get('confidence_score', '—')}%**",
        f"- Sources present: {', '.join(trust.get('sources_present') or [])}",
        f"- Source coverage: {trust.get('source_coverage_pct', 0)}% of known LinkedIn export files",
        f"- Connections analyzed: {trust.get('connections_analyzed', 0):,}",
        f"- Baseline records: {trust.get('baseline_record_count', 0):,}",
        f"- Connection coverage: {trust.get('connection_coverage_pct', 0)}% of baseline",
        f"- Note: {trust.get('notes', '')}",
    ])

    # --- Graph Mutations ---
    out.extend(["", "## Graph Mutations", ""])
    out.extend([
        f"- Persistent graph mutated: {graph.get('persistent_graph_mutated')}",
        f"- Baseline entries added: {graph.get('baseline_entries_added', 0)}",
        f"- Baseline entries updated: {graph.get('baseline_entries_updated', 0)}",
        f"- Relationship strength mutations: {graph.get('relationship_strength_mutations', 0)}",
        f"- Strategic importance mutations: {graph.get('strategic_importance_mutations', 0)}",
        f"- Opportunity graph mutations: {graph.get('opportunity_graph_mutations', 0)}",
        f"- Who Matters Now mutations: {graph.get('who_matters_now_mutations', 0)}",
        f"- Mutation tag: `{graph.get('mutation_tag')}`",
    ])
    for note in graph.get("notes") or []:
        out.append(f"- {note}")

    # --- Daily Brief Mutations ---
    out.extend(["", "## Daily Brief Mutations", ""])
    out.extend([
        f"- Cache path: `{daily.get('cache_path')}`",
        f"- Section: {daily.get('section')}",
        f"- Items available for brief: {daily.get('items_available_for_brief', 0)}",
    ])
    for line in daily.get("top_lines") or []:
        out.append(f"- {line}")

    # --- Persistence Verification ---
    out.extend(["", "## Persistence Verification", ""])
    out.extend([
        f"- Status: {persistence.get('status')}",
        f"- Source tag: `{persistence.get('source_tag')}`",
        f"- Post-write validation: {persistence.get('post_write_validation')}",
    ])
    if rep.get("files_written"):
        for f in rep["files_written"]:
            out.append(f"- Wrote: `{f}`")

    out.extend(["", "## Activity Files Detected", ""])
    out.extend(["| File | Rows |", "|---|---:|"])
    for k, v in rep["activity_counts"].items():
        out.append(f"| {k} | {v:,} |")

    out.extend(["", "## Guardrails", ""])
    out.extend(f"- {g}" for g in rep["guardrails"])

    if rep["open_questions"]:
        out.extend(["", "## Open Questions", ""])
        out.extend(f"- {q}" for q in rep["open_questions"][:20])

    out.append("")
    out.append(f"*Generated by `system/scripts/linkedin_ingest.py` on {rep['ingest_date']}.*")
    return "\n".join(out) + "\n"


def _employment_reconciliation(delta: dict) -> dict:
    """Post-refresh reconciliation view distinguishing what this ingest can
    and cannot resolve about employment status (RB ended-role cleanup,
    2026-08-06, remaining-work item 6). Connections.csv carries no
    employment dates, so 'new current role' and 'ended role with no stated
    successor' can only be resolved by the dated linkedin_session_reader.py
    profile-capture path or explicit operator confirmation — this ingest
    reports those buckets empty rather than guessing."""
    by_id: dict[str, dict] = {}
    for ch in delta["company_changes"]:
        e = ch["entry"]
        row = by_id.setdefault(e["id"], {"name": e["name"], "id": e["id"]})
        row["old_company"] = ch["old"]
        row["new_company"] = e.get("current_company")
    for ch in delta["role_changes"]:
        e = ch["entry"]
        row = by_id.setdefault(e["id"], {"name": e["name"], "id": e["id"]})
        row["old_role"] = ch["old"]
        row["new_role"] = e.get("current_role")

    return {
        "new_current_role": [],
        "ended_no_successor": [],
        "stale_conflict_suppressed": list(delta["employment_status_suppressed"]),
        "dates_unavailable": list(by_id.values()),
        "needs_operator_review": list(delta["conflicts"]),
        "note": (
            "Connections.csv carries no employment dates, so this ingest path "
            "cannot resolve 'new current role' or 'ended role with no stated "
            "successor' on its own — those require a dated LinkedIn profile "
            "capture (linkedin_session_reader.py) or explicit operator "
            "confirmation."
        ),
    }


def _update_entry(
    entry: dict,
    *,
    company: str | None,
    role: str | None,
    connected_on: str | None,
    email: str | None,
    source_tag: str,
    d: date,
    delta: dict,
) -> None:
    """Merge one LinkedIn export row into an existing baseline `entry` in
    place, recording reconnections, company/role/email changes (including
    RB-DEFECT-041 Enhancement #4's `company_history`/`title_history`
    arrays, and `email_history` alongside them -- 2026-09-19, same
    overwrite-permitted-with-delta-and-history policy extended to contact
    information, not just job title/company), conflicts, and rc/lki moves
    into `delta`. Shared by `ingest()` and `ingest_from_csv_text()`."""
    entry.setdefault("sources", [])
    if source_tag not in entry["sources"]:
        entry["sources"].append(source_tag)
    if connected_on and entry.get("linkedin_connected_on") != connected_on:
        entry["linkedin_connected_on"] = connected_on

    old_email = entry.get("email")
    email_changed = bool(email and _norm(email) != _norm(old_email))
    if email_changed:
        if _is_operator_confirmed(entry):
            delta["conflicts"].append({
                "name": entry["name"],
                "field": "email",
                "canonical": old_email,
                "linkedin": email,
            })
            _append_note(entry, f"[{d.isoformat()}] CONFLICT: LinkedIn reads email {email}; canonical retained as {old_email}. Confirm.")
        else:
            entry.setdefault("email_history", []).append({"email": old_email, "changed_on": d.isoformat()})
            entry["email"] = email
            _add_tag(entry, f"linkedin_delta_{d.isoformat()}")
            delta["email_changes"].append({"entry": entry, "old": old_email, "new": email})
            _append_note(entry, f"[{d.isoformat()}] LinkedIn: email {old_email or '-'} -> {email}.")

    tags = entry.setdefault("tags", [])
    disconnected_tags = [t for t in tags if t.startswith("linkedin_disconnected_")]
    if disconnected_tags:
        entry["tags"] = [t for t in tags if not t.startswith("linkedin_disconnected_")]
        _add_tag(entry, f"linkedin_delta_{d.isoformat()}")
        _append_note(entry, f"[{d.isoformat()}] LinkedIn: reconnected in export.")
        delta["reconnections"].append(entry)

    old_company = entry.get("current_company")
    old_role = entry.get("current_role")
    company_changed = bool(company and _norm(company) != _norm(old_company))
    role_changed = bool(role and not _same_title(role, old_role, company or old_company))

    if (company_changed or role_changed) and is_employment_state_protected(entry):
        # RB employment-status cleanup (2026-08-06): Connections.csv carries no
        # employment dates, so it can never resolve a dated/operator-confirmed
        # no_stated_current_role state. Suppress instead of restoring the
        # stale company/title — see CLAUDE_HANDOFF_LINKEDIN_ENDED_ROLE_
        # CURRENT_COMPANY_CLEANUP_2026-08-06.md, remaining-work item 3.
        canonical_status = entry.get("employment_status")
        canonical_since = entry.get("employment_status_observed_at")
        delta.setdefault("employment_status_suppressed", []).append({
            "name": entry["name"],
            "id": entry["id"],
            "stale_export_company": company,
            "stale_export_role": role,
            "canonical_status": canonical_status,
            "canonical_since": canonical_since,
        })
        _append_note(
            entry,
            f"[{d.isoformat()}] LinkedIn export still shows "
            f"{company or '-'} / {role or '-'}; canonical state is "
            f"{canonical_status} since {canonical_since or 'unknown date'}. "
            f"Suppressed, not restored.",
        )
        audit_log.log_mutation_rejected(
            f"LinkedIn Connections export company/role for {entry['name']} suppressed: "
            f"canonical employment_status={canonical_status}.",
            reason="employment_status_protected",
            profile_context=entry.get("id"),
        )
        return

    if company_changed:
        if _is_operator_confirmed(entry):
            delta["conflicts"].append({
                "name": entry["name"],
                "field": "current_company",
                "canonical": old_company,
                "linkedin": company,
            })
            _append_note(entry, f"[{d.isoformat()}] CONFLICT: LinkedIn reads company {company}; canonical retained as {old_company}. Confirm.")
        else:
            entry.setdefault("company_history", []).append({"company": old_company, "changed_on": d.isoformat()})
            entry["current_company"] = company
            _add_tag(entry, f"linkedin_delta_{d.isoformat()}")
            delta["company_changes"].append({"entry": entry, "old": old_company, "new": company})
            _append_note(entry, f"[{d.isoformat()}] LinkedIn: company {old_company or '-'} -> {company}.")
    if role_changed:
        if _is_operator_confirmed(entry):
            delta["conflicts"].append({
                "name": entry["name"],
                "field": "current_role",
                "canonical": old_role,
                "linkedin": role,
            })
            _append_note(entry, f"[{d.isoformat()}] CONFLICT: LinkedIn reads role {role}; canonical retained as {old_role}. Confirm.")
        else:
            entry.setdefault("title_history", []).append({"role": old_role, "changed_on": d.isoformat()})
            entry["current_role"] = role
            _add_tag(entry, f"linkedin_delta_{d.isoformat()}")
            delta["role_changes"].append({"entry": entry, "old": old_role, "new": role})
            _append_note(entry, f"[{d.isoformat()}] LinkedIn: role {old_role or '-'} -> {role}.")
    if company_changed or role_changed:
        move = {
            "name": entry["name"],
            "id": entry["id"],
            "signal_class": entry.get("signal_class"),
            "rc_tier": entry.get("rc_tier"),
            "old_company": old_company,
            "new_company": entry.get("current_company"),
            "old_role": old_role,
            "new_role": entry.get("current_role"),
            "company_changed": company_changed,
            "role_changed": role_changed,
            "reason": "LinkedIn export shows company/title movement against prior baseline.",
            "entry_snapshot": deepcopy(entry),
        }
        if entry.get("signal_class") == "RC":
            delta["rc_moves"].append(move)
        elif entry.get("signal_class") == "LKI":
            delta["lki_moves"].append(move)


def ingest(
    path: str | Path,
    *,
    ingest_date: str | None = None,
    dry_run: bool = False,
    threads: list[dict] | None = None,
) -> dict[str, Any]:
    """`threads` overrides active-threads context for warm-intro scoring
    (RB-DEFECT-064 Phase 5) — tests pass `[]` so they never depend on the
    real active_threads.yaml; production leaves it None to load from disk."""
    src = Path(path)
    d = date.fromisoformat(ingest_date) if ingest_date else date.today()
    source_tag = f"linkedin_export_{d.isoformat()}"
    classification = classify_archive(src)
    if classification.get("artifact_type") != "linkedin_export_zip" or not classification.get("has_connections_csv"):
        raise ValueError(f"not a LinkedIn export ZIP with Connections.csv: {classification}")

    baseline = core.load_baseline()
    before_count = len(baseline)
    existing_linkedin_count = sum(
        1
        for e in baseline
        if e.get("linkedin_url")
        or any(str(s).startswith("linkedin_export_") for s in (e.get("sources") or []))
    )
    merged = deepcopy(baseline)
    matchers = _build_matchers(merged)
    existing_ids = {e["id"] for e in merged}
    matched_ids: set[str] = set()
    present_ids: set[str] = set()
    delta: dict[str, Any] = {
        "new": [], "company_changes": [], "role_changes": [], "email_changes": [], "conflicts": [],
        "employment_status_suppressed": [],
        "reconnections": [], "disconnections": [], "rc_moves": [], "lki_moves": [],
        "new_highlights": [],
    }
    redacted_rows = 0

    with zipfile.ZipFile(src) as zf:
        lower_to_name = {Path(n).name.lower(): n for n in zf.namelist() if not n.endswith("/")}
        rows = _read_csv_from_zip(zf, lower_to_name["connections.csv"])
        activity_counts = _activity_counts(zf)

    for row in rows:
        name = _display_name(row)
        url = (row.get("URL") or "").strip() or None
        email = (row.get("Email Address") or "").strip() or None
        company = (row.get("Company") or "").strip() or None
        role = (row.get("Position") or "").strip() or None
        connected_on = (row.get("Connected On") or "").strip() or None
        if not any([name, url, email]):
            redacted_rows += 1
            continue

        entry = _match_row(row, matchers)
        if entry is None:
            new_entry = {
                "id": _unique_id(name or _linkedin_slug(url) or "linkedin-contact", existing_ids),
                "name": name or (_linkedin_slug(url) or "LinkedIn Contact").replace("-", " ").title(),
                "current_company": company,
                "current_role": role,
                "location": None,
                "linkedin_url": url,
                "email": email,
                "phone": None,
                "sources": [source_tag],
                "linkedin_connected_on": connected_on,
                "signal_class": "VC",
                "rc_state": None,
                "rc_tier": None,
                "last_touch": None,
                "circles": [],
                "tags": [],
                "notes": "",
                "company_history": [],
                "title_history": [],
            }
            merged.append(new_entry)
            delta["new"].append(new_entry)
            present_ids.add(new_entry["id"])
            _add_tag(new_entry, f"linkedin_delta_{d.isoformat()}")
            if TITLE_PROMOTION_RX.search(role or "") or TARGET_DOMAIN_RX.search(f"{company or ''} {role or ''}"):
                delta["new_highlights"].append({
                    "name": new_entry["name"],
                    "company": company,
                    "role": role,
                    "reason": "Leadership or restaurant/hospitality/AI-adjacent signal in new connection.",
                })
            continue

        matched_ids.add(entry["id"])
        present_ids.add(entry["id"])
        _update_entry(
            entry,
            company=company,
            role=role,
            connected_on=connected_on,
            email=email,
            source_tag=source_tag,
            d=d,
            delta=delta,
        )

    for entry in merged:
        if entry["id"] in present_ids:
            continue
        source_blob = " ".join(entry.get("sources") or []).lower()
        tags = entry.setdefault("tags", [])
        if (
            (entry.get("linkedin_url") or "linkedin_export_" in source_blob)
            and "non_linkedin_contact" not in tags
            and not any(t.startswith("linkedin_disconnected_") for t in tags)
        ):
            tag = f"linkedin_disconnected_{d.isoformat()}"
            tags.append(tag)
            _add_tag(entry, f"linkedin_delta_{d.isoformat()}")
            _append_note(entry, f"[{d.isoformat()}] LinkedIn: connection lost (not present in export).")
            delta["disconnections"].append(entry)

    opportunities = []
    for move in delta["rc_moves"] + delta["lki_moves"][:20]:
        if TITLE_PROMOTION_RX.search(move.get("new_role") or ""):
            opportunities.append({
                "person": move["name"],
                "opportunity": "promotion or leadership-move outreach",
                "why": f"New role reads {move.get('new_role')}.",
            })
        if TARGET_DOMAIN_RX.search(" ".join(str(move.get(k) or "") for k in ("new_company", "new_role"))):
            opportunities.append({
                "person": move["name"],
                "opportunity": "restaurant/AI/hospitality warm-path scan",
                "why": "Move intersects target operating terrain.",
            })
    for n in delta["new_highlights"][:10]:
        opportunities.append({
            "person": n["name"],
            "opportunity": "new warm-entry candidate",
            "why": n["reason"],
        })

    delta_intelligence = _build_delta_intelligence(
        baseline_before=baseline,
        merged=merged,
        delta=delta,
        rows=rows,
        activity_counts=activity_counts,
        source_tag=source_tag,
        ingest_date=d.isoformat(),
        dry_run=dry_run,
    )
    # RB-DEFECT-064 Phase 5 — the same Stage 6 signal hubspot_ingest.py surfaces,
    # applied here so it's not HubSpot-only: existing contacts this import
    # touched that have gone quiet, and newly created contacts whose company
    # already has a baseline broker.
    baseline_by_id = {e["id"]: e for e in merged}
    delta_intelligence["post_ingest_intelligence"] = {
        "dormant_relationships_resurfaced": pii.dormant_relationships_resurfaced(
            list(matched_ids), baseline_by_id, today=d,
        ),
        "warm_intro_candidates": pii.warm_intro_candidates(
            delta["new"], merged, today=d, threads=threads,
        ),
    }
    report = {
        "ok": True,
        "dry_run": dry_run,
        "classification": classification,
        "source_file": src.name,
        "ingest_date": d.isoformat(),
        "headline_counts": {
            "connections_in_export": len(rows),
            "baseline_before": before_count,
            "baseline_after": len(merged),
            "new_connections": len(delta["new"]),
            "matched_existing": len(matched_ids),
            "redacted_rows_skipped": redacted_rows,
            "disconnections": len(delta["disconnections"]),
            "reconnections": len(delta["reconnections"]),
            "company_changes": len(delta["company_changes"]),
            "role_changes": len(delta["role_changes"]),
            "email_changes": len(delta["email_changes"]),
            "conflicts": len(delta["conflicts"]),
            "employment_status_suppressed": len(delta["employment_status_suppressed"]),
            "new_connection_companies_added": _count_new_companies(baseline, delta["new"]),
        },
        "warnings": [],
        "activity_counts": activity_counts,
        "rc_moves": delta["rc_moves"],
        "lki_moves": delta["lki_moves"],
        "employment_status_suppressed": delta["employment_status_suppressed"],
        "employment_reconciliation": _employment_reconciliation(delta),
        "opportunities": opportunities,
        "recommended_actions": [],
        "delta_intelligence": delta_intelligence,
        "network_visualization": _network_visualization_payload(merged, delta["new"]),
        "open_questions": [
            f"{c['name']}: confirm {c['field']} (canonical={c['canonical']!r}, LinkedIn={c['linkedin']!r})."
            for c in delta["conflicts"]
        ],
        "guardrails": [
            "Enhanced baseline; did not replace operator-owned fields wholesale.",
            "Did not promote signal_class from LinkedIn connection alone.",
            "Did not treat LinkedIn export presence as last_touch.",
            "Held operator-confirmed conflicts instead of overwriting them.",
            "Suppressed stale export company/role against a dated or operator-confirmed "
            "no_stated_current_role state instead of restoring it.",
        ],
        "files_written": [],
    }
    if existing_linkedin_count > 100 and len(rows) < existing_linkedin_count * 0.5:
        report["warnings"].append(
            "Connections.csv is less than half the persisted LinkedIn-derived baseline; "
            "this may be a partial archive and would create excessive disconnection deltas."
        )
        report["guardrails"].append(
            "Partial-export safeguard: destructive disconnection writes are blocked for undersized archives."
        )
    report["recommended_actions"] = _recommend_actions({**delta, "new_highlights": delta["new_highlights"]})
    report["summary_markdown"] = _render_markdown(report)

    if not dry_run:
        if report["warnings"]:
            raise ValueError("; ".join(report["warnings"]))
        core.SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
        DELTAS_DIR.mkdir(parents=True, exist_ok=True)
        stamp = d.isoformat()
        snapshot = core.SNAPSHOTS_DIR / f"baseline_index.pre-linkedin-ingest-{stamp}.json"
        i = 2
        while snapshot.exists():
            snapshot = core.SNAPSHOTS_DIR / f"baseline_index.pre-linkedin-ingest-{stamp}-{i}.json"
            i += 1
        shutil.copy2(core.BASELINE_PATH, snapshot)
        core.BASELINE_PATH.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
        delta_path = DELTAS_DIR / f"linkedin_export_{stamp}.md"
        i = 2
        while delta_path.exists():
            delta_path = DELTAS_DIR / f"linkedin_export_{stamp}-{i}.md"
            i += 1
        LINKEDIN_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        report["files_written"] = [
            _display_path(snapshot),
            _display_path(core.BASELINE_PATH),
            _display_path(delta_path),
            _display_path(LINKEDIN_CACHE_PATH),
        ]
        report["delta_intelligence"]["persistence_verification"]["post_write_validation"] = "baseline_json_written_delta_markdown_written_cache_written"
        report["summary_markdown"] = _render_markdown(report)
        delta_path.write_text(report["summary_markdown"], encoding="utf-8")
        from datetime import datetime, timezone as _tz
        cache_payload = {
            "_generated_at": datetime.now(tz=_tz.utc).isoformat(),  # required by cos_judgment freshness check
            "ingest_date": report["ingest_date"],
            "source_file": report["source_file"],
            "headline_counts": report["headline_counts"],
            "delta_intelligence": report["delta_intelligence"],
            "recommended_actions": report["recommended_actions"],
            "files_written": report["files_written"],
        }
        LINKEDIN_CACHE_PATH.write_text(json.dumps(cache_payload, indent=2) + "\n", encoding="utf-8")

        # Generate the three structured brief files and email them
        try:
            brief_paths = _generate_linkedin_briefs(report, delta, d)
            report["brief_files"] = [str(p) for p in brief_paths]
            email_result = _email_linkedin_briefs(d)
            report["email_result"] = email_result
        except Exception as _email_exc:
            report["email_result"] = {"sent": False, "status": "error", "detail": str(_email_exc)}

    return report


_RT_KEYWORDS = [
    "restaurant", "pos", "payment", "hospitality", "food", "beverage", "qsr",
    "fast food", "franchise", "dining", "culinary", "toast", "par ", "olo",
    "ncr", "oracle", "global payments", "agilysys", "lightspeed", "revel",
    "shift4", "micros", "brink", "xenial", "intouch", "starbucks", "mcdonald",
    "yum", "darden", "bloomin", "dine brands", "jack in the box", "burger king",
    "wendy", "subway", "panera", "chipotle", "portillo", "sweetgreen",
    "shake shack", "cracker barrel", "denny", "ihop", "waffle", "noodles",
    "panda", "thrive restaurant", "qu ", "revel systems", "olo", "grubbrr",
    "xpr pos", "brink pos", "lightspeed restaurant", "touchpoint restaurant",
    "restaurant365", "harri", "hme", "drive-thru", "drive thru",
]
_SENIOR_TITLES = [
    "vp", "vice president", "svp", "evp", "chief", "ceo", "cto", "coo",
    "cmo", "cfo", "president", "director", "head of", "founder", "partner",
    "principal", "managing", "general manager", "owner",
]
_SENIORITY_LADDER = [
    "coordinator", "specialist", "analyst", "associate", "manager", "senior",
    "director", "vp", "vice president", "svp", "evp", "chief", "president",
    "ceo", "cto", "coo", "cmo",
]


def _is_rt(company: str, role: str) -> bool:
    text = f"{company} {role}".lower()
    return any(kw in text for kw in _RT_KEYWORDS)


def _is_senior(role: str) -> bool:
    return any(t in role.lower() for t in _SENIOR_TITLES)


def _change_score(old_co: str, new_co: str, old_role: str, new_role: str) -> int:
    score = 0
    if old_co != new_co:
        score += 3
    if _is_rt(new_co, new_role) or _is_rt(old_co, old_role):
        score += 3
    if _is_senior(new_role):
        score += 2
    old_lv = next((i for i, t in enumerate(_SENIORITY_LADDER) if t in old_role.lower()), -1)
    new_lv = next((i for i, t in enumerate(_SENIORITY_LADDER) if t in new_role.lower()), -1)
    if new_lv > old_lv >= 0:
        score += 2
    return score


def _generate_linkedin_briefs(report: dict, delta: dict, d: "date") -> list:
    """Write the three LinkedIn brief files to BRIEFS_DIR and return their paths."""
    core.BRIEFS_DIR.mkdir(parents=True, exist_ok=True)
    h = report["headline_counts"]
    ingest_date = d.isoformat()
    stamp = ingest_date

    # ---- score role/company changes ----
    scored: list[tuple[int, dict]] = []
    for ch in delta["company_changes"]:
        entry = ch["entry"]
        old_co, new_co = ch["old"] or "", entry.get("current_company") or ""
        old_role = entry.get("current_role") or ""
        s = _change_score(old_co, new_co, old_role, old_role)
        scored.append((s, {"name": entry["name"], "old_company": old_co,
                           "new_company": new_co, "old_role": old_role,
                           "new_role": old_role, "change_type": "COMPANY CHANGE"}))
    for ch in delta["role_changes"]:
        entry = ch["entry"]
        co = entry.get("current_company") or ""
        old_role, new_role = ch["old"] or "", entry.get("current_role") or ""
        s = _change_score(co, co, old_role, new_role)
        scored.append((s, {"name": entry["name"], "old_company": co,
                           "new_company": co, "old_role": old_role,
                           "new_role": new_role, "change_type": "TITLE CHANGE"}))
    scored.sort(key=lambda x: -x[0])
    high_priority = [(s, c) for s, c in scored if s >= 6]
    medium_priority = [(s, c) for s, c in scored if 3 <= s < 6]

    # ---- high-value new connections ----
    hv_new = [e for e in delta["new"]
               if _is_rt(e.get("current_company") or "", e.get("current_role") or "")
               or _is_senior(e.get("current_role") or "")]

    # ================================================================
    # Report 1: Intelligence Report
    # ================================================================
    intel_lines = [
        f"# LinkedIn Intelligence Report",
        f"**Generated:** {ingest_date}  ",
        f"**Export date:** {ingest_date}  ",
        "",
        "## Executive Summary",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Prior baseline | {h['baseline_before']:,} connections |",
        f"| New snapshot | {h['connections_in_export']:,} connections |",
        f"| Net change | {h['connections_in_export'] - h['baseline_before']:+,} |",
        f"| New connections | {h['new_connections']:,} |",
        f"| Removed connections | {h['disconnections']:,} |",
        f"| Role/company changes | {h['company_changes'] + h['role_changes']:,} |",
        f"| High-priority signals | {len(high_priority)} |",
        "",
        "## High-Priority Signals",
        "",
    ]
    if high_priority:
        for score, c in high_priority[:20]:
            rt_tag = " \\[RESTAURANT TECH\\]" if _is_rt(c["new_company"], c["new_role"]) or _is_rt(c["old_company"], c["old_role"]) else ""
            intel_lines += [
                f"### {c['name']} — {c['change_type']}{rt_tag}",
                f"**Was:** {c['old_role']} @ {c['old_company']}  ",
                f"**Now:** {c['new_role']} @ {c['new_company']}  ",
                f"Signal score: {score}",
                "",
            ]
    else:
        intel_lines.append("No high-priority signals detected in this delta.")
        intel_lines.append("")

    if medium_priority:
        intel_lines += ["## Medium-Priority Signals", ""]
        for score, c in medium_priority[:15]:
            intel_lines.append(
                f"- **{c['name']}** — {c['change_type']}: {c['old_role']} @ {c['old_company']} → "
                f"{c['new_role']} @ {c['new_company']}"
            )
        intel_lines.append("")

    intel_lines += ["## High-Value New Connections", ""]
    if hv_new:
        intel_lines += ["| Name | Role | Company |", "|---|---|---|"]
        for e in hv_new[:20]:
            intel_lines.append(
                f"| {e.get('name','')} | {e.get('current_role','')} | {e.get('current_company','')} |"
            )
    else:
        intel_lines.append("No high-value new connections detected.")

    intel_lines += [
        "",
        "## Recommended Actions",
        "",
    ]
    for ra in (report.get("recommended_actions") or [])[:15]:
        p = ra.get("person") or ra.get("name") or ""
        action = ra.get("action") or ra.get("recommended_action") or ""
        why = ra.get("why") or ra.get("reason") or ""
        intel_lines.append(f"- **{p}** — {action}" + (f" _{why}_" if why else ""))

    intel_lines.append("")
    intel_lines.append(f"*Generated by RB LinkedIn Intelligence Pipeline. Baseline: {ingest_date}.*")

    intel_path = core.BRIEFS_DIR / f"{stamp}-linkedin-intelligence-report.md"
    intel_path.write_text("\n".join(intel_lines), encoding="utf-8")

    # ================================================================
    # Report 2: Contact Rationalization
    # ================================================================
    rat_lines = [
        f"# LinkedIn Contact Rationalization Report",
        f"**Generated:** {ingest_date}  ",
        f"**LinkedIn snapshot:** {ingest_date} ({h['connections_in_export']:,} connections)  ",
        f"**Policy:** Explicit LinkedIn export facts auto-apply; inferred information remains advisory; absent connections never delete contacts.",
        "",
        "## Summary",
        "",
        "| Category | Count |",
        "|---|---|",
        f"| Total LinkedIn connections | {h['connections_in_export']:,} |",
        f"| New connections since last export | {h['new_connections']:,} |",
        f"| Connections absent from this export (contacts retained) | {h['disconnections']:,} |",
        f"| Role/company changes | {h['company_changes'] + h['role_changes']:,} |",
        "",
        "## New Connections Added to Baseline",
        "",
        "These records were created automatically from explicit rows in the LinkedIn export. "
        "Their imported name, company, title, LinkedIn URL, email (when supplied), and connection date are source facts; "
        "no inferred tier, trust state, or relationship judgment was added.",
        "",
    ]
    if hv_new:
        rat_lines += ["| Name | Role | Company |", "|---|---|---|"]
        for e in hv_new[:20]:
            rat_lines.append(
                f"| {e.get('name','')} | {e.get('current_role','')} | {e.get('current_company','')} |"
            )
    else:
        rat_lines.append("No high-value new connections detected.")

    rat_lines += [
        "",
        "## Applied Role and Company Updates",
        "",
        "| Name | Old Role/Company | New Role/Company |",
        "|---|---|---|",
    ]
    for s, c in high_priority[:15]:
        rat_lines.append(
            f"| {c['name']} | {c['old_role']} @ {c['old_company']} | {c['new_role']} @ {c['new_company']} |"
        )

    if delta["disconnections"]:
        rat_lines += [
            "",
            "## Connections Absent From This Export — Contacts Retained",
            "",
            "RB recorded the missing LinkedIn edge for reconciliation. It did not delete or demote any contact.",
            "",
        ]
        for e in delta["disconnections"][:10]:
            rat_lines.append(
                f"- {e.get('name','')} — {e.get('current_role','')} @ {e.get('current_company','')}"
            )

    rat_lines.append("")
    rat_lines.append(
        f"*Applied {h['new_connections']} new-contact facts and "
        f"{h['company_changes'] + h['role_changes']} company/title changes. "
        "Recommendations and other inferred judgments remain unmutated.*"
    )

    rat_path = core.BRIEFS_DIR / f"{stamp}-linkedin-contact-rationalization.md"
    rat_path.write_text("\n".join(rat_lines), encoding="utf-8")

    # ================================================================
    # Report 3: Mutation Package
    # ================================================================
    mut_lines = [
        f"# RB LinkedIn Mutation Receipt",
        f"**Generated:** {ingest_date}  ",
        f"**Source:** LinkedIn Export Delta  ",
        f"**Policy:** Explicit imported facts auto-apply. Inferences remain advisory. Missing connections never delete contacts.",
        "",
        "## Applied Fact Mutations",
        "",
    ]
    for idx, (score, c) in enumerate(high_priority[:15], 1):
        rt_note = " Relevant to restaurant tech ecosystem." if _is_rt(c["new_company"], c["new_role"]) or _is_rt(c["old_company"], c["old_role"]) else ""
        kind = "company_change" if c["old_company"] != c["new_company"] else "title_change"
        mut_lines += [
            f"### MUTATION-{idx:03d}: {c['name']}",
            f"- **Type:** {c['change_type']}",
            f"- **Was:** {c['old_role']} @ {c['old_company']}",
            f"- **Now:** {c['new_role']} @ {c['new_company']}",
            f"- **Source status:** APPLIED from LinkedIn export {ingest_date}",
            f"- **Advisory relevance score:** {score}/10 (does not control mutation)",
            f"- **Note:**{rt_note} Imported fact type: `{kind}`. Any interpretation or outreach recommendation remains advisory.",
            "",
        ]

    if delta["new"] and hv_new:
        mut_lines += [
            "## New Contact Records Applied From Export Facts",
            "",
        ]
        for e in hv_new[:10]:
            mut_lines.append(f"- {e.get('name','')} — {e.get('current_role','')} @ {e.get('current_company','')}")

    mut_lines += [
        "",
        f"## Baseline Archive",
        "",
        f"LinkedIn export {ingest_date} archived. Now the active baseline for future delta analysis.",
        "",
        f"*{len(high_priority)} high-priority fact mutations shown above were already applied. "
        f"{len(hv_new)} high-value new records were created from explicit export rows. "
        "No inferred information was mutated and no contacts were deleted.*",
    ]

    mut_path = core.BRIEFS_DIR / f"{stamp}-linkedin-mutation-package.md"
    mut_path.write_text("\n".join(mut_lines), encoding="utf-8")

    return [intel_path, rat_path, mut_path]


def _email_linkedin_briefs(d: "date") -> dict:
    """Email the three LinkedIn brief files via the existing send_brief_email infrastructure."""
    import sys as _sys
    import importlib
    scripts_dir = str(core.SYSTEM_DIR / "scripts")
    if scripts_dir not in _sys.path:
        _sys.path.insert(0, scripts_dir)

    try:
        send_brief_email = importlib.import_module("send_brief_email")
    except ImportError:
        return {"sent": False, "status": "import_error", "detail": "send_brief_email not importable"}

    return send_brief_email.send_linkedin_reports(d)


def ingest_from_csv_text(
    csv_text: str,
    *,
    source_filename: str = "Connections.csv",
    ingest_date: str | None = None,
    dry_run: bool = False,
    threads: list[dict] | None = None,
) -> dict[str, Any]:
    """Ingest LinkedIn connections directly from CSV text content.

    DEFECT-024 / GPT upload bridge: the Custom GPT extracts Connections.csv
    from the uploaded ZIP using Code Interpreter and passes the text content
    here. Avoids the need to binary-encode the entire ZIP.

    Shares all baseline-mutation logic with ingest(). Writes the same
    linkedin_ingest_latest.json cache and delta markdown.

    Args:
        csv_text:        Raw text content of Connections.csv.
        source_filename: Original filename for source tagging (default Connections.csv).
        ingest_date:     ISO date override; defaults to today.
        dry_run:         If True, compute delta but do not write files.
    """
    d = date.fromisoformat(ingest_date) if ingest_date else date.today()
    source_tag = f"linkedin_export_{d.isoformat()}"

    # Parse the CSV
    lines = csv_text.splitlines()
    header_idx = _find_header_index(lines)
    reader = csv.DictReader(io.StringIO("\n".join(lines[header_idx:])))
    rows: list[dict[str, str]] = [{k: (v or "").strip() for k, v in row.items()} for row in reader]

    if not rows:
        raise ValueError("CSV text parsed to zero rows — check that Connections.csv content was passed.")

    baseline = core.load_baseline()
    before_count = len(baseline)
    existing_linkedin_count = sum(
        1 for e in baseline
        if e.get("linkedin_url")
        or any(str(s).startswith("linkedin_export_") for s in (e.get("sources") or []))
    )
    merged = deepcopy(baseline)
    matchers = _build_matchers(merged)
    existing_ids = {e["id"] for e in merged}
    matched_ids: set[str] = set()
    present_ids: set[str] = set()
    delta: dict[str, Any] = {
        "new": [], "company_changes": [], "role_changes": [], "email_changes": [], "conflicts": [],
        "employment_status_suppressed": [],
        "reconnections": [], "disconnections": [], "rc_moves": [], "lki_moves": [],
        "new_highlights": [],
    }
    redacted_rows = 0

    for row in rows:
        name = _display_name(row)
        url = (row.get("URL") or "").strip() or None
        email = (row.get("Email Address") or "").strip() or None
        company = (row.get("Company") or "").strip() or None
        role = (row.get("Position") or "").strip() or None
        connected_on = (row.get("Connected On") or "").strip() or None
        if not any([name, url, email]):
            redacted_rows += 1
            continue

        entry = _match_row(row, matchers)
        if entry is None:
            new_entry = {
                "id": _unique_id(name or _linkedin_slug(url) or "linkedin-contact", existing_ids),
                "name": name or (_linkedin_slug(url) or "LinkedIn Contact").replace("-", " ").title(),
                "current_company": company, "current_role": role,
                "location": None, "linkedin_url": url, "email": email, "phone": None,
                "sources": [source_tag], "linkedin_connected_on": connected_on,
                "signal_class": "VC", "rc_state": None, "rc_tier": None,
                "last_touch": None, "circles": [], "tags": [], "notes": "",
                "company_history": [], "title_history": [],
            }
            merged.append(new_entry)
            delta["new"].append(new_entry)
            present_ids.add(new_entry["id"])
            _add_tag(new_entry, f"linkedin_delta_{d.isoformat()}")
            existing_ids.add(new_entry["id"])
        else:
            present_ids.add(entry["id"])
            matched_ids.add(entry["id"])
            _update_entry(
                entry,
                company=company,
                role=role,
                connected_on=connected_on,
                email=email,
                source_tag=source_tag,
                d=d,
                delta=delta,
            )

    # Disconnection detection
    for entry in merged:
        eid = entry.get("id", "")
        if eid in present_ids or eid in matched_ids:
            continue
        if not (
            entry.get("linkedin_url")
            or any(str(s).startswith("linkedin_export_") for s in (entry.get("sources") or []))
        ):
            continue
        delta["disconnections"].append(entry)

    # Build report using the same structure as ingest()
    report: dict[str, Any] = {
        "ingest_date": d.isoformat(),
        "source_file": source_filename,
        "source_method": "csv_text",
        "headline_counts": {
            "connections_in_export": len(rows),
            "baseline_before": before_count,
            "baseline_after": len(merged),
            "new_connections": len(delta["new"]),
            "matched_existing": len(matched_ids),
            "redacted_rows_skipped": redacted_rows,
            "disconnections": len(delta["disconnections"]),
            "reconnections": len(delta["reconnections"]),
            "company_changes": len(delta["company_changes"]),
            "role_changes": len(delta["role_changes"]),
            "email_changes": len(delta["email_changes"]),
            "conflicts": len(delta["conflicts"]),
            "new_connection_companies_added": _count_new_companies(baseline, delta["new"]),
        },
        "delta_intelligence": {
            "relationship_delta_metrics": {
                "prior_baseline_record_count": before_count,
                "current_baseline_record_count": len(merged),
                "connections_in_export": len(rows),
                "matched_existing": len(matched_ids),
                "net_new_relationships": len(delta["new"]),
                "lost_relationships": len(delta["disconnections"]),
                "reconnections": len(delta["reconnections"]),
            },
            "persistence_verification": {
                "post_write_validation": "pending" if not dry_run else "dry_run",
            },
            # RB-DEFECT-064 Phase 5 — same Stage 6 signal as ingest() and
            # hubspot_ingest.py: dormant relationships this import touched,
            # warm-intro candidates for newly created contacts.
            "post_ingest_intelligence": {
                "dormant_relationships_resurfaced": pii.dormant_relationships_resurfaced(
                    list(matched_ids), {e["id"]: e for e in merged}, today=d,
                ),
                "warm_intro_candidates": pii.warm_intro_candidates(
                    delta["new"], merged, today=d, threads=threads,
                ),
            },
        },
        "warnings": [],
        "guardrails": [],
        "activity_counts": {},
        "rc_moves": delta["rc_moves"],
        "lki_moves": delta["lki_moves"],
        "open_questions": [
            f"{c['name']}: confirm {c['field']} (canonical={c['canonical']!r}, LinkedIn={c['linkedin']!r})."
            for c in delta["conflicts"]
        ],
        "delta": {k: v for k, v in delta.items() if k != "new_highlights"},
        "employment_reconciliation": _employment_reconciliation(delta),
        "new_highlights": delta["new_highlights"],
        "files_written": [],
    }

    if existing_linkedin_count > 100 and len(rows) < existing_linkedin_count * 0.5:
        report["warnings"].append(
            "CSV row count is less than half the persisted LinkedIn-derived baseline; "
            "this may be a partial export."
        )

    report["recommended_actions"] = _recommend_actions(delta)
    report["summary_markdown"] = _render_markdown(report)

    if not dry_run:
        if report["warnings"]:
            raise ValueError("; ".join(report["warnings"]))
        core.SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
        DELTAS_DIR.mkdir(parents=True, exist_ok=True)
        stamp = d.isoformat()
        snapshot = core.SNAPSHOTS_DIR / f"baseline_index.pre-linkedin-ingest-{stamp}.json"
        i = 2
        while snapshot.exists():
            snapshot = core.SNAPSHOTS_DIR / f"baseline_index.pre-linkedin-ingest-{stamp}-{i}.json"
            i += 1
        shutil.copy2(core.BASELINE_PATH, snapshot)
        core.BASELINE_PATH.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
        delta_path = DELTAS_DIR / f"linkedin_export_{stamp}.md"
        i = 2
        while delta_path.exists():
            delta_path = DELTAS_DIR / f"linkedin_export_{stamp}-{i}.md"
            i += 1
        LINKEDIN_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        from datetime import datetime, timezone as _tz
        cache_payload = {
            "_generated_at": datetime.now(tz=_tz.utc).isoformat(),
            "ingest_date": report["ingest_date"],
            "source_file": report["source_file"],
            "headline_counts": report["headline_counts"],
            "delta_intelligence": report["delta_intelligence"],
            "recommended_actions": report["recommended_actions"],
            "files_written": report["files_written"],
        }
        report["files_written"] = [
            _display_path(snapshot),
            _display_path(core.BASELINE_PATH),
            _display_path(delta_path),
            _display_path(LINKEDIN_CACHE_PATH),
        ]
        report["delta_intelligence"]["persistence_verification"]["post_write_validation"] = (
            "baseline_json_written_delta_markdown_written_cache_written"
        )
        report["summary_markdown"] = _render_markdown(report)
        delta_path.write_text(report["summary_markdown"], encoding="utf-8")
        cache_payload["files_written"] = report["files_written"]
        LINKEDIN_CACHE_PATH.write_text(json.dumps(cache_payload, indent=2) + "\n", encoding="utf-8")

    return report


def main() -> int:
    p = argparse.ArgumentParser(description="Ingest or classify a LinkedIn export ZIP.")
    p.add_argument("zip_path")
    p.add_argument("--date", help="Ingest date YYYY-MM-DD, default today.")
    p.add_argument("--classify-only", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    if args.classify_only:
        result = classify_archive(args.zip_path)
    else:
        result = ingest(args.zip_path, ingest_date=args.date, dry_run=args.dry_run)

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(result.get("summary_markdown") or json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
