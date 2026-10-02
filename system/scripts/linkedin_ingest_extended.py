#!/usr/bin/env python3
"""
linkedin_ingest_extended.py — Supplemental LinkedIn export intelligence.

Processes the high-value datasets in a LinkedIn export beyond Connections.csv:

  Company Follows    → strategic interest signals, watchlist enrichment
  Invitations        → outbound prospecting + inbound relationship interest
  Endorsements       → relationship warmth signals, baseline enrichment
  Recommendations    → highest-quality relationship evidence

Each source produces canonical intelligence items and baseline mutations.
Mutations are proposed, not auto-applied — operator confirms via mutations.py.

CLI:
    python3 linkedin_ingest_extended.py --zip PATH [--dry-run] [--json]
    python3 linkedin_ingest_extended.py --company-follows TEXT --invitations TEXT ...
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
import zipfile
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rb_core as core  # noqa: E402

EXTENDED_CACHE_PATH = core.CACHE_DIR / "linkedin_extended_latest.json"

# ---------------------------------------------------------------------------
# Baseline helpers
# ---------------------------------------------------------------------------

def _linkedin_slug(url: str | None) -> str | None:
    if not url:
        return None
    m = re.search(r"linkedin\.com/in/([^/?&#]+)", url or "")
    return m.group(1).lower() if m else None


def _name_key(name: str) -> str:
    return re.sub(r"[^a-z]", "", (name or "").lower())


def _load_baseline() -> list[dict]:
    try:
        raw = json.loads(core.BASELINE_PATH.read_text())
        return raw if isinstance(raw, list) else list(raw.values())
    except Exception:
        return []


def _build_indexes(contacts: list[dict]) -> tuple[dict, dict]:
    """Return (slug_idx, name_idx)."""
    slug_idx: dict[str, dict] = {}
    name_idx: dict[str, dict] = {}
    for c in contacts:
        slug = _linkedin_slug(c.get("linkedin_url") or "")
        if slug:
            slug_idx[slug] = c
        key = _name_key(c.get("name") or "")
        if key:
            name_idx[key] = c
    return slug_idx, name_idx


def _match_by_url(url: str | None, slug_idx: dict) -> dict | None:
    slug = _linkedin_slug(url)
    return slug_idx.get(slug) if slug else None


def _match_by_name(name: str, name_idx: dict) -> dict | None:
    key = _name_key(name)
    if key and key in name_idx:
        return name_idx[key]
    parts = name.lower().split()
    if len(parts) >= 2:
        for bkey, bc in name_idx.items():
            bname = (bc.get("name") or "").lower()
            if parts[0] in bname and parts[-1] in bname:
                return bc
    return None


def _parse_li_date(raw: str) -> str | None:
    """Parse various LinkedIn date formats to ISO date string."""
    raw = (raw or "").strip()
    for fmt in (
        "%a %b %d %H:%M:%S UTC %Y",
        "%Y-%m-%d %H:%M:%S",
        "%m/%d/%y, %I:%M %p",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            pass
    # Try partial parse
    m = re.search(r"(\d{4})", raw)
    return None


def _co_name_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


# ---------------------------------------------------------------------------
# Company Follows processor
# ---------------------------------------------------------------------------

def process_company_follows(
    csv_text: str,
    contacts: list[dict],
    active_threads: list[dict] | None = None,
) -> dict:
    """Process Company Follows.csv.

    Matches followed companies against:
    - Active threads (confirms strategic interest in tracked targets)
    - Baseline contacts (finds baseline contacts at followed companies)

    Returns signals, thread matches, and unmatched company list.
    """
    rows = list(csv.DictReader(io.StringIO(csv_text)))
    active_threads = active_threads or []

    # Build thread company set
    thread_companies: set[str] = set()
    for t in active_threads:
        for co in (t.get("companies") or []):
            thread_companies.add(_co_name_key(co))

    # Build baseline company index
    baseline_cos: dict[str, list[dict]] = defaultdict(list)
    for c in contacts:
        co_key = _co_name_key(c.get("current_company") or "")
        if co_key:
            baseline_cos[co_key].append(c)

    thread_matches: list[dict] = []
    baseline_matches: list[dict] = []
    unmatched: list[dict] = []
    recent_follows: list[dict] = []  # followed in last 90 days

    cutoff = date.today().isoformat()
    ninety_days_ago = date.fromisoformat(cutoff).toordinal() - 90

    for row in rows:
        org = (row.get("Organization") or "").strip()
        followed_on_raw = row.get("Followed On") or ""
        followed_on = _parse_li_date(followed_on_raw)
        org_key = _co_name_key(org)

        entry: dict = {
            "company": org,
            "followed_on": followed_on,
            "thread_match": org_key in thread_companies,
            "baseline_contacts": [c.get("name") for c in baseline_cos.get(org_key, [])],
        }

        if followed_on:
            try:
                if date.fromisoformat(followed_on).toordinal() >= ninety_days_ago:
                    recent_follows.append(entry)
            except ValueError:
                pass

        if org_key in thread_companies:
            thread_matches.append(entry)
        elif baseline_cos.get(org_key):
            baseline_matches.append(entry)
        else:
            unmatched.append(entry)

    return {
        "total_follows": len(rows),
        "thread_matches": thread_matches,
        "baseline_matches": baseline_matches,
        "unmatched_count": len(unmatched),
        "recent_follows_90d": recent_follows,
        "new_opportunity_signals": [
            u["company"] for u in unmatched
            if u.get("followed_on") and u["followed_on"] >= date.today().replace(day=1).isoformat()
        ],
    }


# ---------------------------------------------------------------------------
# Invitations processor
# ---------------------------------------------------------------------------

def process_invitations(
    csv_text: str,
    contacts: list[dict],
) -> dict:
    """Process Invitations.csv.

    Sent invitations = outbound prospecting signals.
    Received invitations = inbound relationship interest.

    Matches against baseline via LinkedIn URL. Flags:
    - Sent to people not yet in baseline (new prospecting targets)
    - Received from people already in baseline (relationship strengthening)
    - Received from people not in baseline (inbound leads)
    """
    rows = list(csv.DictReader(io.StringIO(csv_text)))
    slug_idx, name_idx = _build_indexes(contacts)

    sent_matched: list[dict] = []
    sent_unmatched: list[dict] = []
    recv_matched: list[dict] = []
    recv_unmatched: list[dict] = []
    proposed_mutations: list[dict] = []

    for row in rows:
        direction = row.get("Direction", "")
        sent_at = _parse_li_date(row.get("Sent At") or "")
        message = (row.get("Message") or "").strip()

        if direction == "OUTGOING":
            to_name = (row.get("To") or "").strip()
            to_url = row.get("inviteeProfileUrl") or ""
            match = _match_by_url(to_url, slug_idx) or _match_by_name(to_name, name_idx)
            entry = {
                "name": to_name,
                "url": to_url,
                "sent_at": sent_at,
                "message": message[:200] if message else None,
                "baseline_id": match.get("id") if match else None,
                "signal_class": match.get("signal_class") if match else None,
            }
            if match:
                sent_matched.append(entry)
            else:
                sent_unmatched.append(entry)

        elif direction == "INCOMING":
            from_name = (row.get("From") or "").strip()
            from_url = row.get("inviterProfileUrl") or ""
            match = _match_by_url(from_url, slug_idx) or _match_by_name(from_name, name_idx)
            entry = {
                "name": from_name,
                "url": from_url,
                "sent_at": sent_at,
                "message": message[:200] if message else None,
                "baseline_id": match.get("id") if match else None,
                "signal_class": match.get("signal_class") if match else None,
                "rc_tier": match.get("rc_tier") if match else None,
            }
            if match:
                recv_matched.append(entry)
                # Inbound from known contact = relationship warmth signal
                if sent_at:
                    proposed_mutations.append({
                        "type": "inbound_invitation_signal",
                        "contact_id": match["id"],
                        "contact_name": match.get("name"),
                        "signal": f"[{sent_at}] LinkedIn inbound invitation received.",
                        "note": f"Inbound invitation {sent_at} — relationship warmth signal.",
                    })
            else:
                recv_unmatched.append(entry)

    # Recent received (last 60 days) not in baseline = new inbound leads
    sixty_days_ago = (date.today().toordinal() - 60)
    new_inbound_leads = [
        r for r in recv_unmatched
        if r.get("sent_at") and date.fromisoformat(r["sent_at"]).toordinal() >= sixty_days_ago
    ]

    return {
        "total_invitations": len(rows),
        "sent_total": len(sent_matched) + len(sent_unmatched),
        "sent_matched_to_baseline": len(sent_matched),
        "sent_unmatched": len(sent_unmatched),
        "received_total": len(recv_matched) + len(recv_unmatched),
        "received_matched_to_baseline": len(recv_matched),
        "received_unmatched": len(recv_unmatched),
        "new_inbound_leads_60d": new_inbound_leads[:20],
        "sent_unmatched_recent": [
            s for s in sent_unmatched
            if s.get("sent_at") and date.fromisoformat(s["sent_at"]).toordinal() >= sixty_days_ago
        ][:20],
        "received_rc_contacts": [r for r in recv_matched if r.get("rc_tier")],
        "proposed_mutations": proposed_mutations,
    }


# ---------------------------------------------------------------------------
# Endorsements processor
# ---------------------------------------------------------------------------

def process_endorsements(
    csv_text: str,
    contacts: list[dict],
) -> dict:
    """Process Endorsement_Received_Info.csv.

    Matches endorsers against baseline via LinkedIn URL.
    Recent endorsements from known contacts = relationship warmth signal.
    """
    rows = list(csv.DictReader(io.StringIO(csv_text)))
    slug_idx, name_idx = _build_indexes(contacts)

    matched: list[dict] = []
    unmatched: list[dict] = []
    proposed_mutations: list[dict] = []
    skill_counts: dict[str, int] = defaultdict(int)

    ninety_days_ago = (date.today().toordinal() - 90)

    for row in rows:
        skill = (row.get("Skill Name") or "").strip()
        first = (row.get("Endorser First Name") or "").strip()
        last = (row.get("Endorser Last Name") or "").strip()
        name = f"{first} {last}".strip()
        url = row.get("Endorser Public Url") or ""
        endorsed_on_raw = row.get("Endorsement Date") or ""
        endorsed_on = _parse_li_date(endorsed_on_raw)
        status = row.get("Endorsement Status") or ""

        if status.upper() != "ACCEPTED":
            continue

        skill_counts[skill] += 1
        match = _match_by_url(url, slug_idx) or _match_by_name(name, name_idx)

        entry = {
            "name": name,
            "url": url,
            "skill": skill,
            "endorsed_on": endorsed_on,
            "baseline_id": match.get("id") if match else None,
            "signal_class": match.get("signal_class") if match else None,
            "rc_tier": match.get("rc_tier") if match else None,
        }

        if match:
            matched.append(entry)
            # Recent endorsement from known contact = warmth signal
            if endorsed_on:
                try:
                    if date.fromisoformat(endorsed_on).toordinal() >= ninety_days_ago:
                        proposed_mutations.append({
                            "type": "endorsement_signal",
                            "contact_id": match["id"],
                            "contact_name": match.get("name"),
                            "skill": skill,
                            "endorsed_on": endorsed_on,
                            "note": f"[{endorsed_on}] LinkedIn endorsement received: {skill}.",
                        })
                except ValueError:
                    pass
        else:
            unmatched.append(entry)

    top_skills = sorted(skill_counts.items(), key=lambda x: -x[1])[:10]
    recent_matched = [
        e for e in matched
        if e.get("endorsed_on") and
        date.fromisoformat(e["endorsed_on"]).toordinal() >= ninety_days_ago
    ]

    return {
        "total_endorsements": len([r for r in rows if (r.get("Endorsement Status") or "").upper() == "ACCEPTED"]),
        "matched_to_baseline": len(matched),
        "unmatched": len(unmatched),
        "recent_90d_matched": recent_matched,
        "top_skills": [{"skill": s, "count": c} for s, c in top_skills],
        "proposed_mutations": proposed_mutations,
    }


# ---------------------------------------------------------------------------
# Recommendations processor
# ---------------------------------------------------------------------------

def process_recommendations(
    csv_text: str,
    contacts: list[dict],
) -> dict:
    """Process Recommendations_Received.csv.

    Highest-quality relationship evidence in the export.
    Matches recommenders against baseline. Recommendation text = strong signal.
    """
    rows = list(csv.DictReader(io.StringIO(csv_text)))
    _, name_idx = _build_indexes(contacts)

    matched: list[dict] = []
    unmatched: list[dict] = []

    for row in rows:
        first = (row.get("First Name") or "").strip()
        last = (row.get("Last Name") or "").strip()
        name = f"{first} {last}".strip()
        company = (row.get("Company") or "").strip()
        title = (row.get("Job Title") or "").strip()
        text = (row.get("Text") or "").strip()
        created = _parse_li_date(row.get("Creation Date") or "")

        match = _match_by_name(name, name_idx)
        entry = {
            "name": name,
            "company": company,
            "title": title,
            "text_preview": text[:300] if text else None,
            "created_on": created,
            "baseline_id": match.get("id") if match else None,
            "signal_class": match.get("signal_class") if match else None,
            "rc_tier": match.get("rc_tier") if match else None,
        }
        if match:
            matched.append(entry)
        else:
            unmatched.append(entry)

    return {
        "total_recommendations": len(rows),
        "matched_to_baseline": len(matched),
        "unmatched": len(unmatched),
        "recommendations": matched + unmatched,
    }


# ---------------------------------------------------------------------------
# Main extended ingest
# ---------------------------------------------------------------------------

def ingest_extended(
    company_follows_csv: str | None = None,
    invitations_csv: str | None = None,
    endorsements_csv: str | None = None,
    recommendations_csv: str | None = None,
    source_filename: str = "linkedin_export",
    dry_run: bool = False,
) -> dict:
    """Run all available supplemental LinkedIn ingest sources.

    Each source is optional. Pass only the CSVs you have available.
    Returns a unified intelligence result with trust stats and proposed mutations.
    """
    contacts = _load_baseline()
    slug_idx, name_idx = _build_indexes(contacts)

    # Load active threads if yaml available
    active_threads: list[dict] = []
    try:
        import yaml as _yaml  # type: ignore
        at_path = core.SYSTEM_DIR / "active_threads.yaml"
        if at_path.exists():
            raw = _yaml.safe_load(at_path.read_text())
            active_threads = (raw.get("threads") if isinstance(raw, dict) else raw) or []
    except Exception:
        pass

    results: dict[str, Any] = {
        "source_filename": source_filename,
        "ingest_date": date.today().isoformat(),
        "dry_run": dry_run,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sources_processed": [],
        "company_follows": None,
        "invitations": None,
        "endorsements": None,
        "recommendations": None,
        "all_proposed_mutations": [],
    }

    if company_follows_csv:
        results["company_follows"] = process_company_follows(
            company_follows_csv, contacts, active_threads
        )
        results["sources_processed"].append("company_follows")

    if invitations_csv:
        inv = process_invitations(invitations_csv, contacts)
        results["invitations"] = inv
        results["sources_processed"].append("invitations")
        results["all_proposed_mutations"].extend(inv.get("proposed_mutations") or [])

    if endorsements_csv:
        end = process_endorsements(endorsements_csv, contacts)
        results["endorsements"] = end
        results["sources_processed"].append("endorsements")
        results["all_proposed_mutations"].extend(end.get("proposed_mutations") or [])

    if recommendations_csv:
        rec = process_recommendations(recommendations_csv, contacts)
        results["recommendations"] = rec
        results["sources_processed"].append("recommendations")

    # Trust stats
    total_rows = sum([
        (results["company_follows"] or {}).get("total_follows", 0),
        (results["invitations"] or {}).get("total_invitations", 0),
        (results["endorsements"] or {}).get("total_endorsements", 0),
        (results["recommendations"] or {}).get("total_recommendations", 0),
    ])
    total_matched = sum([
        len((results["company_follows"] or {}).get("thread_matches", [])) +
        len((results["company_follows"] or {}).get("baseline_matches", [])),
        (results["invitations"] or {}).get("sent_matched_to_baseline", 0) +
        (results["invitations"] or {}).get("received_matched_to_baseline", 0),
        (results["endorsements"] or {}).get("matched_to_baseline", 0),
        (results["recommendations"] or {}).get("matched_to_baseline", 0),
    ])
    results["trust_stats"] = {
        "sources_assessed": len(results["sources_processed"]),
        "total_rows_processed": total_rows,
        "total_matched_to_baseline": total_matched,
        "total_proposed_mutations": len(results["all_proposed_mutations"]),
        "confidence": "high" if len(results["sources_processed"]) >= 3 else "medium",
        "trust_contract_met": len(results["sources_processed"]) > 0,
        "source_filename": source_filename,
    }

    # Persist cache
    if not dry_run:
        EXTENDED_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        EXTENDED_CACHE_PATH.write_text(
            json.dumps(results, indent=2, default=str) + "\n"
        )

    return results


def ingest_extended_from_zip(path: str | Path, *, dry_run: bool = False) -> dict:
    """Extract and process all supplemental sources from a LinkedIn ZIP."""
    p = Path(path)
    with zipfile.ZipFile(p) as zf:
        names_lower = {Path(n).name.lower(): n for n in zf.namelist() if not n.endswith("/")}

        def _read(filename_lower: str) -> str | None:
            actual = names_lower.get(filename_lower)
            if not actual:
                # Try partial match (e.g. comments have a suffix number)
                for k, v in names_lower.items():
                    if k.startswith(filename_lower.split(".")[0]):
                        actual = v
                        break
            if not actual:
                return None
            return zf.read(actual).decode("utf-8-sig", errors="replace")

        return ingest_extended(
            company_follows_csv=_read("company follows.csv"),
            invitations_csv=_read("invitations.csv"),
            endorsements_csv=_read("endorsement_received_info.csv"),
            recommendations_csv=_read("recommendations_received.csv"),
            source_filename=p.name,
            dry_run=dry_run,
        )


# ---------------------------------------------------------------------------
# CLI printer
# ---------------------------------------------------------------------------

def _print_results(r: dict) -> None:
    print(f"\n=== LinkedIn Extended Ingest — {r.get('ingest_date')} ===")
    print(f"Sources processed: {', '.join(r.get('sources_processed') or [])}")
    ts = r.get("trust_stats") or {}
    print(f"Trust stats: {ts.get('total_rows_processed')} rows · "
          f"{ts.get('total_matched_to_baseline')} matched · "
          f"{ts.get('total_proposed_mutations')} mutations proposed · "
          f"confidence={ts.get('confidence')}")

    cf = r.get("company_follows")
    if cf:
        print(f"\nCompany Follows ({cf['total_follows']} total):")
        print(f"  Thread matches: {len(cf['thread_matches'])} — "
              + ", ".join(m['company'] for m in cf['thread_matches'][:5]))
        print(f"  Baseline matches: {len(cf['baseline_matches'])}")
        print(f"  Recent follows (90d): {len(cf['recent_follows_90d'])}")
        if cf.get("new_opportunity_signals"):
            print(f"  New opportunity signals: {', '.join(cf['new_opportunity_signals'][:5])}")

    inv = r.get("invitations")
    if inv:
        print(f"\nInvitations ({inv['total_invitations']} total):")
        print(f"  Sent: {inv['sent_total']} ({inv['sent_matched_to_baseline']} in baseline, "
              f"{inv['sent_unmatched']} new contacts)")
        print(f"  Received: {inv['received_total']} ({inv['received_matched_to_baseline']} in baseline, "
              f"{inv['received_unmatched']} new leads)")
        if inv.get("new_inbound_leads_60d"):
            print(f"  New inbound leads (60d): "
                  + ", ".join(l['name'] for l in inv['new_inbound_leads_60d'][:5]))

    end = r.get("endorsements")
    if end:
        print(f"\nEndorsements ({end['total_endorsements']} total):")
        print(f"  Matched to baseline: {end['matched_to_baseline']}")
        print(f"  Recent (90d) from known contacts: {len(end['recent_90d_matched'])}")
        if end.get("top_skills"):
            top = ", ".join(f"{s['skill']} ({s['count']})" for s in end['top_skills'][:5])
            print(f"  Top skills: {top}")

    rec = r.get("recommendations")
    if rec:
        print(f"\nRecommendations ({rec['total_recommendations']} total):")
        print(f"  Matched to baseline: {rec['matched_to_baseline']}")
        for rr in rec.get("recommendations", [])[:3]:
            print(f"  - {rr['name']} ({rr.get('company')}) — "
                  f"{(rr.get('text_preview') or '')[:80]}…")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--zip", metavar="PATH", help="Process directly from a LinkedIn export ZIP.")
    g.add_argument("--company-follows", metavar="TEXT",
                   help="Raw text of Company Follows.csv (for API/GPT flow).")
    p.add_argument("--invitations", metavar="TEXT")
    p.add_argument("--endorsements", metavar="TEXT")
    p.add_argument("--recommendations", metavar="TEXT")
    p.add_argument("--source-filename", default="linkedin_export")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    if args.zip:
        result = ingest_extended_from_zip(args.zip, dry_run=args.dry_run)
    else:
        result = ingest_extended(
            company_follows_csv=args.company_follows,
            invitations_csv=args.invitations,
            endorsements_csv=args.endorsements,
            recommendations_csv=args.recommendations,
            source_filename=args.source_filename,
            dry_run=args.dry_run,
        )

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        _print_results(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
