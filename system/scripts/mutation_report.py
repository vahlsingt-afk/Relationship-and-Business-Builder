#!/usr/bin/env python3
"""
mutation_report.py — canonical ingest mutation-report format (RB-DEFECT-064 Phase 3).

Every structured-dataset ingestion path (hubspot_ingest.py, linkedin_ingest.py,
contacts_ingest.py) emits the same standard block of counts, requested
directly in RB-DEFECT-064: "Instead of simply acknowledging the upload, RB
should produce a mutation report." Before this, each script (when it
produced a written report at all) invented its own shape.

This module owns *only* the shared header block. It deliberately does not
try to unify each source's much richer domain-specific narrative (LinkedIn's
RC-tier career-move detection, Apple Contacts' identity-resolution trust
stats) — those stay in each script's own renderer, appended after this
block.

A count field left as `None` renders as "not computed" rather than a fake
zero — some sources genuinely don't compute some fields yet (e.g. no source
does relationship-graph mutation as of Phase 3; see RB-DEFECT-064 Phase 4+).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class MutationReport:
    source_label: str            # e.g. "HubSpot CRM Export", "LinkedIn Connections Export"
    date: str                    # ISO date
    people_imported: int
    existing_people_updated: int
    new_people_created: int
    knowledge_mutations_applied: int
    confidence: float | str      # 0-1 float, or a source-native label like "high"
    knowledge_sources_updated: int = 1
    duplicate_candidates: int | None = None
    companies_added: int | None = None
    relationship_links_created: int | None = None
    not_computed_reasons: list[str] = field(default_factory=list)

    def render_markdown(self) -> str:
        def fmt_count(value: int | None) -> str:
            return "not computed" if value is None else str(value)

        if isinstance(self.confidence, (int, float)):
            confidence_str = f"{self.confidence:.1%}"
        else:
            confidence_str = str(self.confidence)

        lines = [
            f"# {self.source_label} Ingest — {self.date}",
            "",
            "## Mutation Report",
            "",
            f"- Knowledge Sources Updated: {self.knowledge_sources_updated}",
            f"- People Imported: {self.people_imported}",
            f"- Existing People Updated: {self.existing_people_updated}",
            f"- New People Created: {self.new_people_created}",
            f"- Duplicate Candidates: {fmt_count(self.duplicate_candidates)}",
            f"- Companies Added: {fmt_count(self.companies_added)}",
            f"- Relationship Links Created: {fmt_count(self.relationship_links_created)}",
            f"- Knowledge Mutations Applied: {self.knowledge_mutations_applied}",
            f"- Confidence: {confidence_str}",
        ]
        if self.not_computed_reasons:
            lines.append("")
            for reason in self.not_computed_reasons:
                lines.append(f"- {reason}")
        lines.append("")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

def _smoke() -> bool:
    errors: list[str] = []

    full = MutationReport(
        source_label="HubSpot CRM Export", date="2026-07-07", people_imported=5,
        existing_people_updated=2, new_people_created=2, duplicate_candidates=1,
        companies_added=3, relationship_links_created=None,
        knowledge_mutations_applied=4, confidence=0.987,
    )
    md = full.render_markdown()
    if "People Imported: 5" not in md:
        errors.append("expected People Imported: 5 in rendered markdown")
    if "Relationship Links Created: not computed" not in md:
        errors.append("None field should render as 'not computed'")
    if "Confidence: 98.7%" not in md:
        errors.append("float confidence should render as a percentage")

    labeled = MutationReport(
        source_label="Apple Contacts Export", date="2026-07-07", people_imported=10,
        existing_people_updated=6, new_people_created=0,
        knowledge_mutations_applied=6, confidence="high",
    )
    md2 = labeled.render_markdown()
    if "Confidence: high" not in md2:
        errors.append("string confidence label should render as-is")
    if "Duplicate Candidates: not computed" not in md2:
        errors.append("unset optional field should default to 'not computed'")

    if errors:
        for e in errors:
            print(f"  FAIL: {e}")
        return False
    print("mutation_report smoke: all checks passed")
    return True


if __name__ == "__main__":
    import sys
    sys.exit(0 if _smoke() else 1)
