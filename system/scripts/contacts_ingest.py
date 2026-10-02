#!/usr/bin/env python3
"""
contacts_ingest.py — Apple Contacts identity-resolution pipeline (DEFECT-025).

Recognized artifact type: apple_contacts_export
Intent: baseline_identity_enrichment
Downstream consumers: SMS/Messages intelligence, relationship intelligence

6-stage pipeline:
  Stage 1 — Parse:     VCF, CSV, or XLSX → raw contact records
  Stage 2 — Resolve:   Phone (E.164) → baseline; Name → baseline; Composite
  Stage 3 — Classify:  matched_network / probable / unmatched / personal / service
  Stage 4 — Queue:     Ambiguous records enter reconciliation queue for user confirmation
  Stage 5 — Persist:   Identity map, exclusion registry, baseline enrichment
  Stage 6 — Enable:    SMS/Messages intelligence uses identity map for attribution

Drop locations:
    system/inbox/contacts_exports/   (.vcf, .csv, .xlsx)
    system/inbox/contacts.vcf        (convenience)
    system/inbox/contacts.csv

Outputs:
    system/.cache/contacts_identity_map.json     — phone → entity_id mapping
    system/.cache/contacts_exclusion_registry.json — phones excluded from scoring
    system/.cache/contacts_reconciliation_queue.json — ambiguous records for user
    system/.cache/contacts_ingest_latest.json    — ingest summary + trust stats
    system/.cache/contacts_resolved.json         — classified contact list
    baseline_index.json                          — enriched phone/email fields

CLI:
    python3 contacts_ingest.py --scan
    python3 contacts_ingest.py --ingest-new [--dry-run]
    python3 contacts_ingest.py --file PATH [--dry-run]
    python3 contacts_ingest.py --show-queue
    python3 contacts_ingest.py --resolve ID --match BASELINE_ID
    python3 contacts_ingest.py --resolve ID --exclude
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import sys
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rb_core as core  # noqa: E402
import identity_matcher as im  # noqa: E402
import mutation_report as mr  # noqa: E402
import post_ingest_intelligence as pii  # noqa: E402

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

EXPORTS_DIR = core.INBOX_DIR / "contacts_exports"
SINGLE_VCF_PATH = core.INBOX_DIR / "contacts.vcf"
SINGLE_CSV_PATH = core.INBOX_DIR / "contacts.csv"
MANIFEST_PATH = core.CACHE_DIR / "contacts_ingest_manifest.json"
LATEST_PATH = core.CACHE_DIR / "contacts_ingest_latest.json"
RESOLVED_PATH = core.CACHE_DIR / "contacts_resolved.json"
DELTAS_DIR = core.SYSTEM_DIR / "deltas"
IDENTITY_MAP_PATH = core.CACHE_DIR / "contacts_identity_map.json"
EXCLUSION_PATH = core.CACHE_DIR / "contacts_exclusion_registry.json"
QUEUE_PATH = core.CACHE_DIR / "contacts_reconciliation_queue.json"

# ---------------------------------------------------------------------------
# Classification categories
# ---------------------------------------------------------------------------

CLASS_MATCHED = "matched_network_entity"      # high-confidence baseline match
CLASS_PROBABLE = "probable_network_entity"    # name/company match, phone pending
CLASS_UNMATCHED = "unmatched"                 # no baseline match
CLASS_PERSONAL = "personal_contact"           # family, household, personal
CLASS_SERVICE = "service_automated"           # businesses, short codes, automated

PERSONAL_RX = re.compile(
    r"\b(mom|dad|mother|father|sister|brother|aunt|uncle|grandma|grandpa|"
    r"pastor|reverend|church|insurance|pharmacy|hospital|clinic|utilities|"
    r"electric|gas company|plumber|contractor|hvac|doctor|dentist|pediatric|"
    r"pediatrician|orthodon|vision|eye care|urgent care|veterinar|vet\b|"
    r"family|household|home|house)\b",
    re.I,
)
SERVICE_RX = re.compile(
    r"\b(alert|notification|verification|confirm|code|bank|credit union|"
    r"fraud|security|amazon|fedex|ups|usps|delivery|shipping|irs|dmv|"
    r"government|gov\b|utility|comcast|att|verizon|spectrum|sprint|t-mobile)\b",
    re.I,
)

# ---------------------------------------------------------------------------
# Stage 1 — Parse
# ---------------------------------------------------------------------------

def _normalize_phone(raw: str) -> str:
    """Normalize to 10-digit US number (strip +1 country code)."""
    return im.normalize_phone(raw)


def _find_header_row(rows: list[list[str]]) -> int:
    """Find the row index containing column headers in an XLSX/CSV."""
    name_cols = {"name", "first name", "first", "full name", "contact"}
    for i, row in enumerate(rows[:10]):
        normalized = {(str(c) or "").lower().strip() for c in row}
        if normalized & name_cols:
            return i
    return 0


def _parse_vcf(text: str) -> list[dict]:
    contacts = []
    card: dict[str, Any] = {}
    in_card = False
    current_key = None

    for raw_line in text.splitlines():
        line = raw_line.rstrip()

        # Handle folded lines (RFC 6350: continuation starts with whitespace)
        if in_card and line and line[0] in (" ", "\t") and current_key:
            card.setdefault("_raw_" + current_key, "")
            continue  # skip folded continuations for simplicity

        line = line.strip()
        if line.upper() == "BEGIN:VCARD":
            card = {"phones": [], "emails": [], "orgs": [], "titles": []}
            in_card = True
            current_key = None
            continue
        if line.upper() == "END:VCARD":
            if card.get("name") or card.get("phones") or card.get("emails"):
                contacts.append(card)
            in_card = False
            continue
        if not in_card:
            continue

        key_part, _, value = line.partition(":")
        key_upper = key_part.upper().split(";")[0]
        value = value.strip()
        current_key = key_upper

        if key_upper == "FN":
            card["name"] = value
        elif key_upper == "N":
            parts = value.split(";")
            last = parts[0].strip() if parts else ""
            first = parts[1].strip() if len(parts) > 1 else ""
            if not card.get("name") and (first or last):
                card["name"] = f"{first} {last}".strip()
        elif "TEL" in key_upper:
            n = _normalize_phone(value)
            if n and n not in card["phones"]:
                card["phones"].append(n)
        elif "EMAIL" in key_upper:
            e = value.lower().strip()
            if e and "@" in e and e not in card["emails"]:
                card["emails"].append(e)
        elif key_upper == "ORG":
            org = value.split(";")[0].strip()
            if org:
                card["orgs"].append(org)
        elif key_upper == "TITLE":
            if value:
                card["titles"].append(value)
        elif key_upper == "NOTE":
            card["note"] = value

    return contacts


def _parse_abbu_entry(data: bytes) -> dict | None:
    """Parse a single Apple Address Book binary plist contact entry."""
    try:
        import plistlib
        pl = plistlib.loads(data)
    except Exception:
        return None
    if not isinstance(pl, dict):
        return None
    first = str(pl.get("First") or "").strip()
    last = str(pl.get("Last") or "").strip()
    name = f"{first} {last}".strip() or str(pl.get("Organization") or "").strip()
    if not name:
        return None
    phones: list[str] = []
    for entry in (pl.get("Phone") or []):
        raw_p = str(entry.get("value") or "") if isinstance(entry, dict) else str(entry)
        n = _normalize_phone(raw_p)
        if n and n not in phones:
            phones.append(n)
    emails: list[str] = []
    for entry in (pl.get("Email") or []):
        raw_e = str(entry.get("value") or "") if isinstance(entry, dict) else str(entry)
        raw_e = raw_e.lower().strip()
        if raw_e and "@" in raw_e and raw_e not in emails:
            emails.append(raw_e)
    org = str(pl.get("Organization") or "").strip()
    title = str(pl.get("JobTitle") or "").strip()
    note = str(pl.get("Note") or "").strip()
    return {
        "name": name,
        "phones": phones,
        "emails": emails,
        "orgs": [org] if org else [],
        "titles": [title] if title else [],
        "note": note or None,
    }


def _extract_contacts_from_zip(path: Path) -> list[dict]:
    """Extract contacts from a ZIP archive (.abbu.zip, contacts.zip, etc.).

    Try order:
      1. VCF files anywhere in the archive (highest fidelity, most common export)
      2. Binary Apple Address Book plists (.abbu individual entry files)
      3. Any text file containing BEGIN:VCARD blocks
    """
    contacts: list[dict] = []
    try:
        with zipfile.ZipFile(path, "r") as zf:
            names = zf.namelist()
            names_lower = [n.lower() for n in names]

            # 1. VCF files
            vcf_entries = [n for n in names if n.lower().endswith(".vcf")]
            if vcf_entries:
                for vcf_name in vcf_entries:
                    try:
                        text = zf.read(vcf_name).decode("utf-8", errors="replace")
                        contacts.extend(_parse_vcf(text))
                    except Exception:
                        pass
                return contacts

            # 2. Apple Address Book binary plist entries (individual .abbu files inside the package)
            abbu_entries = [
                n for n in names
                if not n.endswith("/") and (
                    n.lower().endswith(".abbu") or
                    (re.search(r"\.abbu/[^/]+$", n, re.I) and "." not in n.rsplit("/", 1)[-1])
                )
            ]
            for entry_name in abbu_entries:
                try:
                    data = zf.read(entry_name)
                    contact = _parse_abbu_entry(data)
                    if contact:
                        contacts.append(contact)
                except Exception:
                    pass
            if contacts:
                return contacts

            # 3. Text files that might contain vCard data
            for name in names:
                if name.endswith("/"):
                    continue
                try:
                    text = zf.read(name).decode("utf-8", errors="replace")
                    if "BEGIN:VCARD" in text:
                        parsed = _parse_vcf(text)
                        if parsed:
                            contacts.extend(parsed)
                except Exception:
                    pass
    except zipfile.BadZipFile:
        pass
    return contacts


def _parse_csv_text(text: str) -> list[dict]:
    reader = csv.DictReader(io.StringIO(text))
    contacts = []
    for row in reader:
        row = {k.strip().lower(): (v or "").strip() for k, v in row.items() if k}
        name = (row.get("name") or row.get("full name") or
                f"{row.get('first name','').strip()} {row.get('last name','').strip()}".strip())
        phones: list[str] = []
        emails: list[str] = []
        for k, v in row.items():
            if "phone" in k and v:
                n = _normalize_phone(v)
                if n: phones.append(n)
            if "email" in k and v and "@" in v:
                emails.append(v.lower())
        org = row.get("company") or row.get("organization") or ""
        title = row.get("title") or row.get("job title") or ""
        if name or phones or emails:
            contacts.append({"name": name, "phones": phones, "emails": emails,
                             "orgs": [org] if org else [], "titles": [title] if title else []})
    return contacts


def _parse_xlsx(path: Path) -> list[dict]:
    """Parse XLSX contacts file (e.g. apple_contacts_baseline_extract.xlsx)."""
    try:
        import openpyxl
    except ImportError:
        # Fallback: try pandas
        try:
            import pandas as pd
            df = pd.read_excel(path, dtype=str).fillna("")
            df.columns = [str(c).lower().strip() for c in df.columns]
            contacts = []
            for _, row in df.iterrows():
                row = dict(row)
                name = (row.get("name") or row.get("full name") or
                        f"{row.get('first name','').strip()} {row.get('last name','').strip()}".strip())
                phones: list[str] = []
                emails: list[str] = []
                for k, v in row.items():
                    if "phone" in k and v:
                        n = _normalize_phone(v)
                        if n: phones.append(n)
                    if "email" in k and v and "@" in v:
                        emails.append(v.lower())
                org = row.get("company") or row.get("organization") or row.get("org") or ""
                title = row.get("title") or row.get("job title") or ""
                if name or phones or emails:
                    contacts.append({"name": name, "phones": phones, "emails": emails,
                                     "orgs": [org] if org else [], "titles": [title] if title else []})
            return contacts
        except ImportError:
            raise RuntimeError("openpyxl or pandas required to parse XLSX files. pip install openpyxl")

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    all_rows = [[str(cell.value or "").strip() for cell in row] for row in ws.iter_rows()]
    wb.close()

    if not all_rows:
        return []

    header_idx = _find_header_row(all_rows)
    headers = [h.lower() for h in all_rows[header_idx]]
    contacts = []

    for raw_row in all_rows[header_idx + 1:]:
        if not any(raw_row):
            continue
        row = {headers[i]: raw_row[i] for i in range(min(len(headers), len(raw_row)))}

        name = (row.get("name") or row.get("full name") or
                f"{row.get('first name','').strip()} {row.get('last name','').strip()}".strip())
        phones: list[str] = []
        emails: list[str] = []
        for k, v in row.items():
            if v and "phone" in k:
                n = _normalize_phone(v)
                if n: phones.append(n)
            if v and "email" in k and "@" in v:
                emails.append(v.lower())
        org = row.get("company") or row.get("organization") or row.get("org") or ""
        title = row.get("title") or row.get("job title") or ""
        if name or phones or emails:
            contacts.append({"name": name, "phones": phones, "emails": emails,
                             "orgs": [org] if org else [], "titles": [title] if title else []})
    return contacts


def parse_file(path: Path) -> list[dict]:
    suffix = path.suffix.lower()
    if suffix == ".vcf":
        return _parse_vcf(path.read_text(encoding="utf-8", errors="replace"))
    elif suffix == ".csv":
        return _parse_csv_text(path.read_text(encoding="utf-8", errors="replace"))
    elif suffix == ".xlsx":
        return _parse_xlsx(path)
    elif suffix == ".zip":
        return _extract_contacts_from_zip(path)
    else:
        raise ValueError(f"Unsupported contacts file type: {suffix}")


# ---------------------------------------------------------------------------
# Stage 2 — Identity Resolution
# ---------------------------------------------------------------------------

# Nickname → canonical name map (bidirectional at match time)
_NICKNAME_CANONICAL: dict[str, str] = {
    "bob": "robert", "rob": "robert", "bobby": "robert",
    "bill": "william", "will": "william", "billy": "william",
    "jim": "james", "jimmy": "james", "jamie": "james",
    "johnny": "john", "jon": "john",
    "jenn": "jennifer", "jenny": "jennifer", "jen": "jennifer",
    "mike": "michael", "mick": "michael", "mickey": "michael",
    "matt": "matthew", "mat": "matthew",
    "tom": "thomas", "tommy": "thomas",
    "dave": "david", "davey": "david",
    "rich": "richard", "rick": "richard", "dick": "richard", "ricky": "richard",
    "peggy": "margaret", "maggie": "margaret", "marge": "margaret", "meg": "margaret",
    "liz": "elizabeth", "beth": "elizabeth", "betsy": "elizabeth", "eliza": "elizabeth",
    "kate": "katherine", "kathy": "katherine", "katie": "katherine", "kat": "katherine",
    "chris": "christopher",
    "andy": "andrew", "drew": "andrew",
    "joe": "joseph", "joey": "joseph",
    "dan": "daniel", "danny": "daniel",
    "nick": "nicholas", "nicky": "nicholas",
    "pat": "patricia", "patty": "patricia", "trish": "patricia",
    "sue": "susan", "susie": "susan",
    "barb": "barbara", "babs": "barbara",
    "nan": "nancy",
    "deb": "deborah", "debbie": "deborah",
    "terry": "theresa",
    "sam": "samuel", "sammy": "samuel",
    "alex": "alexander",
    "tony": "anthony",
    "ken": "kenneth", "kenny": "kenneth",
    "ron": "ronald", "ronnie": "ronald",
    "don": "donald", "donnie": "donald",
    "larry": "lawrence",
    "gary": "gerald",
    "ben": "benjamin", "benny": "benjamin",
    "steve": "steven", "stevie": "steven",
    "ed": "edward", "eddie": "edward", "ned": "edward",
    "frank": "francis",
    "fred": "frederick", "freddy": "frederick",
    "greg": "gregory",
    "hal": "harold",
    "hank": "henry",
    "jake": "jacob",
    "jeff": "jeffrey",
    "jerry": "jerome",
    "chuck": "charles", "charlie": "charles",
    "art": "arthur",
    "walt": "walter",
    "ray": "raymond",
    "vince": "vincent",
}

# Reverse map: canonical → set of nicknames
_CANONICAL_TO_NICKNAMES: dict[str, set[str]] = {}
for _nick, _canon in _NICKNAME_CANONICAL.items():
    _CANONICAL_TO_NICKNAMES.setdefault(_canon, set()).add(_nick)


def _name_key(name: str) -> str:
    return im.name_letters_key(name)


def _canonical_first(first: str) -> str:
    """Return the canonical form of a first name (resolve nickname → canonical)."""
    key = _name_key(first)
    return _NICKNAME_CANONICAL.get(key, key)


def _first_names_match(a: str, b: str) -> tuple[bool, bool]:
    """Return (matches, is_nickname_match). Both directions checked."""
    ka, kb = _name_key(a), _name_key(b)
    if ka == kb:
        return True, False
    # a is a nickname for b's canonical, or b is a nickname for a's canonical
    canon_a = _NICKNAME_CANONICAL.get(ka, ka)
    canon_b = _NICKNAME_CANONICAL.get(kb, kb)
    if canon_a == canon_b:
        return True, True
    return False, False


def _build_baseline_indexes(contacts: list[dict]) -> tuple[dict, dict, dict]:
    """Return (phone_idx, email_idx, name_idx)."""
    return (
        im.build_phone_index(contacts),
        im.build_email_index(contacts),
        im.build_name_index_single(contacts, key_fn=im.name_letters_key),
    )


def _resolve_contact(
    raw: dict,
    phone_idx: dict,
    email_idx: dict,
    name_idx: dict,
    exclusion_phones: set[str],
) -> tuple[dict | None, str, float]:
    """Resolve a raw contact to a baseline entry.

    Returns (matched_contact | None, match_method, confidence 0-1).
    """
    # Phone match (primary key — highest confidence)
    for phone in (raw.get("phones") or []):
        if phone in exclusion_phones:
            return None, "excluded", 0.0
        match = phone_idx.get(phone)
        if match:
            return match, "phone_exact", 1.0

    # Email match
    for email in (raw.get("emails") or []):
        match = email_idx.get(email)
        if match:
            return match, "email_exact", 1.0

    # Exact name match
    name = raw.get("name") or ""
    key = _name_key(name)
    if key:
        match = name_idx.get(key)
        if match:
            return match, "name_exact", 0.90

    # First + last name match (including nickname expansion)  — Tier 2 High Confidence
    parts = name.strip().split()
    if len(parts) >= 2:
        raw_first, raw_last = parts[0], parts[-1]
        raw_last_key = _name_key(raw_last)

        for bc in name_idx.values():
            bparts = (bc.get("name") or "").strip().split()
            if len(bparts) < 2:
                continue
            b_first, b_last = bparts[0], bparts[-1]

            if _name_key(b_last) != raw_last_key:
                continue  # Last name must always match exactly

            first_match, is_nickname = _first_names_match(raw_first, b_first)
            if not first_match:
                continue

            orgs = [o.lower() for o in (raw.get("orgs") or [])]
            bco = (bc.get("current_company") or "").lower()
            company_match = orgs and any(o[:6] in bco or bco[:6] in o for o in orgs if o)

            if is_nickname:
                method = "nickname+last+company" if company_match else "nickname+last"
                conf = 0.80 if not company_match else 0.85
            else:
                method = "name_first+last+company" if company_match else "name_first+last"
                conf = 0.90 if not company_match else 0.95
            return bc, method, conf

    # Name + company only (Tier 3 additional validation) — below 70% threshold, queue
    orgs = [o.lower() for o in (raw.get("orgs") or []) if o]
    parts_keys = [_name_key(p) for p in parts]
    if parts_keys and orgs:
        for bc in name_idx.values():
            bco = (bc.get("current_company") or "").lower()
            if not any(o[:8] in bco for o in orgs):
                continue
            bparts = [(bc.get("name") or "").strip().split()]
            bname_keys = [_name_key(p) for p in (bc.get("name") or "").split()]
            if bname_keys and parts_keys and (
                parts_keys[-1] == bname_keys[-1] or parts_keys[0] == bname_keys[0]
            ):
                return bc, "name_partial+company", 0.70

    return None, "no_match", 0.0


# ---------------------------------------------------------------------------
# Stage 3 — Classification
# ---------------------------------------------------------------------------

def _classify_contact(raw: dict, match: dict | None, confidence: float) -> str:
    name = raw.get("name") or ""
    orgs = " ".join(raw.get("orgs") or [])
    titles = " ".join(raw.get("titles") or [])
    phones = raw.get("phones") or []

    # Service number detection (short codes, known service patterns)
    if phones and all(len(p) < 8 for p in phones):
        return CLASS_SERVICE
    combined = f"{name} {orgs} {titles}"
    if SERVICE_RX.search(combined):
        return CLASS_SERVICE
    if PERSONAL_RX.search(combined):
        return CLASS_PERSONAL

    if match and confidence >= 0.85:
        return CLASS_MATCHED
    if match and confidence >= 0.70:
        return CLASS_PROBABLE  # Below 0.70 = unmatched → queue for review
    return CLASS_UNMATCHED


def _detect_employer_change(raw: dict, match: dict) -> dict | None:
    """Return a change dict if the contact's current company differs from baseline."""
    raw_org = (raw.get("orgs") or [""])[0].strip()
    if not raw_org:
        return None
    baseline_org = (match.get("current_company") or "").strip()
    if not baseline_org:
        return None
    raw_key = re.sub(r"[^a-z0-9]", "", raw_org.lower())
    base_key = re.sub(r"[^a-z0-9]", "", baseline_org.lower())
    if raw_key == base_key:
        return None
    # Treat as same if one is a prefix of the other (handles abbreviations)
    if raw_key[:8] == base_key[:8]:
        return None
    return {
        "baseline_id": match.get("id"),
        "name": match.get("name"),
        "from_company": baseline_org,
        "to_company": raw_org,
        "raw_title": (raw.get("titles") or [""])[0],
        "confidence": "medium",
    }


