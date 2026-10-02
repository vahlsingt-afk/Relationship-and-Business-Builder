#!/usr/bin/env python3
"""
contact_index.py — Contact Intelligence Index (Sprint D / RB 9.45)

Builds a queryable contact-company index from three sources:
  1. system/loop_ledger.md     — person + company from open/closed loops
  2. system/active_threads.yaml — company + contact slug per active thread
  3. system/inbox/email.*.json  — email thread participants + company from domain

Output: system/.cache/contact_index.json

Schema per contact:
  {
    "id": "mike-schwartz",        # slug — lowercase, hyphenated
    "name": "Mike Schwartz",
    "company": "Global Payments",  # primary company
    "company_aliases": ["Genius", "Xenial", "Global Payments Inc."],
    "email_domain": "genius.com",  # from email participants if known
    "thread_ids": ["T-genius-gp"],  # active_threads that reference this contact
    "open_loops": ["L-2026-05-26-001"],
    "all_loops": ["L-2026-05-26-001", "L-2026-05-08-007"],
    "last_loop_date": "2026-05-26",
    "loop_context": "Move Genius / Global Payments role conversation forward",
    "sources": ["loop_ledger", "active_threads"],
    "importance": "high | medium | low",  # derived from loop count + recency
  }

Usage:
  python3 system/scripts/contact_index.py          # rebuild and write
  python3 system/scripts/contact_index.py --lookup "Toast"  # query by company
  python3 system/scripts/contact_index.py --status          # summary stats

Importable API:
  from contact_index import ContactIndex
  ci = ContactIndex.load()           # load from cache
  ci.by_company("McDonald's")        # → list[dict] contacts at company
  ci.by_name("Mike Schwartz")        # → dict | None
  ci.related_to_companies(["Toast", "PAR"])  # → list[dict] across all
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rb_core as core

LOOP_LEDGER_PATH = core.SYSTEM_DIR / "loop_ledger.md"
ACTIVE_THREADS_PATH = core.SYSTEM_DIR / "active_threads.yaml"
INBOX_DIR = core.SYSTEM_DIR / "inbox"
OUTPUT_PATH = core.CACHE_DIR / "contact_index.json"

# Todd's own email addresses — exclude from participant extraction
TODD_EMAILS = {
    "todd@bridgepointops.com",
    "toddvahlsing@gmail.com",
    "todd.vahlsing@bridgepointops.com",
    "todd.vahlsing@gmail.com",
}
TODD_DOMAINS = {"bridgepointops.com"}

# Email domains that are NOT companies (newsletter services, personal, etc.)
NOISE_DOMAINS = {
    "gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "icloud.com",
    "me.com", "aol.com", "protonmail.com", "pm.me",
    # Newsletter and marketing services
    "divenewsletter.com", "e.myfilingservices.com", "email.informeddelivery.usps.com",
    "email.marcustheatres.com", "email.menards.com", "exchange.spectrum.com",
    "fathom.video", "daily.therundown.ai", "backerviews.net",
    # Internal / misc
    "colemanrg.com",
}

# Known company→domain mappings for enrichment
COMPANY_DOMAIN_MAP = {
    "foodsconnected.com": "Foods Connected",
    "toast.com": "Toast",
    "par.com": "PAR Technology",
    "partech.com": "PAR Technology",
    "olo.com": "Olo",
    "ncrvoyix.com": "NCR Voyix",
    "globalpayments.com": "Global Payments",
    "qu.com": "Qu",
    "eragroup.com": "ERA Group",
    "maho.ai": "Maho.ai",
    "benchmarksixty.com": "BenchmarkSixty",
    "longfisolutions.com": "LongFi Solutions",
    "matrixsoftware.com": "Matrix Software Solutions",
    "voosh.ai": "Voosh",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _slug(name: str) -> str:
    """Normalize a person name to a stable slug: 'Mike Schwartz' → 'mike-schwartz'."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower().strip()).strip("-")


def _clean_company(raw: str) -> str:
    """Strip parentheses, leading/trailing punctuation from a company name."""
    return raw.strip("()[]").strip()


def _importance(loop_count: int, last_loop_date: str | None) -> str:
    """Derive importance from loop frequency and recency."""
    if loop_count >= 3:
        return "high"
    if loop_count == 2:
        return "medium"
    # Single loop: recency determines importance
    if last_loop_date:
        try:
            delta = (date.today() - date.fromisoformat(last_loop_date[:10])).days
            if delta <= 30:
                return "medium"
        except ValueError:
            pass
    return "low"


