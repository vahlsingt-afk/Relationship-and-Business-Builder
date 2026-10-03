#!/usr/bin/env python3
"""team_franchisee_finder.py — Team Portal read logic for Franchisee Finder.

Franchisee Finder Phase 1 (storage domain, seed import, 3 read-only
operations on system/api/server.py) shipped 2026-10-02 with zero Team
Portal wiring -- checked directly, confirmed in ROADMAP.md's scoping entry.
This module is the Phase-1-honest Team Portal slice scoped there: it
delegates the actual queries to franchisee_finder_common.py (the same
functions server.py's own listFranchiseeOrganizations/
queryFranchiseesByBrand call), so there is exactly one implementation of
"how to filter/search organizations," not two that can drift.

No new storage, no mutation -- Franchisee Finder's Phase 2 review workflow
(submitFranchiseeCorrection/reviewFranchiseeSubmission, spec section 10) is
explicitly out of scope here, same as it is for server.py's own Phase 1.

Honest-data constraint (verified directly against seeded records before
building this, not assumed): `ownership`, `legal_entities`,
`geographic_footprint`, and `people` are empty/null on every organization
seeded so far -- those are Phase 2+ fields (spec section 16). Callers must
render explicit "not yet researched" states for them, never omit the
section or imply the data exists.
"""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

import franchisee_finder_common as ffc  # noqa: E402


class NotFoundError(Exception):
    pass


def search_organizations(q: str | None = None, *, min_units: int | None = None,
                         multi_brand_only: bool = False) -> list[dict]:
    """One search box covering both directions spec section 12 asks for --
    brand->franchisee and franchisee->portfolio -- since `q` matches
    against both the organization's own name/aliases and any brand it
    operates. Person->organization search isn't possible yet (see module
    docstring)."""
    return ffc.list_organizations(min_units=min_units, multi_brand_only=multi_brand_only, q=q)


def get_organization_profile(org_slug: str) -> dict:
    """Full profile: organization record + its evidence ledger. Every
    assertion already carries its own confidence_pct/status/evidence_ids
    (franchisee_finder_common.assertion_field) -- never flatten that away
    before returning to a caller."""
    try:
        return ffc.load_organization(org_slug)
    except FileNotFoundError as exc:
        raise NotFoundError(
            f"No Franchisee Finder record for org_slug '{org_slug}'. Call search_organizations first."
        ) from exc