def _build_cos_assessment(
    *,
    matched: list[dict],
    probable: list[dict],
    unmatched: list[dict],
    personal: list[dict],
    service: list[dict],
    employer_changes: list[dict],
    phone_enrichments: int,
    email_enrichments: int,
    new_queue_items: list[dict],
    mutations: list[dict],
) -> dict:
    """Build the Stage 5 CoS-level intelligence summary."""
    total = (len(matched) + len(probable) + len(unmatched)
             + len(personal) + len(service))
    network = matched + probable + unmatched

    missing_phones = sum(1 for c in network if not c.get("phones"))
    missing_emails = sum(1 for c in network if not c.get("emails"))

    notable_changes = [
        f"{ec['name']} moved from {ec['from_company']} to {ec['to_company']}"
        + (f" ({ec['raw_title']})" if ec.get("raw_title") else "")
        for ec in employer_changes[:10]
    ]
    relationship_opportunities = [
        f"Update relationship context for {ec['name']}: now at {ec['to_company']}"
        for ec in employer_changes[:5]
    ]
    network_health = []
    if missing_phones > 5:
        network_health.append(f"{missing_phones} contacts missing phone numbers")
    if missing_emails > 5:
        network_health.append(f"{missing_emails} contacts missing email addresses")
    if probable:
        network_health.append(
            f"{len(probable)} contacts need reconciliation confirmation"
        )

    return {
        "contacts_scanned": total,
        "matched_network_entities": len(matched),
        "probable_network_entities": len(probable),
        "unmatched": len(unmatched),
        "personal_excluded": len(personal),
        "service_excluded": len(service),
        "employer_changes_detected": len(employer_changes),
        "employer_changes": employer_changes[:20],
        "phone_enrichments": phone_enrichments,
        "email_enrichments": email_enrichments,
        "reconciliation_queue_additions": len(new_queue_items),
        "baseline_mutations": len(mutations),
        "notable_changes": notable_changes,
        "relationship_opportunities": relationship_opportunities,
        "network_health": network_health,
        "missing_phone_count": missing_phones,
        "missing_email_count": missing_emails,
    }