# ---------------------------------------------------------------------------
# Source 1: loop_ledger.md parser
# ---------------------------------------------------------------------------

# Patterns for the Person/Company column
_PERSON_COMPANY_RE = re.compile(
    r"^([A-Z][a-zA-Z\-']+(?:\s+[A-Z][a-zA-Z\-']+)*)"  # First name + optional last name(s)
    r"(?:\s*[/(]\s*([^)]+?)\s*\)?)?"                     # optional (Company) or /Company
    r"$"
)

def _parse_person_company(raw: str) -> list[tuple[str, str]]:
    """Parse the Person/Company column of loop_ledger.md.

    Returns a list of (name, company) tuples.  Most rows have one person;
    intro loops (A → B) and multi-person rows have multiple.
    """
    results: list[tuple[str, str]] = []

    # Handle intro loops: "Noelle Labrie → Cristina Gia Luciano"
    if "→" in raw:
        parts = [p.strip() for p in raw.split("→")]
        for part in parts:
            if part and not any(skip in part.lower() for skip in ("lexi", "other")):
                sub = _parse_person_company(part)
                results.extend(sub)
        return results

    # Handle comma-separated multi-person: "Dave Miller (Franke), Bob Gibson, ..."
    # Only split on commas if there are multiple capital-name segments
    comma_parts = [p.strip() for p in raw.split(",")]
    if len(comma_parts) > 1 and all(
        re.match(r"[A-Z]", p.strip()) for p in comma_parts if p.strip()
    ):
        for part in comma_parts:
            part = part.strip()
            if part and "ecosystem" not in part.lower():
                sub = _parse_person_company(part)
                results.extend(sub)
        return results

    # Skip non-person entries
    SKIP_PATTERNS = ("cRM", "background-check", "internal:", "hospitality table chapter")
    if any(s.lower() in raw.lower() for s in SKIP_PATTERNS):
        return []
    if raw.lower().startswith(("crm", "internal", "background")):
        return []

    # "First Last (Company)" pattern
    m = re.match(r"^([A-Z][a-zA-ZÀ-ÿ'\-]+(?:\s+[A-Z][a-zA-ZÀ-ÿ'\-]+)*)\s+\(([^)]+)\)\s*$", raw)
    if m:
        name = m.group(1).strip()
        company = _clean_company(m.group(2))
        return [(name, company)]

    # "First Last / Company" pattern
    m2 = re.match(r"^([A-Z][a-zA-ZÀ-ÿ'\-]+(?:\s+[A-Z][a-zA-ZÀ-ÿ'\-]+)*)\s*/\s*(.+)$", raw)
    if m2:
        name = m2.group(1).strip()
        company = m2.group(2).strip()
        # Verify company doesn't look like a person name
        if not re.match(r"^[A-Z][a-z]+ [A-Z][a-z]+$", company):
            return [(name, company)]

    # Plain name
    m3 = re.match(r"^([A-Z][a-zA-ZÀ-ÿ'\-]+(?:\s+[A-Z][a-zA-ZÀ-ÿ'\-]+)*)$", raw.strip())
    if m3:
        name = m3.group(1).strip()
        # Skip single-word entries that look like company names
        if " " in name or len(name) > 3:
            return [(name, "")]
        return []

    return []


def _parse_loop_ledger() -> list[dict]:
    """Parse loop_ledger.md and return a list of loop dicts."""
    if not LOOP_LEDGER_PATH.exists():
        return []

    loops: list[dict] = []
    text = LOOP_LEDGER_PATH.read_text(encoding="utf-8")

    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("|") or line.startswith("| ID") or line.startswith("|---"):
            continue
        cells = [c.strip() for c in line.split("|") if c.strip()]
        if len(cells) < 4:
            continue

        loop_id = cells[0]
        opened = cells[1]
        person_col = cells[2]
        loop_desc = cells[3]
        closure_target = cells[4] if len(cells) > 4 else ""
        status_raw = cells[5] if len(cells) > 5 else "open"

        is_open = not status_raw.startswith("**closed") and not status_raw.startswith("**abandoned")

        people = _parse_person_company(person_col)
        for name, company in people:
            loops.append({
                "loop_id": loop_id,
                "name": name,
                "company": company,
                "opened": opened,
                "loop_desc": loop_desc[:200],
                "is_open": is_open,
            })

    return loops


# ---------------------------------------------------------------------------
# Source 2: active_threads.yaml parser
# ---------------------------------------------------------------------------

