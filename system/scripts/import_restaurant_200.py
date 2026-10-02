#!/usr/bin/env python3
"""Import Franchise Times Restaurant 200 tables into RB's operator hierarchy.

The magazine table uses four independent layout columns and is not tagged as a
table.  This importer uses the embedded font/position metadata so operator,
brand, and unit counts remain attached to the correct ranked company.
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path


RANK_RE = re.compile(r"^(\d{1,3})(\*)?$")
STATE_RE = re.compile(r"^[A-Z]{2}$")
MONEY_RE = re.compile(r"^\$[\d,]+$")
UNIT_RE = re.compile(r"^[\d,]+$")
COL_STARTS = (72.0, 223.0, 374.0, 525.0)
COL_WIDTH = 145.0


def _is_font(word: dict, fragment: str, size: float) -> bool:
    return fragment in word.get("fontname", "") and abs(float(word.get("size", 0)) - size) < 0.2


def _lines(words: list[dict]) -> list[list[dict]]:
    grouped: dict[float, list[dict]] = {}
    for word in words:
        key = round(float(word["top"]), 1)
        grouped.setdefault(key, []).append(word)
    return [sorted(grouped[key], key=lambda w: w["x0"]) for key in sorted(grouped)]


def _text(line: list[dict]) -> str:
    return " ".join(w["text"] for w in line).strip()


def extract(pdf_path: Path) -> list[dict]:
    try:
        import pdfplumber
    except ImportError as exc:  # pragma: no cover - environment guard
        raise SystemExit("pdfplumber is required to import the Restaurant 200 PDF") from exc

    found: dict[int, dict] = {}
    with pdfplumber.open(pdf_path) as pdf:
        for page_number in range(3, min(len(pdf.pages), 10) + 1):
            page = pdf.pages[page_number - 1]
            words = page.extract_words(extra_attrs=["fontname", "size"])
            for col_start in COL_STARTS:
                col_words = [
                    w for w in words
                    if col_start - 2 <= float(w["x0"]) < col_start + COL_WIDTH
                    and 90 <= float(w["top"]) <= 855
                ]
                candidates = []
                for word in col_words:
                    match = RANK_RE.match(word["text"])
                    if not match or not _is_font(word, "Atrament-Bold", 11.0):
                        continue
                    if abs(float(word["x0"]) - col_start) > 12:
                        continue
                    rank = int(match.group(1))
                    if 1 <= rank <= 200:
                        candidates.append((float(word["top"]), rank, bool(match.group(2))))
                candidates.sort()

                for idx, (top, rank, estimated) in enumerate(candidates):
                    bottom = candidates[idx + 1][0] - 0.2 if idx + 1 < len(candidates) else 855
                    block = [w for w in col_words if top - 0.2 <= float(w["top"]) < bottom]
                    lines = _lines(block)
                    if not lines:
                        continue

                    name_parts: list[str] = []
                    location = ""
                    revenue = None
                    unit_rows: list[dict] = []
                    after_header = False
                    for line in lines:
                        text = _text(line)
                        tokens = text.split()
                        if not tokens:
                            continue
                        if MONEY_RE.match(tokens[0]):
                            revenue = int(tokens[0][1:].replace(",", ""))
                            after_header = True
                            continue
                        if not after_header:
                            bold = all(_is_font(w, "Atrament-Bold", 11.0) for w in line)
                            if not bold:
                                continue
                            clean = tokens[1:] if RANK_RE.match(tokens[0]) else tokens
                            if not clean:
                                continue
                            if clean[-1] == "Canada" and name_parts:
                                location = name_parts.pop() + " " + " ".join(clean)
                                after_header = True
                            elif STATE_RE.match(clean[-1]) and any("," in t for t in clean[:-1]):
                                location = " ".join(clean)
                                after_header = True
                            elif not location:
                                name_parts.append(" ".join(clean))
                            continue

                        regular = all(_is_font(w, "ACaslonPro-Regular", 10.0) for w in line)
                        if not regular:
                            continue
                        if UNIT_RE.match(tokens[0]) and len(tokens) > 1:
                            unit_rows.append({
                                "brand": " ".join(tokens[1:]),
                                "unit_count": int(tokens[0].replace(",", "")),
                            })
                        elif unit_rows and not UNIT_RE.match(tokens[0]):
                            unit_rows[-1]["brand"] += " " + text

                    name = " ".join(name_parts).strip()
                    if not name or not location or not unit_rows:
                        continue
                    record = {
                        "rank": rank,
                        "rank_revenue_estimated": estimated,
                        "name": name,
                        "location": location,
                        "revenue_usd": revenue,
                        "brands": unit_rows,
                        "total_units": sum(row["unit_count"] for row in unit_rows),
                        "brand_count": len(unit_rows),
                        "hierarchy_level": "multi_brand_franchisee_group" if len(unit_rows) > 1 else "franchisee_group",
                    }
                    if rank not in found or len(unit_rows) > len(found[rank]["brands"]):
                        found[rank] = record

    operators = [found[rank] for rank in sorted(found)]
    # One wrapped California location is typographically indistinguishable
    # from a wrapped company name in the PDF's embedded text.
    for operator in operators:
        if operator["rank"] == 28 and operator["name"] == "Cotti Foods Rancho Santa":
            operator["name"] = "Cotti Foods"
            operator["location"] = "Rancho Santa " + operator["location"]
    return operators


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("pdf", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    operators = extract(args.pdf)
    extracted_unit_total = sum(operator["total_units"] for operator in operators)
    reported_total_units = 33635
    payload = {
        "contract": "rb_franchisee_hierarchy_v1",
        "description": "Searchable operator -> brand -> reported unit-count hierarchy.",
        "source": {
            "title": "2026 Restaurant 200",
            "publisher": "Franchise Times",
            "publication_date": "2026-08",
            "source_file": args.pdf.name,
            "methodology_note": "Ranked by revenue from franchised restaurants; nonrespondent revenue may be estimated.",
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "operator_count": len(operators),
        "reported_total_units": reported_total_units,
        "extracted_unit_total": extracted_unit_total,
        "unit_total_variance": reported_total_units - extracted_unit_total,
        "reconciliation_note": (
            "The publication reports 33,635 locations in its summary. The 200 printed operator rows "
            "sum to the extracted total; the difference is retained explicitly rather than allocated "
            "to any operator without evidence."
        ),
        "operators": operators,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "operator_count": len(operators)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