def _render_report(result: dict, d: date) -> str:
    """RB-DEFECT-064 Phase 3: canonical mutation report, this pipeline's
    first-ever markdown delta report (previously JSON cache only)."""
    ts = result["trust_stats"]
    cos = result["cos_assessment"]

    report = mr.MutationReport(
        source_label="Apple Contacts Export",
        date=d.isoformat(),
        people_imported=ts["total_parsed"],
        existing_people_updated=ts["matched_network_entities"],
        new_people_created=0,  # this pipeline enriches identity only; never creates baseline entries
        duplicate_candidates=ts["probable_network_entities"],
        companies_added=None,
        relationship_links_created=None,
        knowledge_mutations_applied=ts["baseline_mutations"],
        confidence=ts["confidence"],
        not_computed_reasons=[
            "Companies Added not computed — this pipeline enriches phone/email identity only; "
            "it never writes company data to baseline.",
            f"Relationship Links Created not computed — {ts['employer_changes_detected']} employer "
            "change(s) detected (see Employer Changes below) but not written to baseline; this "
            "pipeline surfaces them for the operator rather than auto-mutating current_company.",
        ],
    )
    lines = [report.render_markdown()]

    if cos["notable_changes"]:
        lines.append("## Employer Changes Detected (surfaced, not auto-applied)")
        lines.append("")
        for change in cos["notable_changes"]:
            lines.append(f"- {change}")
        lines.append("")

    if ts["probable_network_entities"]:
        lines.append("## Duplicate/Ambiguous Candidates (needs operator confirmation)")
        lines.append("")
        lines.append(f"- {ts['probable_network_entities']} contact(s) queued in the reconciliation queue.")
        lines.append("")

    dormant = ((result.get("post_ingest_intelligence") or {}).get("dormant_relationships_resurfaced") or [])
    lines.append("## Post-Ingest Intelligence")
    lines.append("")
    if dormant:
        lines.append(f"**Dormant relationships resurfaced ({len(dormant)}):**")
        lines.append("")
        for d_entry in dormant:
            days = d_entry["days_since_last_touch"]
            age = "no last_touch on file" if days is None else f"last touch {days} days ago"
            lines.append(f"- {d_entry['name']} — {age}")
        lines.append("")
    else:
        lines.append("- No dormant relationships among the contacts this import touched.")
        lines.append("")

    lines.append("## What the system did NOT do")
    lines.append("")
    lines.append("- Did not create new baseline entries for unmatched contacts.")
    lines.append("- Did not overwrite an existing phone/email — only backfilled when null.")
    lines.append("- Did not write detected employer changes into current_company (surfaced for confirmation instead).")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Stage 4 — Reconciliation queue