def _parse_active_threads() -> list[dict]:
    """Parse active_threads.yaml and return per-thread contact+company records."""
    if not ACTIVE_THREADS_PATH.exists():
        return []
    try:
        import yaml as _yaml
        raw = _yaml.safe_load(ACTIVE_THREADS_PATH.read_text(encoding="utf-8")) or {}
        threads = raw.get("threads") if isinstance(raw, dict) else raw
        if not isinstance(threads, list):
            return []
    except Exception:
        return []

    records: list[dict] = []
    for t in threads:
        thread_id = t.get("id") or ""
        companies = t.get("companies") or []
        contacts = t.get("contacts") or []  # slugs like "mike-schwartz"
        title = t.get("title") or ""
        status = t.get("status") or "open"

        for contact_slug in contacts:
            # Derive name from slug: "mike-schwartz" → "Mike Schwartz"
            name = " ".join(w.capitalize() for w in contact_slug.split("-"))
            records.append({
                "contact_slug": contact_slug,
                "name": name,
                "companies": companies,
                "thread_id": thread_id,
                "thread_title": title,
                "thread_status": status,
            })

    return records


# ---------------------------------------------------------------------------
# Source 3: email thread participants
# ---------------------------------------------------------------------------

def _parse_email_participants() -> list[dict]:
    """Extract unique external email participants from inbox email files."""
    participants: list[dict] = []

    for fname in ["email.bridgepoint.json", "email.personal.json"]:
        path = INBOX_DIR / fname
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            threads = data.get("threads") or []
        except Exception:
            continue

        for thread in threads:
            sender = thread.get("last_message_from") or {}
            email_field = sender.get("email") or ""
            sender_name = sender.get("name") or ""

            # Extract email address from "Name email@domain.com" format
            email_match = re.search(r"[\w.+\-]+@[\w.\-]+", email_field)
            if not email_match:
                continue
            email_addr = email_match.group(0).lower()

            # Skip Todd's own addresses
            if email_addr in TODD_EMAILS:
                continue
            if any(d in email_addr for d in TODD_DOMAINS):
                continue

            domain = email_addr.split("@")[-1].lower()
            if domain in NOISE_DOMAINS:
                continue

            # Extract sender name from the email field if not provided separately
            if not sender_name:
                # Format: "First Last email@domain.com"
                name_part = email_field.replace(email_match.group(0), "").strip()
                if name_part and re.match(r"[A-Z]", name_part):
                    sender_name = name_part

            company = COMPANY_DOMAIN_MAP.get(domain, "")
            if not company:
                # Derive company from domain: "foods-connected.com" → "foods-connected"
                company = domain.split(".")[0].replace("-", " ").title()

            if sender_name and " " in sender_name.strip():
                participants.append({
                    "name": sender_name.strip(),
                    "email_domain": domain,
                    "company": company,
                    "source": "email",
                })

    return participants


# ---------------------------------------------------------------------------
# Index builder
# ---------------------------------------------------------------------------

