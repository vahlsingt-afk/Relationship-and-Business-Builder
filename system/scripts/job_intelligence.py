#!/usr/bin/env python3
"""
job_intelligence.py — job opportunity detection from email and LinkedIn messages.

Gated entirely by opportunity_context.yaml → job_search_active.
When job_search_active is false: returns an empty result immediately.
When job_search_active is true: scans email and LinkedIn message threads for
job signal patterns, scores each role against the user profile, and returns
a structured report with role title, company, fit score, links, and source.

Design principles:
- Never surface job signals when job_search_active is false.
- Score against the user's actual capabilities and target titles/companies.
- Extract posting URLs when present; flag as speculative when not.
- Never fabricate role details — only what is in the source thread.
- Source citations always included so the user can navigate directly.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import rb_core as core  # noqa: E402

CACHE_PATH = core.SYSTEM_DIR / ".cache" / "job_intelligence.json"
OPPORTUNITY_CONTEXT_PATH = core.SYSTEM_DIR / "profiles" / "todd_vahlsing" / "opportunity_context.yaml"

# ── Job signal detection patterns ─────────────────────────────────────────────

# Patterns that indicate a message is about a job opportunity
JOB_SIGNAL_PATTERNS = [
    re.compile(r"\b(job opportunity|career opportunity|exciting opportunity|new opportunity)\b", re.I),
    re.compile(r"\b(we('re| are) hiring|we have an opening|open position|open role|job opening)\b", re.I),
    re.compile(r"\b(recruiter|talent acquisition|head of talent|people ops|hr team)\b", re.I),
    re.compile(r"\b(your background|your experience|your profile|came across your|noticed your background)\b", re.I),
    re.compile(r"\b(role at|position at|opportunity at|join us at|join our team)\b", re.I),
    re.compile(r"\b(chief revenue|chief sales|vp sales|vp enterprise|head of sales|head of revenue)\b", re.I),
    re.compile(r"\b(director of sales|svp sales|managing director|general manager|president)\b", re.I),
    re.compile(r"\b(apply|job description|jd|job req|req[#\s]\d|compensation|ote|base salary|equity)\b", re.I),
    re.compile(r"\b(know anyone|referral|warm introduction|reach out about|exploring.*role)\b", re.I),
    re.compile(r"(linkedin\.com/jobs|greenhouse\.io|lever\.co|ashbyhq\.com|workday|icims|jobvite)", re.I),
    re.compile(r"\b(confidential.*search|executive search|search firm|retained search|contingency search)\b", re.I),
]

# Patterns that indicate this is NOT a job signal (noise filters)
NOISE_PATTERNS = [
    re.compile(r"\b(job done|great job|good job|nice job|job well done)\b", re.I),
    re.compile(r"\b(job number|job order|job id|work order)\b", re.I),
    re.compile(r"\b(hiring.*client|helping.*client.*hire|sourcing.*for.*client)\b", re.I),  # recruiter fishing
]

# URL extraction pattern
URL_PATTERN = re.compile(
    r"https?://[^\s\"'>]+(?:job|career|position|opening|req|greenhouse|lever|ashby|workday|icims)[^\s\"'>]*",
    re.I,
)

GENERIC_URL_PATTERN = re.compile(r"https?://[^\s\"'>]{10,}", re.I)

# Title extraction — look for these patterns near the signal
TITLE_PATTERNS = [
    re.compile(r"\b(Chief\s+(?:Revenue|Sales|Strategy|Commercial|Growth)\s+Officer)\b", re.I),
    re.compile(r"\b((?:SVP|VP|Head of|Director of|Managing Director of|GM of|General Manager of)\s+(?:Enterprise\s+)?(?:Sales|Revenue|Business Development|Customer Success|Accounts|Growth|Commercial))\b", re.I),
    re.compile(r"\b((?:VP|Head of|Director of)\s+(?:Restaurant|Foodservice|Hospitality)\s+(?:Sales|Revenue|Partnerships|Success))\b", re.I),
    re.compile(r"\b(President(?:\s+(?:of|and))?(?:\s+(?:Sales|Revenue|North America|US|Commercial))?)\b", re.I),
    re.compile(r"\b((?:Interim|Fractional)\s+(?:CSO|CRO|VP\s+Sales|Chief\s+Revenue\s+Officer))\b", re.I),
]

COMPANY_PATTERNS = [
    re.compile(r"\bat\s+([A-Z][A-Za-z0-9\s&\-\.]{2,40}?)(?:\s*[,.\|]|\s+(?:is|we|they|the|our|this|that))", re.I),
    re.compile(r"\bwith\s+([A-Z][A-Za-z0-9\s&\-\.]{2,40}?)(?:\s*[,.\|]|\s+(?:is|we|they|the|our|this|that))", re.I),
    re.compile(r"\bjoin\s+([A-Z][A-Za-z0-9\s&\-\.]{2,40}?)(?:\s*[,.\|!])", re.I),
]


# ── Profile loading ────────────────────────────────────────────────────────────

def _load_opportunity_context() -> dict:
    """Load opportunity_context.yaml. Returns {} on any failure."""
    try:
        import yaml  # type: ignore
        return yaml.safe_load(OPPORTUNITY_CONTEXT_PATH.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        try:
            return json.loads(OPPORTUNITY_CONTEXT_PATH.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}


def _is_job_search_active(ctx: dict) -> bool:
    return bool(ctx.get("job_search_active"))


def _job_search_context(ctx: dict) -> dict:
    jsc = ctx.get("job_search_context") or {}
    return jsc if isinstance(jsc, dict) else {}


# ── Signal detection ──────────────────────────────────────────────────────────

def _is_job_signal(text: str) -> bool:
    """Return True if text contains job opportunity signals."""
    text_lower = text.lower()
    # Quick noise filter
    for pat in NOISE_PATTERNS:
        if pat.search(text):
            return False
    # Must match at least one job signal pattern
    for pat in JOB_SIGNAL_PATTERNS:
        if pat.search(text):
            return True
    return False


def _extract_titles(text: str) -> list[str]:
    """Extract role title mentions from text."""
    titles = []
    for pat in TITLE_PATTERNS:
        for m in pat.finditer(text):
            t = m.group(0).strip()
            if t and t not in titles:
                titles.append(t)
    return titles[:3]


def _extract_companies(text: str, target_companies: list[str]) -> list[str]:
    """Extract company names — target companies first, then pattern extraction."""
    found = []
    text_lower = text.lower()
    for tc in target_companies:
        if tc.lower() in text_lower and tc not in found:
            found.append(tc)
    if not found:
        for pat in COMPANY_PATTERNS:
            for m in pat.finditer(text):
                name = m.group(1).strip().rstrip(".,!;:")
                if len(name) >= 3 and name not in found:
                    found.append(name)
    return found[:3]


def _extract_urls(text: str) -> list[str]:
    """Extract job-related URLs from text. Falls back to any URL."""
    urls = URL_PATTERN.findall(text)
    if not urls:
        urls = GENERIC_URL_PATTERN.findall(text)
    # Deduplicate, cap at 3
    seen: set[str] = set()
    result = []
    for u in urls:
        u_clean = u.rstrip(".,)>\"'")
        if u_clean not in seen:
            seen.add(u_clean)
            result.append(u_clean)
    return result[:3]


# ── Fit scoring ───────────────────────────────────────────────────────────────

def _score_fit(
    titles: list[str],
    companies: list[str],
    text: str,
    ctx: dict,
    jsc: dict,
) -> tuple[int, list[str]]:
    """Score role fit against opportunity_context. Returns (score 0–100, reasons)."""
    score = 0
    reasons: list[str] = []
    text_lower = text.lower()

    target_titles = [t.lower() for t in (jsc.get("target_titles") or [])]
    target_companies = [c.lower() for c in (jsc.get("target_companies") or [])]
    capabilities = ctx.get("capabilities") or []

    # Title match (+30 if any extracted title matches target list)
    for t in titles:
        if any(tt in t.lower() for tt in target_titles):
            score += 30
            reasons.append(f"Title match: {t}")
            break
    # Partial title keyword match (+15)
    if score < 30:
        title_keywords = ["chief", "vp ", "head of", "svp", "director", "managing director", "president"]
        for kw in title_keywords:
            if kw in text_lower:
                score += 15
                reasons.append(f"Partial title signal: {kw}")
                break

    # Company match (+25 if target company)
    for c in companies:
        if any(tc in c.lower() for tc in target_companies):
            score += 25
            reasons.append(f"Target company: {c}")
            break
    # Industry match (+10 if restaurant/foodservice mentioned)
    if any(kw in text_lower for kw in ["restaurant", "foodservice", "qsr", "franchise", "hospitality"]):
        score += 10
        reasons.append("Restaurant/foodservice industry mentioned")

    # Capability alignment (+10 if role aligns with core capabilities)
    cap_keywords = {
        "enterprise_restaurant_tech_sales": ["enterprise", "restaurant tech", "saas", "platform"],
        "gtm_strategy_and_execution": ["gtm", "go to market", "strategy", "commercial"],
        "interim_cso_and_sales_leadership": ["interim", "fractional", "cso", "chief sales"],
        "mcdonalds_global_account_management": ["mcdonald", "global account", "strategic account"],
    }
    for cap in capabilities[:5]:
        for kw in (cap_keywords.get(cap) or []):
            if kw in text_lower:
                score += 10
                reasons.append(f"Capability alignment: {cap}")
                break

    # Has a job posting URL (+5)
    if URL_PATTERN.search(text):
        score += 5
        reasons.append("Job posting URL detected")

    # Direct outreach to user (+10 — personalized, not mass blast)
    if any(kw in text_lower for kw in ["your background", "your experience", "your profile", "came across your"]):
        score += 10
        reasons.append("Personalized outreach to user profile")

    return min(score, 100), reasons


# ── Source scanners ───────────────────────────────────────────────────────────

def _scan_email_threads(
    cutoff: datetime,
    target_companies: list[str],
    ctx: dict,
    jsc: dict,
) -> list[dict]:
    """Scan email inbox files for job signals."""
    inbox_dir = core.SYSTEM_DIR / "inbox"
    email_files = list(inbox_dir.glob("email.*.json")) + list(inbox_dir.glob("email.personal.json")) + list(inbox_dir.glob("email.bridgepoint.json"))
    # Deduplicate
    email_files = list({f.resolve(): f for f in email_files}.values())

    signals: list[dict] = []

    for fpath in email_files:
        try:
            raw = json.loads(fpath.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        threads = raw if isinstance(raw, list) else (raw.get("threads") or raw.get("messages") or [])
        for thread in threads:
            if not isinstance(thread, dict):
                continue
            # Date filter
            ts_str = thread.get("last_message_at") or thread.get("date") or ""
            try:
                ts = datetime.fromisoformat(str(ts_str).replace("Z", "+00:00"))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if ts < cutoff:
                    continue
            except (ValueError, TypeError):
                pass

            subject = str(thread.get("subject") or "")
            snippet = str(thread.get("snippet") or "")
            text = f"{subject} {snippet}"

            if not _is_job_signal(text):
                continue

            titles = _extract_titles(text)
            companies = _extract_companies(text, target_companies)
            urls = _extract_urls(text)
            score, reasons = _score_fit(titles, companies, text, ctx, jsc)

            if score < (jsc.get("minimum_fit_score") or 60):
                continue

            sender = thread.get("last_message_from") or {}
            sender_name = sender.get("name") or sender.get("email") or "unknown"
            sender_email = sender.get("email") or ""

            signals.append({
                "source": "email",
                "source_file": fpath.name,
                "thread_id": thread.get("thread_id") or "",
                "detected_at": ts_str,
                "subject": subject[:120],
                "sender_name": sender_name,
                "sender_email": sender_email,
                "extracted_titles": titles,
                "extracted_companies": companies,
                "posting_urls": urls,
                "fit_score": score,
                "fit_reasons": reasons,
                "is_speculative": not bool(urls),
                "signal_type": "email_job_signal",
                "snippet": snippet[:200],
            })

    return signals


def _scan_linkedin_messages(
    cutoff: datetime,
    target_companies: list[str],
    ctx: dict,
    jsc: dict,
) -> list[dict]:
    """Scan LinkedIn message threads for job signals."""
    msg_path = core.SYSTEM_DIR / "inbox" / "linkedin.messages.json"
    if not msg_path.exists():
        return []

    try:
        raw = json.loads(msg_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []

    messages = raw if isinstance(raw, list) else (raw.get("messages") or raw.get("threads") or [])
    signals: list[dict] = []

    for msg in messages:
        if not isinstance(msg, dict):
            continue
        # Skip sent messages — we want inbound job signals
        direction = msg.get("direction") or msg.get("folder") or ""
        if str(direction).lower() in ("sent", "outbox"):
            continue

        # Date filter
        ts_str = msg.get("date") or msg.get("last_message_at") or ""
        try:
            ts = datetime.fromisoformat(str(ts_str).replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if ts < cutoff:
                continue
        except (ValueError, TypeError):
            pass

        content = str(msg.get("content") or "")
        subject = str(msg.get("subject") or msg.get("conversation_title") or "")
        text = f"{subject} {content}"

        if not _is_job_signal(text):
            continue

        titles = _extract_titles(text)
        companies = _extract_companies(text, target_companies)
        urls = _extract_urls(text)
        score, reasons = _score_fit(titles, companies, text, ctx, jsc)

        if score < (jsc.get("minimum_fit_score") or 60):
            continue

        sender_from = msg.get("from") or ""

        signals.append({
            "source": "linkedin",
            "source_file": "linkedin.messages.json",
            "conversation_id": msg.get("conversation_id") or "",
            "detected_at": ts_str,
            "subject": subject[:120],
            "sender_name": str(sender_from)[:80],
            "extracted_titles": titles,
            "extracted_companies": companies,
            "posting_urls": urls,
            "fit_score": score,
            "fit_reasons": reasons,
            "is_speculative": not bool(urls),
            "signal_type": "linkedin_job_signal",
            "snippet": content[:200],
        })

    return signals


# ── Deduplication ─────────────────────────────────────────────────────────────

def _dedup_signals(signals: list[dict]) -> list[dict]:
    """Deduplicate by (company, title) pair. Keep highest fit_score."""
    seen: dict[str, dict] = {}
    for sig in signals:
        companies = sig.get("extracted_companies") or ["unknown"]
        titles = sig.get("extracted_titles") or ["role"]
        key = f"{companies[0].lower()}|{titles[0].lower() if titles else 'role'}"
        if key not in seen or sig["fit_score"] > seen[key]["fit_score"]:
            seen[key] = sig
    return sorted(seen.values(), key=lambda s: s["fit_score"], reverse=True)


# ── Main build ────────────────────────────────────────────────────────────────

def build_report(*, now: Optional[datetime] = None) -> dict:
    """Build the job intelligence report.

    Returns a minimal dict with gate_status when job_search_active is false.
    Returns full signal report when active.
    """
    ctx = _load_opportunity_context()

    if not _is_job_search_active(ctx):
        return {
            "gate_status": "inactive",
            "job_search_active": False,
            "message": "Job intelligence scanning is disabled. Set job_search_active: true in opportunity_context.yaml to enable.",
            "signals": [],
            "signal_count": 0,
            "generated_at": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
        }

    jsc = _job_search_context(ctx)
    scan_days = int(jsc.get("scan_days") or 14)
    scan_sources = str(jsc.get("scan_sources") or "both").lower()
    target_companies = [str(c) for c in (jsc.get("target_companies") or [])]

    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=scan_days)

    all_signals: list[dict] = []

    if scan_sources in ("email", "both"):
        try:
            email_signals = _scan_email_threads(cutoff, target_companies, ctx, jsc)
            all_signals.extend(email_signals)
        except Exception as exc:  # noqa: BLE001
            all_signals.append({"error": f"email scan failed: {exc}", "source": "email"})

    if scan_sources in ("linkedin", "both"):
        try:
            li_signals = _scan_linkedin_messages(cutoff, target_companies, ctx, jsc)
            all_signals.extend(li_signals)
        except Exception as exc:  # noqa: BLE001
            all_signals.append({"error": f"linkedin scan failed: {exc}", "source": "linkedin"})

    # Filter out error entries before dedup
    error_entries = [s for s in all_signals if "error" in s]
    clean_signals = [s for s in all_signals if "error" not in s]
    deduped = _dedup_signals(clean_signals)

    return {
        "gate_status": "active",
        "job_search_active": True,
        "generated_at": now.isoformat(timespec="seconds"),
        "scan_days": scan_days,
        "scan_sources": scan_sources,
        "cutoff": cutoff.isoformat(timespec="seconds"),
        "signal_count": len(deduped),
        "signals": deduped[:20],
        "scan_errors": error_entries,
        "profile_id": ctx.get("profile_id") or "unknown",
        "minimum_fit_score": jsc.get("minimum_fit_score") or 60,
        "target_companies_count": len(target_companies),
    }


def write_cache(report: dict) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")


def load_cache() -> dict:
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def main() -> int:
    import argparse
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--json", action="store_true", help="Emit JSON output")
    p.add_argument("--write-cache", action="store_true", help="Write report to cache")
    p.add_argument("--force", action="store_true", help="Run even if job_search_active is false (for testing)")
    args = p.parse_args()

    report = build_report()

    if args.force and not report.get("job_search_active"):
        print("NOTE: job_search_active is false. Use --force to override for testing.")

    if args.write_cache:
        write_cache(report)
        print(f"Cache written to {CACHE_PATH}")

    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        status = report.get("gate_status", "unknown")
        if status == "inactive":
            print(f"Job intelligence: INACTIVE — {report.get('message')}")
        else:
            print(f"Job intelligence: ACTIVE")
            print(f"  Scan window: last {report.get('scan_days')}d | Sources: {report.get('scan_sources')}")
            print(f"  Signals found: {report.get('signal_count')}")
            for sig in (report.get("signals") or [])[:5]:
                companies = ", ".join(sig.get("extracted_companies") or ["unknown"])
                titles = ", ".join(sig.get("extracted_titles") or ["role"])
                score = sig.get("fit_score", 0)
                urls = sig.get("posting_urls") or []
                print(f"  [{score}%] {companies} — {titles} (source: {sig.get('source')}) {'[URL]' if urls else '[speculative]'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