# ---------------------------------------------------------------------------

def _reconciliation_candidates(
    raw: dict,
    name_idx: dict,
    phone_idx: dict,
) -> list[dict]:
    """For an unmatched contact, find up to 3 plausible baseline candidates."""
    name = raw.get("name") or ""
    parts = name.lower().split()
    candidates: list[tuple[float, dict]] = []

    for bkey, bc in name_idx.items():
        bname = (bc.get("name") or "").lower().split()
        score = 0.0
        if parts and bname:
            if parts[-1] == bname[-1]:
                score += 0.4
            if len(parts) > 1 and len(bname) > 1 and parts[0] == bname[0]:
                score += 0.3
            if parts and bname and parts[-1] in " ".join(bname):
                score += 0.2
        orgs = [o.lower() for o in (raw.get("orgs") or []) if o]
        bco = (bc.get("current_company") or "").lower()
        if orgs and any(o[:6] in bco for o in orgs):
            score += 0.3
        if score > 0.3:
            candidates.append((score, bc))

    candidates.sort(key=lambda x: -x[0])
    return [
        {"id": bc.get("id"), "name": bc.get("name"), "company": bc.get("current_company"),
         "score": round(score, 2)}
        for score, bc in candidates[:3]
    ]


# ---------------------------------------------------------------------------
# Stage 5 — Persistence helpers
# ---------------------------------------------------------------------------