def build_index() -> dict:
    """Build the full contact index from all three sources."""
    loops = _parse_loop_ledger()
    thread_records = _parse_active_threads()
    email_participants = _parse_email_participants()

    # Aggregate by person slug
    contacts: dict[str, dict] = {}

    # ── Source 1: loops ──────────────────────────────────────────────────────
    for loop in loops:
        name = loop["name"]
        sid = _slug(name)
        if not sid or len(sid) < 3:
            continue

        company_raw = loop["company"] or ""
        # Normalize company aliases from "Global Payments / Genius-Xenial"
        company_parts = re.split(r"\s*/\s*|\s+and\s+|\s+&\s+", company_raw)
        primary_company = company_parts[0].strip() if company_parts else ""
        aliases = [p.strip() for p in company_parts[1:] if p.strip()]

        if sid not in contacts:
            contacts[sid] = {
                "id": sid,
                "name": name,
                "company": primary_company,
                "company_aliases": aliases,
                "email_domain": "",
                "thread_ids": [],
                "open_loops": [],
                "all_loops": [],
                "last_loop_date": "",
                "loop_context": "",
                "sources": [],
            }

        c = contacts[sid]
        # Update company if we now have one and didn't before
        if primary_company and not c["company"]:
            c["company"] = primary_company
        # Add new aliases
        for alias in aliases:
            if alias and alias not in c["company_aliases"] and alias != c["company"]:
                c["company_aliases"].append(alias)

        if "loop_ledger" not in c["sources"]:
            c["sources"].append("loop_ledger")

        c["all_loops"].append(loop["loop_id"])
        if loop["is_open"]:
            c["open_loops"].append(loop["loop_id"])

        # Track most recent loop date
        opened = loop["opened"]
        if opened > c["last_loop_date"]:
            c["last_loop_date"] = opened
            c["loop_context"] = loop["loop_desc"]

    # ── Source 2: active_threads ─────────────────────────────────────────────
    for rec in thread_records:
        sid = rec["contact_slug"]
        name = rec["name"]

        if sid not in contacts:
            contacts[sid] = {
                "id": sid,
                "name": name,
                "company": rec["companies"][0] if rec["companies"] else "",
                "company_aliases": rec["companies"][1:] if len(rec["companies"]) > 1 else [],
                "email_domain": "",
                "thread_ids": [],
                "open_loops": [],
                "all_loops": [],
                "last_loop_date": "",
                "loop_context": "",
                "sources": [],
            }

        c = contacts[sid]
        if rec["thread_id"] and rec["thread_id"] not in c["thread_ids"]:
            c["thread_ids"].append(rec["thread_id"])
        for comp in rec["companies"]:
            if comp and comp not in c["company_aliases"] and comp != c["company"]:
                c["company_aliases"].append(comp)
        if not c["company"] and rec["companies"]:
            c["company"] = rec["companies"][0]
        if "active_threads" not in c["sources"]:
            c["sources"].append("active_threads")

    # ── Source 3: email participants ─────────────────────────────────────────
    seen_email_slugs: set[str] = set()
    for rec in email_participants:
        name = rec["name"]
        sid = _slug(name)
        if not sid or len(sid) < 5:
            continue
        if sid in seen_email_slugs:
            continue
        seen_email_slugs.add(sid)

        if sid in contacts:
            c = contacts[sid]
            if not c["email_domain"]:
                c["email_domain"] = rec["email_domain"]
            if not c["company"] and rec["company"]:
                c["company"] = rec["company"]
            if "email" not in c["sources"]:
                c["sources"].append("email")
        else:
            # Only add email-only contacts if they have a recognizable company domain
            if rec["email_domain"] not in NOISE_DOMAINS and "." in rec["email_domain"]:
                contacts[sid] = {
                    "id": sid,
                    "name": name,
                    "company": rec["company"],
                    "company_aliases": [],
                    "email_domain": rec["email_domain"],
                    "thread_ids": [],
                    "open_loops": [],
                    "all_loops": [],
                    "last_loop_date": "",
                    "loop_context": "",
                    "sources": ["email"],
                }

    # ── Compute importance + finalize ─────────────────────────────────────────
    contact_list = []
    for c in contacts.values():
        n_loops = len(c["all_loops"])
        c["importance"] = _importance(n_loops, c["last_loop_date"] or None)
        c["loop_count"] = n_loops
        c["open_loop_count"] = len(c["open_loops"])
        contact_list.append(c)

    # Sort: most loops first, then alphabetical
    contact_list.sort(key=lambda x: (-x["loop_count"], x["name"].lower()))

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "contact_count": len(contact_list),
        "sources_used": ["loop_ledger", "active_threads", "email"],
        "contacts": contact_list,
    }


def write_index() -> dict:
    """Build and write the contact index to cache. Returns the built index."""
    index = build_index()
    core.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")
    return index


# ---------------------------------------------------------------------------
# Public API — ContactIndex class
# ---------------------------------------------------------------------------

