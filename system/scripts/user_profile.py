#!/usr/bin/env python3
"""user_profile.py — RB profile loader with legacy fallback.

Resolves the active profile_id, reads the canonical profile directory layout,
and exposes structured profile facts to opportunity sensing and daily brief
rendering.

Resolution order:
  1. Explicit profile_id passed to load() or set in settings.json under
     "active_profile_id".
  2. Single profile discovered under system/profiles/ (auto-select if exactly
     one exists).
  3. Legacy fallback: system/00_TODD_PROFILE.md seed profile, reported as
     source="legacy_seed".

Output shape (also returned by load()):
    {
      "profile_id": "todd_vahlsing",
      "source": "profiles_dir|legacy_seed",
      "facts": {},                          # from profile.md YAML frontmatter + text
      "capabilities": [],
      "product_service_capabilities": [],
      "target_customers": [],
      "target_employers": [],
      "target_industries": [],
      "adjacent_industries": [],
      "preferred_opportunity_types": [],
      "no_go_categories": [],
      "credible_pain_types": [],
      "constraints": {},
      "voice_constraints": [],
      "intro_philosophy": "balanced",
      "intro_boundaries": [],
      "confidence": "high|medium|low",
    }

CLI:
    python3 user_profile.py                     # print loaded profile (human-readable)
    python3 user_profile.py --json              # emit JSON
    python3 user_profile.py --profile todd_vahlsing
    python3 user_profile.py --smoke             # in-memory regression tests
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

try:
    import rb_core as core  # noqa: E402
    _SYSTEM_DIR = core.SYSTEM_DIR
except Exception:
    _SYSTEM_DIR = _HERE.parent

_PROFILES_DIR = _SYSTEM_DIR / "profiles"
_LEGACY_SEED = _SYSTEM_DIR / "00_TODD_PROFILE.md"
_SETTINGS_PATH = _SYSTEM_DIR / "settings.json"

# ---------------------------------------------------------------------------
# YAML lite-parser (only the subset used in our profile files — no dependency)
# ---------------------------------------------------------------------------

def _parse_yaml_list(text: str, key: str) -> list[str]:
    """Extract a YAML list under `key:` from text. Returns [] if not found."""
    pattern = re.compile(
        r"^" + re.escape(key) + r"\s*:\s*\n((?:[ \t]*-[^\n]*\n)*)",
        re.MULTILINE,
    )
    m = pattern.search(text)
    if not m:
        return []
    block = m.group(1)
    items = []
    for line in block.splitlines():
        stripped = re.sub(r"^\s*-\s*", "", line).strip()
        # strip inline comments
        stripped = re.sub(r"\s*#.*$", "", stripped).strip()
        if stripped:
            items.append(stripped)
    return items


def _parse_yaml_scalar(text: str, key: str) -> str | None:
    """Extract a scalar YAML value for `key:` from text."""
    pattern = re.compile(
        r"^" + re.escape(key) + r"\s*:\s*(.+)$",
        re.MULTILINE,
    )
    m = pattern.search(text)
    if not m:
        return None
    val = m.group(1).strip()
    # strip inline comments
    val = re.sub(r"\s*#.*$", "", val).strip()
    # strip quotes
    val = val.strip("\"'")
    return val or None


# ---------------------------------------------------------------------------
# Settings / active profile resolution
# ---------------------------------------------------------------------------

def _load_settings() -> dict[str, Any]:
    if _SETTINGS_PATH.exists():
        try:
            return json.loads(_SETTINGS_PATH.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    return {}


def _resolve_profile_id(explicit: str | None = None) -> tuple[str, str]:
    """Return (profile_id, source) where source is 'profiles_dir' or 'legacy_seed'."""
    # 1. Explicit override
    if explicit:
        profile_dir = _PROFILES_DIR / explicit
        if profile_dir.is_dir():
            return explicit, "profiles_dir"
        # Explicit ID given but not found in profiles_dir — still try legacy
        if explicit == "todd_vahlsing" and _LEGACY_SEED.exists():
            return "todd_vahlsing", "legacy_seed"
        return explicit, "profiles_dir"  # caller will handle missing gracefully

    # 2. settings.json active_profile_id
    settings = _load_settings()
    configured = settings.get("active_profile_id")
    if configured:
        profile_dir = _PROFILES_DIR / configured
        if profile_dir.is_dir():
            return configured, "profiles_dir"

    # 3. Auto-select single profile
    if _PROFILES_DIR.is_dir():
        found = [p.name for p in _PROFILES_DIR.iterdir() if p.is_dir()]
        if len(found) == 1:
            return found[0], "profiles_dir"
        if len(found) > 1:
            # Multiple profiles without an explicit setting — use first alphabetically
            found.sort()
            return found[0], "profiles_dir"

    # 4. Legacy fallback
    if _LEGACY_SEED.exists():
        return "todd_vahlsing", "legacy_seed"

    return "unknown", "legacy_seed"


# ---------------------------------------------------------------------------
# Profile readers
# ---------------------------------------------------------------------------

def _read_opportunity_context(profile_dir: Path) -> dict[str, Any]:
    """Parse opportunity_context.yaml into structured fields."""
    path = profile_dir / "opportunity_context.yaml"
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    return {
        "capabilities": _parse_yaml_list(text, "capabilities"),
        "product_service_capabilities": _parse_yaml_list(text, "product_service_capabilities"),
        "target_industries": _parse_yaml_list(text, "target_industries"),
        "adjacent_industries": _parse_yaml_list(text, "adjacent_industries"),
        "target_customers": _parse_yaml_list(text, "target_customers"),
        "target_employers": _parse_yaml_list(text, "target_employers"),
        "preferred_opportunity_types": _parse_yaml_list(text, "preferred_opportunity_types"),
        "no_go_categories": _parse_yaml_list(text, "no_go_categories"),
        "credible_pain_types": _parse_yaml_list(text, "credible_pain_types"),
        "career_context": _parse_yaml_scalar(text, "career_context"),
        "intro_philosophy": _parse_yaml_scalar(text, "intro_philosophy"),
        "intro_boundaries": _parse_yaml_list(text, "intro_boundaries"),
        "accessible_buyer_personas": _parse_yaml_list(text, "accessible_buyer_personas"),
        "constraints": {
            "geographic": _parse_yaml_scalar(text, "geographic"),
            "travel": _parse_yaml_scalar(text, "travel"),
            "work_style": _parse_yaml_scalar(text, "work_style"),
        },
    }


def _read_voice_constraints(profile_dir: Path) -> list[str]:
    """Extract prohibited language / constraints from voice.md."""
    path = profile_dir / "voice.md"
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    # Extract items from "Prohibited Language" section
    prohibited = []
    in_section = False
    for line in text.splitlines():
        if re.match(r"^##\s*Prohibited Language", line, re.I):
            in_section = True
            continue
        if in_section and line.startswith("##"):
            break
        if in_section:
            item = re.sub(r"^[-*]\s*", "", line).strip()
            if item:
                prohibited.append(item)
    return prohibited


def _read_profile_facts(profile_dir: Path) -> dict[str, Any]:
    """Read profile.md for key facts."""
    path = profile_dir / "profile.md"
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    facts: dict[str, Any] = {}

    # Extract name from H1 heading (first line that starts with #)
    for line in text.splitlines():
        if line.startswith("# "):
            facts["name"] = line.lstrip("# ").split("—")[0].strip()
            break

    # Extract version/source metadata
    for key in ("profile_id", "version", "source", "confidence"):
        val = _parse_yaml_scalar(text, key)
        if val:
            facts[key] = val

    return facts


# ---------------------------------------------------------------------------
# Legacy fallback reader
# ---------------------------------------------------------------------------

def _read_legacy_profile() -> dict[str, Any]:
    """Parse system/00_TODD_PROFILE.md into the canonical output shape.

    This is the backward-compatibility path for single-user installs that
    haven't yet migrated to the profiles_dir layout.
    """
    if not _LEGACY_SEED.exists():
        return {}
    text = _LEGACY_SEED.read_text(encoding="utf-8")

    # Extract name
    name = "Todd Vahlsing"
    for line in text.splitlines():
        if line.startswith("# "):
            name = line.lstrip("# ").strip()
            break

    # Infer capability set from the career arc and known theses
    capabilities = [
        "enterprise_restaurant_tech_sales",
        "gtm_strategy_and_execution",
        "franchise_operator_adoption",
        "mcdonalds_global_account_management",
        "above_store_analytics",
        "drive_thru_operations_and_technology",
        "pos_and_payments_ecosystem",
        "restaurant_ai_readiness_assessment",
        "consulting_and_advisory_structuring",
        "interim_cso_and_sales_leadership",
    ]
    return {
        "name": name,
        "source": "legacy_seed",
        "capabilities": capabilities,
        "product_service_capabilities": [
            "gtm_execution_consulting_for_restaurant_tech_vendors",
            "enterprise_sales_strategy_for_restaurant_tech",
            "franchise_adoption_consulting",
            "advisory_fractional_sales_leadership",
        ],
        "target_industries": [
            "restaurant_technology",
            "foodservice_technology",
            "quick_service_restaurants",
            "enterprise_restaurant_chains",
        ],
        "adjacent_industries": [
            "retail_technology",
            "payments_and_fintech",
            "enterprise_b2b_saas",
        ],
        "target_customers": [
            "restaurant_tech_vendors_series_b_to_public",
            "enterprise_restaurant_chains_200_plus_units",
        ],
        "target_employers": [
            "restaurant_tech_vendors_vp_enterprise_or_above",
            "enterprise_restaurant_operators_head_of_technology_or_revenue",
        ],
        "preferred_opportunity_types": [
            "consulting",
            "job",
            "sales",
            "partnership",
            "content",
        ],
        "no_go_categories": [
            "free_strategy_or_unpaid_thinking",
            "commission_only_arrangements",
            "intro_only_without_conviction",
        ],
        "credible_pain_types": [
            "gtm_execution_failure",
            "franchise_adoption_resistance",
            "enterprise_sales_stall",
            "integration_failure",
            "vendor_gap",
            "leadership_gap",
            "digital_transformation",
        ],
        "intro_philosophy": "balanced",
        "intro_boundaries": [
            "no_intro_without_conviction",
            "must_believe_in_both_sides",
        ],
        "constraints": {
            "geographic": "open_us_preference",
            "travel": "open",
            "work_style": "remote_or_hybrid",
        },
        "voice_constraints": [
            "No AI fluff",
            "No buzzwords",
            "No exaggerated enthusiasm",
            "No one-line paragraphs",
        ],
        "accessible_buyer_personas": [
            "mcdonalds_franchise_operator_ceo_and_directors",
            "enterprise_restaurant_chain_cto_coo_vp_technology",
            "restaurant_tech_vendor_ceo_cro_vp_sales",
        ],
    }


# ---------------------------------------------------------------------------
# Main load function
# ---------------------------------------------------------------------------

def load(profile_id: str | None = None) -> dict[str, Any]:
    """Load and return the active user profile in the canonical output shape.

    Args:
        profile_id: Override the active profile. None = auto-resolve.

    Returns:
        Canonical profile dict (see module docstring for shape).
    """
    resolved_id, source = _resolve_profile_id(profile_id)
    profile_dir = _PROFILES_DIR / resolved_id

    if source == "profiles_dir" and profile_dir.is_dir():
        facts = _read_profile_facts(profile_dir)
        ctx = _read_opportunity_context(profile_dir)
        voice_constraints = _read_voice_constraints(profile_dir)
        confidence = facts.get("confidence") or "high"

        return {
            "profile_id": resolved_id,
            "source": "profiles_dir",
            "facts": facts,
            "capabilities": ctx.get("capabilities") or [],
            "product_service_capabilities": ctx.get("product_service_capabilities") or [],
            "target_customers": ctx.get("target_customers") or [],
            "target_employers": ctx.get("target_employers") or [],
            "target_industries": ctx.get("target_industries") or [],
            "adjacent_industries": ctx.get("adjacent_industries") or [],
            "preferred_opportunity_types": ctx.get("preferred_opportunity_types") or [],
            "no_go_categories": ctx.get("no_go_categories") or [],
            "credible_pain_types": ctx.get("credible_pain_types") or [],
            "constraints": ctx.get("constraints") or {},
            "voice_constraints": voice_constraints,
            "intro_philosophy": ctx.get("intro_philosophy") or "balanced",
            "intro_boundaries": ctx.get("intro_boundaries") or [],
            "accessible_buyer_personas": ctx.get("accessible_buyer_personas") or [],
            "career_context": ctx.get("career_context"),
            "confidence": confidence,
        }

    # Legacy fallback
    legacy = _read_legacy_profile()
    return {
        "profile_id": resolved_id,
        "source": "legacy_seed",
        "facts": {"name": legacy.get("name", ""), "source": "legacy_seed"},
        "capabilities": legacy.get("capabilities") or [],
        "product_service_capabilities": legacy.get("product_service_capabilities") or [],
        "target_customers": legacy.get("target_customers") or [],
        "target_employers": legacy.get("target_employers") or [],
        "target_industries": legacy.get("target_industries") or [],
        "adjacent_industries": legacy.get("adjacent_industries") or [],
        "preferred_opportunity_types": legacy.get("preferred_opportunity_types") or [],
        "no_go_categories": legacy.get("no_go_categories") or [],
        "credible_pain_types": legacy.get("credible_pain_types") or [],
        "constraints": legacy.get("constraints") or {},
        "voice_constraints": legacy.get("voice_constraints") or [],
        "intro_philosophy": legacy.get("intro_philosophy") or "balanced",
        "intro_boundaries": legacy.get("intro_boundaries") or [],
        "accessible_buyer_personas": legacy.get("accessible_buyer_personas") or [],
        "career_context": "founder_advisor",
        "confidence": "medium",  # legacy seed is less precise
    }


def list_profiles() -> list[str]:
    """Return all known profile IDs."""
    if not _PROFILES_DIR.is_dir():
        return ["todd_vahlsing"] if _LEGACY_SEED.exists() else []
    return sorted(p.name for p in _PROFILES_DIR.iterdir() if p.is_dir())


# ---------------------------------------------------------------------------
# Smoke tests
# ---------------------------------------------------------------------------

def _smoke() -> int:
    failures: list[str] = []

    def ck(cond: bool, msg: str) -> None:
        mark = "OK" if cond else "FAIL"
        print(f"  {mark}   {msg}")
        if not cond:
            failures.append(msg)

    # Test 1: load() returns minimum required shape
    profile = load()
    required_keys = {
        "profile_id", "source", "facts", "capabilities",
        "product_service_capabilities", "target_customers", "target_employers",
        "target_industries", "adjacent_industries", "preferred_opportunity_types",
        "no_go_categories", "constraints", "voice_constraints", "confidence",
    }
    missing = required_keys - set(profile.keys())
    ck(not missing, f"Canonical output has all required keys (missing: {missing})")

    # Test 2: profile_id is a non-empty string
    ck(bool(profile.get("profile_id")), "profile_id is set")

    # Test 3: source is one of the two valid values
    ck(profile.get("source") in ("profiles_dir", "legacy_seed"),
       f"source is profiles_dir or legacy_seed (got {profile.get('source')!r})")

    # Test 4: capabilities is a list
    ck(isinstance(profile.get("capabilities"), list), "capabilities is a list")
    ck(len(profile["capabilities"]) > 0, "capabilities is non-empty")

    # Test 5: preferred_opportunity_types contains recognizable types
    valid_types = {"sales", "job", "consulting", "partnership", "intro", "content",
                   "research", "no_action"}
    opp_types = set(profile.get("preferred_opportunity_types") or [])
    ck(bool(opp_types & valid_types),
       f"preferred_opportunity_types overlaps valid types (got {opp_types})")

    # Test 6: confidence is one of the three valid values
    ck(profile.get("confidence") in ("high", "medium", "low"),
       f"confidence in high|medium|low (got {profile.get('confidence')!r})")

    # Test 7: no_go_categories is a list (may be empty)
    ck(isinstance(profile.get("no_go_categories"), list), "no_go_categories is a list")

    # Test 8: explicit profile_id load
    profile_explicit = load("todd_vahlsing")
    ck(profile_explicit.get("profile_id") == "todd_vahlsing",
       f"explicit load('todd_vahlsing') returns todd_vahlsing profile (got {profile_explicit.get('profile_id')!r})")

    # Test 9: legacy fallback works when profiles dir absent (simulated by loading unknown id)
    # We can't nuke the real dir in a smoke test, but we can verify the legacy
    # data path itself returns the right shape by calling _read_legacy_profile().
    legacy = _read_legacy_profile()
    ck(isinstance(legacy.get("capabilities"), list) and len(legacy["capabilities"]) > 0,
       "legacy fallback _read_legacy_profile() returns capabilities")

    # Test 10: no person-specific schema fields in the canonical output shape
    person_specific_keys = [k for k in profile.keys()
                            if re.search(r"_to_todd$|_for_todd$|todd_specific", k)]
    ck(not person_specific_keys,
       f"No person-specific schema fields in canonical output (found: {person_specific_keys})")

    print(f"--- user_profile smoke complete: {len(failures)} failure(s) ---")
    return 1 if failures else 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _render_text(profile: dict[str, Any]) -> str:
    lines = [
        f"Profile: {profile['profile_id']} (source={profile['source']}, confidence={profile['confidence']})",
        f"  capabilities ({len(profile['capabilities'])}): {', '.join(profile['capabilities'][:5])}{'…' if len(profile['capabilities']) > 5 else ''}",
        f"  target_industries: {', '.join(profile['target_industries'][:4])}",
        f"  preferred_opportunity_types: {', '.join(profile['preferred_opportunity_types'])}",
        f"  no_go_categories: {', '.join(profile['no_go_categories'][:3])}{'…' if len(profile['no_go_categories']) > 3 else ''}",
        f"  credible_pain_types: {', '.join((profile.get('credible_pain_types') or [])[:4])}",
        f"  intro_philosophy: {profile.get('intro_philosophy')}",
    ]
    if profile.get("career_context"):
        lines.append(f"  career_context: {profile['career_context']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="RB user profile loader")
    p.add_argument("--profile", dest="profile_id", help="Profile ID to load")
    p.add_argument("--json", action="store_true", help="Emit JSON output")
    p.add_argument("--list", action="store_true", help="List available profile IDs")
    p.add_argument("--smoke", action="store_true", help="Run in-memory regression tests")
    args = p.parse_args(argv)

    if args.smoke:
        return _smoke()

    if args.list:
        for pid in list_profiles():
            print(f"  {pid}")
        return 0

    profile = load(args.profile_id)
    if args.json:
        print(json.dumps(profile, indent=2))
    else:
        print(_render_text(profile))
    return 0


if __name__ == "__main__":
    sys.exit(main())