def _load_json(path: Path, default: Any) -> Any:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            pass
    return default


def _save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str) + "\n")


def _load_identity_map() -> dict:
    return _load_json(IDENTITY_MAP_PATH, {})


def _load_exclusions() -> dict:
    return _load_json(EXCLUSION_PATH, {"excluded_phones": [], "excluded_names": []})


def _load_queue() -> list:
    return _load_json(QUEUE_PATH, [])


# ---------------------------------------------------------------------------
# Core ingest function
# ---------------------------------------------------------------------------

def ingest_file(path: Path, *, dry_run: bool = False) -> dict:
    """Full 6-stage identity resolution pipeline for one contacts file."""
    raw_contacts = parse_file(path)

    baseline_raw = json.loads(core.BASELINE_PATH.read_text())
    baseline_contacts: list[dict] = (
        baseline_raw if isinstance(baseline_raw, list)
        else list(baseline_raw.values())
    )
    phone_idx, email_idx, name_idx = _build_baseline_indexes(baseline_contacts)

    exclusions = _load_exclusions()
    exclusion_phones: set[str] = set(exclusions.get("excluded_phones") or [])

    identity_map = _load_identity_map()
    queue = _load_queue()
    existing_queue_ids = {q.get("contact_fingerprint") for q in queue}

    # Per-stage accumulators
    matched: list[dict] = []
    probable: list[dict] = []
    unmatched: list[dict] = []
    personal: list[dict] = []
    service: list[dict] = []
    mutations: list[dict] = []
    new_queue_items: list[dict] = []
    employer_changes: list[dict] = []

    phone_enrichments = 0
    email_enrichments = 0

    for raw in raw_contacts:
        match, method, confidence = _resolve_contact(
            raw, phone_idx, email_idx, name_idx, exclusion_phones
        )
        category = _classify_contact(raw, match, confidence)

        # Fingerprint for dedup
        fingerprint = hashlib.sha256(
            json.dumps({"name": raw.get("name"), "phones": sorted(raw.get("phones") or [])},
                       sort_keys=True).encode()
        ).hexdigest()[:12]

        record: dict = {
            "fingerprint": fingerprint,
            "name": raw.get("name"),
            "phones": raw.get("phones") or [],
            "emails": raw.get("emails") or [],
            "orgs": raw.get("orgs") or [],
            "titles": raw.get("titles") or [],
            "category": category,
            "match_method": method,
            "confidence": confidence,
            "baseline_id": match.get("id") if match else None,
            "baseline_name": match.get("name") if match else None,
        }

        if category in (CLASS_PERSONAL, CLASS_SERVICE):
            (personal if category == CLASS_PERSONAL else service).append(record)
            continue

        if category == CLASS_MATCHED and match:
            matched.append(record)
            # Baseline enrichment
            delta: dict = {}
            phones = record["phones"]
            if phones and not match.get("phone"):
                delta["phone"] = phones[0]
                phone_enrichments += 1
            if record["emails"] and not match.get("email"):
                delta["email"] = record["emails"][0]
                email_enrichments += 1
            if delta:
                mutations.append({"type": "enrich", "id": match["id"],
                                  "name": match.get("name"), "delta": delta})
                if not dry_run:
                    match.update(delta)
            # Employer change detection
            ec = _detect_employer_change(raw, match)
            if ec:
                employer_changes.append(ec)
            # Update identity map
            for phone in phones:
                identity_map[phone] = {"entity_id": match["id"],
                                       "entity_name": match.get("name"),
                                       "confidence": confidence,
                                       "method": method}

        elif category == CLASS_PROBABLE and match:
            probable.append(record)
            # Queue for user confirmation
            if fingerprint not in existing_queue_ids:
                candidates = _reconciliation_candidates(raw, name_idx, phone_idx)
                queue_item = {
                    "contact_fingerprint": fingerprint,
                    "raw": record,
                    "probable_match": {"id": match.get("id"), "name": match.get("name"),
                                       "company": match.get("current_company"),
                                       "confidence": confidence, "method": method},
                    "other_candidates": candidates,
                    "status": "pending",
                    "added_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                }
                new_queue_items.append(queue_item)

        elif category == CLASS_UNMATCHED:
            unmatched.append(record)
            # Queue unmatched with phone numbers for resolution
            if record["phones"] and fingerprint not in existing_queue_ids:
                candidates = _reconciliation_candidates(raw, name_idx, phone_idx)
                queue_item = {
                    "contact_fingerprint": fingerprint,
                    "raw": record,
                    "probable_match": None,
                    "other_candidates": candidates,
                    "status": "pending",
                    "added_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                }
                new_queue_items.append(queue_item)

    # Stage 5 — Persist
    # Baseline mutations are gated by dry_run; queue/resolved/latest always persist
    # so the brief can surface pending work even on a first dry run.
    if not dry_run:
        if mutations:
            core.BASELINE_PATH.write_text(
                json.dumps(baseline_contacts, indent=2) + "\n"
            )
        _save_json(IDENTITY_MAP_PATH, {
            "_generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "_source": path.name,
            "phones": identity_map,
        })

    # Always save queue, resolved cache, and latest summary regardless of dry_run
    queue.extend(new_queue_items)
    _save_json(QUEUE_PATH, queue)
    all_resolved = matched + probable + unmatched
    _save_json(RESOLVED_PATH, {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_file": path.name,
        "contacts": all_resolved,
    })

    # Trust stats
    total = len(raw_contacts)
    classified = len(matched) + len(probable) + len(unmatched) + len(personal) + len(service)
    trust_stats = {
        "sources_assessed": 1,
        "total_parsed": total,
        "classified": classified,
        "matched_network_entities": len(matched),
        "probable_network_entities": len(probable),
        "unmatched": len(unmatched),
        "personal_excluded": len(personal),
        "service_excluded": len(service),
        "phone_enrichments": phone_enrichments,
        "email_enrichments": email_enrichments,
        "baseline_mutations": len(mutations),
        "employer_changes_detected": len(employer_changes),
        "reconciliation_queue_additions": len(new_queue_items),
        "identity_map_entries": len([v for v in identity_map.values()
                                     if isinstance(v, dict)]),
        "confidence": "high" if len(matched) > 5 else "medium",
        "trust_contract_met": total > 0,
    }

    cos_assessment = _build_cos_assessment(
        matched=matched,
        probable=probable,
        unmatched=unmatched,
        personal=personal,
        service=service,
        employer_changes=employer_changes,
        phone_enrichments=phone_enrichments,
        email_enrichments=email_enrichments,
        new_queue_items=new_queue_items,
        mutations=mutations,
    )

    # RB-DEFECT-064 Phase 5 — same Stage 6 dormancy signal as hubspot_ingest.py
    # and linkedin_ingest.py: existing contacts this import touched (enriched
    # or otherwise matched) that have gone quiet. No warm_intro_candidates
    # here — Apple Contacts import never auto-creates new baseline entries
    # (unmatched/probable rows go to the reconciliation queue for operator
    # confirmation, not straight into the baseline), so there is no
    # newly-created-contact set to score for a broker.
    baseline_by_id = {e["id"]: e for e in baseline_contacts if e.get("id")}
    touched_ids = [r["baseline_id"] for r in matched if r.get("baseline_id")]
    dormant = pii.dormant_relationships_resurfaced(touched_ids, baseline_by_id, today=date.today())

    result = {
        "ok": True,
        "file": path.name,
        "dry_run": dry_run,
        "artifact_type": "apple_contacts_export",
        "intent": "baseline_identity_enrichment",
        "trust_stats": trust_stats,
        "cos_assessment": cos_assessment,
        "mutations": mutations,
        "employer_changes": employer_changes[:20],
        "reconciliation_queue_additions": new_queue_items[:10],
        "post_ingest_intelligence": {
            "dormant_relationships_resurfaced": dormant,
        },
    }

    if not dry_run:
        d = date.today()
        DELTAS_DIR.mkdir(parents=True, exist_ok=True)
        report_path = DELTAS_DIR / f"apple_contacts_export_{d.isoformat()}.md"
        report_path.write_text(_render_report(result, d), encoding="utf-8")
        try:
            result["report_path"] = str(report_path.relative_to(core.PROJECT_DIR))
        except ValueError:
            result["report_path"] = str(report_path)

    _save_json(LATEST_PATH, result)

    return result


# ---------------------------------------------------------------------------
# Reconciliation queue management
# ---------------------------------------------------------------------------

def show_queue() -> dict:
    queue = _load_queue()
    pending = [q for q in queue if q.get("status") == "pending"]
    return {
        "total": len(queue),
        "pending": len(pending),
        "items": pending[:20],
    }


def resolve_queue_item(fingerprint: str, *, baseline_id: str | None = None,
                       exclude: bool = False) -> dict:
    """User resolves one reconciliation queue item."""
    queue = _load_queue()
    identity_map_data = _load_identity_map()
    phones_map = identity_map_data.get("phones") or identity_map_data
    exclusions = _load_exclusions()

    updated = False
    for item in queue:
        if item.get("contact_fingerprint") != fingerprint:
            continue
        raw = item.get("raw") or {}
        phones = raw.get("phones") or []

        if exclude:
            # Add all phones to exclusion registry
            excl_phones = set(exclusions.get("excluded_phones") or [])
            excl_phones.update(phones)
            exclusions["excluded_phones"] = sorted(excl_phones)
            item["status"] = "excluded"
        elif baseline_id:
            # Link to baseline entity
            baseline_raw = json.loads(core.BASELINE_PATH.read_text())
            baseline = baseline_raw if isinstance(baseline_raw, list) else list(baseline_raw.values())
            match = next((c for c in baseline if c.get("id") == baseline_id), None)
            if not match:
                return {"ok": False, "error": f"baseline_id not found: {baseline_id}"}
            # Update identity map
            for phone in phones:
                phones_map[phone] = {"entity_id": baseline_id,
                                     "entity_name": match.get("name"),
                                     "confidence": 1.0, "method": "user_confirmed"}
            # Enrich baseline phone if missing
            if phones and not match.get("phone"):
                match["phone"] = phones[0]
                core.BASELINE_PATH.write_text(json.dumps(baseline, indent=2) + "\n")
            item["status"] = "resolved"
            item["resolved_to"] = baseline_id
        item["resolved_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        updated = True
        break

    if not updated:
        return {"ok": False, "error": f"fingerprint not found: {fingerprint}"}

    _save_json(QUEUE_PATH, queue)
    _save_json(EXCLUSION_PATH, exclusions)
    _save_json(IDENTITY_MAP_PATH, {
        "_generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "phones": phones_map,
    })
    return {"ok": True, "fingerprint": fingerprint,
            "action": "excluded" if exclude else f"resolved→{baseline_id}"}


# ---------------------------------------------------------------------------
# File discovery + manifest
# ---------------------------------------------------------------------------

def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:32]


def _discover_files() -> list[Path]:
    found: list[Path] = []
    if EXPORTS_DIR.exists():
        for ext in ("*.vcf", "*.csv", "*.xlsx", "*.zip"):
            found += list(EXPORTS_DIR.glob(ext))
    for p in (SINGLE_VCF_PATH, SINGLE_CSV_PATH):
        if p.exists():
            found.append(p)
    return found


def _load_manifest() -> dict:
    return _load_json(MANIFEST_PATH, {"processed": {}})


def run_scan() -> dict:
    files = _discover_files()
    manifest = _load_manifest()
    new_files = [f for f in files if _file_hash(f) not in manifest["processed"]]
    queue = _load_json(QUEUE_PATH, [])
    pending = [item for item in queue if not item.get("resolved")]
    return {
        "discovered": len(files),
        "new": len(new_files),
        "files": [str(f) for f in new_files],
        "pending_queue_items": len(pending),
        "pending_queue": pending[:10],
    }


def run_ingest_new(*, dry_run: bool = False) -> dict:
    files = _discover_files()
    manifest = _load_manifest()
    results: list[dict] = []
    new_hashes: dict[str, str] = {}
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    for path in files:
        fhash = _file_hash(path)
        if fhash in manifest["processed"]:
            continue
        result = ingest_file(path, dry_run=dry_run)
        results.append(result)
        new_hashes[fhash] = str(path)

    if not dry_run and new_hashes:
        manifest["processed"].update(
            {h: {"path": p, "processed_at": now} for h, p in new_hashes.items()}
        )
        _save_json(MANIFEST_PATH, manifest)

    ts = _agg_trust_stats(results)
    summary = {
        "ok": True,
        "files_processed": len(results),
        "dry_run": dry_run,
        "aggregate_trust_stats": ts,
        "results": results,
        "generated_at": now,
    }
    if not dry_run:
        _save_json(LATEST_PATH, summary)
    return summary


def _agg_trust_stats(results: list[dict]) -> dict:
    ts: dict[str, int] = {}
    for r in results:
        for k, v in (r.get("trust_stats") or {}).items():
            if isinstance(v, (int, float)):
                ts[k] = ts.get(k, 0) + v
    return ts


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _print_result(result: dict) -> None:
    ts = result.get("trust_stats") or result.get("aggregate_trust_stats") or {}
    print(f"\nArtifact type:  {result.get('artifact_type','contacts')}")
    print(f"Intent:         {result.get('intent','')}")
    print(f"File:           {result.get('file','')}")
    print()
    for k, v in ts.items():
        if isinstance(v, (int, float)) and k not in ("sources_assessed",):
            print(f"  {k:<38} {v}")
    if result.get("dry_run"):
        print("\n  [DRY RUN — no changes written]")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--scan", action="store_true")
    g.add_argument("--ingest-new", action="store_true")
    g.add_argument("--file", metavar="PATH")
    g.add_argument("--show-queue", action="store_true")
    g.add_argument("--resolve", metavar="FINGERPRINT",
                   help="Resolve a queue item by fingerprint.")
    p.add_argument("--match", metavar="BASELINE_ID",
                   help="Baseline ID to link the resolved contact to.")
    p.add_argument("--exclude", action="store_true",
                   help="Mark a queue contact as personal/excluded.")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--confirm", action="store_true")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    if args.scan:
        result = run_scan()
    elif args.show_queue:
        result = show_queue()
    elif args.resolve:
        result = resolve_queue_item(
            args.resolve,
            baseline_id=args.match,
            exclude=args.exclude,
        )
    elif args.file:
        result = ingest_file(Path(args.file), dry_run=args.dry_run)
    else:
        result = run_ingest_new(dry_run=args.dry_run and not args.confirm)

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    elif args.scan:
        print(f"Discovered: {result['discovered']}  New: {result['new']}")
        for f in result.get("files") or []:
            print(f"  {f}")
    elif args.show_queue:
        q = result
        print(f"Queue: {q['pending']} pending of {q['total']} total")
        for item in (q.get("items") or [])[:10]:
            raw = item.get("raw") or {}
            pm = item.get("probable_match") or {}
            print(f"\n  [{item.get('contact_fingerprint','')}] {raw.get('name','?')}")
            print(f"    Phones: {raw.get('phones',[])}  Orgs: {raw.get('orgs',[])}")
            if pm:
                print(f"    Probable match: {pm.get('name')} ({pm.get('confidence',0):.0%}, {pm.get('method')})")
            cands = item.get("other_candidates") or []
            for c in cands:
                print(f"    Candidate: {c.get('name')} @ {c.get('company')} (score {c.get('score')})")
    else:
        _print_result(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