class ContactIndex:
    """Queryable wrapper around the contact index JSON."""

    def __init__(self, data: dict):
        self._data = data
        self._contacts: list[dict] = data.get("contacts") or []
        # Build lookup structures
        self._by_id: dict[str, dict] = {c["id"]: c for c in self._contacts}
        self._by_name: dict[str, dict] = {c["name"].lower(): c for c in self._contacts}
        # Company → contacts reverse index (normalized lowercase)
        self._by_company: dict[str, list[dict]] = {}
        for c in self._contacts:
            for comp in [c.get("company") or ""] + (c.get("company_aliases") or []):
                if comp:
                    key = comp.lower().strip()
                    self._by_company.setdefault(key, [])
                    if c not in self._by_company[key]:
                        self._by_company[key].append(c)

    @classmethod
    def load(cls) -> "ContactIndex":
        """Load from cache, building fresh if missing."""
        if OUTPUT_PATH.exists():
            try:
                data = json.loads(OUTPUT_PATH.read_text(encoding="utf-8"))
                return cls(data)
            except Exception:
                pass
        data = build_index()
        return cls(data)

    def by_company(self, company: str) -> list[dict]:
        """Return all contacts at a company (fuzzy: substring match on normalized name)."""
        key = company.lower().strip()
        # Exact match first
        if key in self._by_company:
            return self._by_company[key]
        # Substring match
        results = []
        seen_ids: set[str] = set()
        for comp_key, contacts in self._by_company.items():
            if key in comp_key or comp_key in key:
                for c in contacts:
                    if c["id"] not in seen_ids:
                        seen_ids.add(c["id"])
                        results.append(c)
        return sorted(results, key=lambda x: -x.get("loop_count", 0))

    def by_name(self, name: str) -> dict | None:
        """Return contact by name (case-insensitive)."""
        return self._by_name.get(name.lower().strip()) or self._by_id.get(_slug(name))

    def related_to_companies(self, companies: list[str]) -> list[dict]:
        """Return all contacts related to any of the given companies, deduped."""
        seen_ids: set[str] = set()
        results: list[dict] = []
        for company in companies:
            for contact in self.by_company(company):
                if contact["id"] not in seen_ids:
                    seen_ids.add(contact["id"])
                    results.append(contact)
        return sorted(results, key=lambda x: -x.get("loop_count", 0))

    def contacts_for_intel_item(self, item: dict) -> list[dict]:
        """Find contacts relevant to an intelligence item.

        Searches company mentions in title + summary + extras.entities.
        Returns contacts sorted by loop_count (most engaged first).
        """
        # Collect candidate company names from the item
        text = f"{item.get('title', '')} {item.get('summary', '')}".lower()
        extras = item.get("extras") or {}
        entity_list: list[str] = extras.get("entities") or extras.get("companies") or []
        if isinstance(entity_list, str):
            entity_list = [entity_list]

        # Also pull from extras for web scanner items
        source_name = extras.get("source_name") or ""
        entities_from_extras: list[str] = list(entity_list)

        seen_ids: set[str] = set()
        results: list[dict] = []

        # Try each known company in the index against the item text
        for comp_key, contacts in self._by_company.items():
            if len(comp_key) < 4:
                continue  # skip very short keys
            if comp_key in text or any(comp_key in e.lower() for e in entities_from_extras):
                for c in contacts:
                    if c["id"] not in seen_ids:
                        seen_ids.add(c["id"])
                        results.append(c)

        return sorted(results, key=lambda x: -x.get("loop_count", 0))[:5]

    @property
    def all_contacts(self) -> list[dict]:
        return self._contacts

    @property
    def open_loop_contacts(self) -> list[dict]:
        return [c for c in self._contacts if c.get("open_loop_count", 0) > 0]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--lookup", metavar="COMPANY", help="Look up contacts at a company")
    p.add_argument("--name", metavar="NAME", help="Look up a contact by name")
    p.add_argument("--status", action="store_true", help="Print index summary stats")
    p.add_argument("--rebuild", action="store_true", help="Force rebuild from sources")
    args = p.parse_args()

    if args.rebuild or args.status:
        print("Building contact index...")
        index = write_index()
        print(f"Written to {OUTPUT_PATH}")
        print(f"Contacts: {index['contact_count']}")
        contacts = index.get("contacts") or []
        high = sum(1 for c in contacts if c.get("importance") == "high")
        med = sum(1 for c in contacts if c.get("importance") == "medium")
        low = sum(1 for c in contacts if c.get("importance") == "low")
        open_loops = sum(c.get("open_loop_count", 0) for c in contacts)
        print(f"Importance: {high} high / {med} medium / {low} low")
        print(f"Open loops: {open_loops}")
        if args.status:
            print("\nTop contacts by loop count:")
            for c in contacts[:10]:
                print(f"  {c['name']:25s} {c.get('company',''):30s} loops={c['loop_count']} open={c['open_loop_count']}")
        return 0

    ci = ContactIndex.load()

    if args.lookup:
        results = ci.by_company(args.lookup)
        if not results:
            print(f"No contacts found for company: {args.lookup}")
            return 1
        print(f"Contacts at '{args.lookup}' ({len(results)}):")
        for c in results:
            loops_str = f"{c['open_loop_count']} open / {c['loop_count']} total loops"
            print(f"  {c['name']:25s} {c.get('company',''):30s} [{loops_str}]")
            if c.get("loop_context"):
                print(f"    Context: {c['loop_context'][:100]}")
        return 0

    if args.name:
        c = ci.by_name(args.name)
        if not c:
            print(f"Contact not found: {args.name}")
            return 1
        print(json.dumps(c, indent=2))
        return 0

    # Default: rebuild
    index = write_index()
    print(f"Contact index built: {index['contact_count']} contacts → {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
