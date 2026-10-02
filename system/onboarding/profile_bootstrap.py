#!/usr/bin/env python3
"""profile_bootstrap.py — first-run user profile creation for RB.

Creates the canonical profile directory layout under system/profiles/{profile_id}/
from user input. Two modes:

  Interactive CLI:
      python3 profile_bootstrap.py --interactive

  From filled-in intake template:
      python3 profile_bootstrap.py --from-template profile_intake_template.md
                                   --profile-id jane_smith

  Dry run (preview only, no writes):
      python3 profile_bootstrap.py --interactive --dry-run

The bootstrap collects:
  - Identity and current context
  - Career / business goals
  - Industries and adjacent markets
  - Products / services / capabilities
  - Target customers and target employers
  - Pain the user can credibly solve
  - Opportunity types to surface
  - Communication preferences
  - Relationship and intro boundaries
  - Privacy and conflict constraints

Output files (per PROFILE_NAMING_CONVENTION.md):
    system/profiles/{profile_id}/profile.md
    system/profiles/{profile_id}/preferences.yaml
    system/profiles/{profile_id}/opportunity_context.yaml
    system/profiles/{profile_id}/voice.md
    system/profiles/{profile_id}/boundaries.md
    system/profiles/{profile_id}/learning_log.jsonl
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
_SCRIPTS_DIR = _HERE.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS_DIR))

try:
    import rb_core as core  # noqa: E402
    _SYSTEM_DIR = core.SYSTEM_DIR
except Exception:
    _SYSTEM_DIR = _HERE.parent

_PROFILES_DIR = _SYSTEM_DIR / "profiles"
_SETTINGS_PATH = _SYSTEM_DIR / "settings.json"
_TODAY = date.today().isoformat()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        val = input(f"{prompt}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return default
    return val or default


def _ask_list(prompt: str, examples: str = "") -> list[str]:
    """Ask the user to enter a comma or newline-separated list of items."""
    hint = f" (comma-separated{', e.g. ' + examples if examples else ''})"
    print(f"{prompt}{hint}")
    try:
        raw = input("> ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return []
    if not raw:
        return []
    return [item.strip() for item in re.split(r"[,\n]+", raw) if item.strip()]


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "unknown"


# ---------------------------------------------------------------------------
# Template parser (reads a filled-in profile_intake_template.md)
# ---------------------------------------------------------------------------

def _extract_yaml_block(text: str, section_heading: str) -> str:
    """Extract the first YAML code block after a matching heading."""
    pattern = re.compile(
        r"#{1,4}\s+" + re.escape(section_heading) + r".*?\n"
        r"(?:.*?\n)*?"
        r"```yaml\n(.*?)```",
        re.DOTALL | re.IGNORECASE,
    )
    m = pattern.search(text)
    return m.group(1) if m else ""


def _parse_yaml_scalar_block(block: str, key: str) -> str | None:
    pattern = re.compile(r"^" + re.escape(key) + r"\s*:\s*(.+)$", re.MULTILINE)
    m = pattern.search(block)
    if not m:
        return None
    val = m.group(1).strip().strip("\"'")
    if val.lower() == "null" or not val:
        return None
    return val


def _parse_yaml_list_block(block: str, key: str) -> list[str]:
    pattern = re.compile(
        r"^" + re.escape(key) + r"\s*:\s*\n((?:[ \t]*-[^\n]*\n)*)",
        re.MULTILINE,
    )
    m = pattern.search(block)
    if not m:
        return []
    items = []
    for line in m.group(1).splitlines():
        item = re.sub(r"^\s*-\s*", "", line).strip()
        if item and item.lower() != "null":
            items.append(item)
    return items


def parse_template(path: Path) -> dict[str, Any]:
    """Parse a filled-in profile_intake_template.md into a data dict."""
    text = path.read_text(encoding="utf-8")

    identity_block = _extract_yaml_block(text, "1. Identity")
    career_block = _extract_yaml_block(text, "2. Career / Business Context")
    goals_block = _extract_yaml_block(text, "3. Relationship Goals")
    caps_block = _extract_yaml_block(text, "4. Core Capabilities")
    ps_caps_block = _extract_yaml_block(text, "5. Product / Service Capabilities")
    industries_block = _extract_yaml_block(text, "6. Industries and Adjacent Markets")
    targets_block = _extract_yaml_block(text, "7. Target Customers and Target Employers")
    pain_block = _extract_yaml_block(text, "8. Pain You Can Credibly Solve")
    opp_block = _extract_yaml_block(text, "9. Opportunity Types You Want Surfaced")
    comms_block = _extract_yaml_block(text, "10. Communication Preferences")
    boundaries_block = _extract_yaml_block(text, "11. Relationship and Intro Boundaries")
    privacy_block = _extract_yaml_block(text, "12. Privacy and Conflict Constraints")
    constraints_block = _extract_yaml_block(text, "13. Constraints")

    return {
        "name": _parse_yaml_scalar_block(identity_block, "name"),
        "preferred_name": _parse_yaml_scalar_block(identity_block, "preferred_name"),
        "current_role": _parse_yaml_scalar_block(identity_block, "current_role"),
        "current_company": _parse_yaml_scalar_block(identity_block, "current_company"),
        "location": _parse_yaml_scalar_block(identity_block, "location"),
        "linkedin_url": _parse_yaml_scalar_block(identity_block, "linkedin_url"),
        "email": _parse_yaml_scalar_block(identity_block, "email"),
        "career_context": _parse_yaml_scalar_block(career_block, "career_context"),
        "industry": _parse_yaml_scalar_block(career_block, "industry"),
        "sub_industry": _parse_yaml_scalar_block(career_block, "sub_industry"),
        "job_search_context": _parse_yaml_scalar_block(career_block, "job_search_context"),
        "relationship_goals": _parse_yaml_list_block(goals_block, "relationship_goals"),
        "capabilities": _parse_yaml_list_block(caps_block, "capabilities"),
        "product_service_capabilities": _parse_yaml_list_block(ps_caps_block, "product_service_capabilities"),
        "target_industries": _parse_yaml_list_block(industries_block, "target_industries"),
        "adjacent_industries": _parse_yaml_list_block(industries_block, "adjacent_industries"),
        "target_customers": _parse_yaml_list_block(targets_block, "target_customers"),
        "target_employers": _parse_yaml_list_block(targets_block, "target_employers"),
        "credible_pain_types": _parse_yaml_list_block(pain_block, "credible_pain_types"),
        "preferred_opportunity_types": _parse_yaml_list_block(opp_block, "preferred_opportunity_types"),
        "no_go_categories": _parse_yaml_list_block(opp_block, "no_go_categories"),
        "tone_style": _parse_yaml_scalar_block(comms_block, "tone_style"),
        "prohibited_language": _parse_yaml_list_block(comms_block, "prohibited_language"),
        "response_mode": _parse_yaml_scalar_block(comms_block, "response_mode") or "expert",
        "verbosity": _parse_yaml_scalar_block(comms_block, "verbosity") or "concise",
        "intro_philosophy": _parse_yaml_scalar_block(boundaries_block, "intro_philosophy") or "balanced",
        "intro_boundaries": _parse_yaml_list_block(boundaries_block, "intro_boundaries"),
        "engagement_no_gos": _parse_yaml_list_block(boundaries_block, "engagement_no_gos"),
        "confidential_relationships": _parse_yaml_list_block(privacy_block, "confidential_relationships"),
        "conflict_constraints": _parse_yaml_list_block(privacy_block, "conflict_constraints"),
        "geographic": _parse_yaml_scalar_block(constraints_block, "geographic") or "open",
        "travel": _parse_yaml_scalar_block(constraints_block, "travel") or "open",
        "work_style": _parse_yaml_scalar_block(constraints_block, "work_style") or "no_preference",
    }


# ---------------------------------------------------------------------------
# Interactive collection
# ---------------------------------------------------------------------------

def collect_interactive() -> dict[str, Any]:
    """Prompt the user for profile information interactively."""
    print("\n=== RB Profile Bootstrap — First-Run Setup ===")
    print("RB cannot score opportunity fit without knowing you.")
    print("Answer each question or press Enter to skip.\n")

    data: dict[str, Any] = {}

    print("--- Identity ---")
    data["name"] = _ask("Full name")
    data["preferred_name"] = _ask("Preferred name", data["name"].split()[0] if data["name"] else "")
    data["current_role"] = _ask("Current role / title")
    data["current_company"] = _ask("Current employer or company (use 'Independent' if self-employed)")
    data["location"] = _ask("Location (city, state)")
    data["linkedin_url"] = _ask("LinkedIn URL (optional)")
    data["email"] = _ask("Email address")

    print("\n--- Career / Business Context ---")
    print("Career context options: employed, consulting, job_search, founder, operator, advisor, investor, other")
    data["career_context"] = _ask("Career context", "consulting")
    data["industry"] = _ask("Primary industry")
    data["sub_industry"] = _ask("Sub-industry (optional)")

    if "job_search" in data.get("career_context", ""):
        data["job_search_context"] = _ask("What kind of role are you looking for?")

    print("\n--- Capabilities ---")
    print("What can you personally deliver? (Be specific — drives opportunity scoring)")
    data["capabilities"] = _ask_list(
        "Core capabilities",
        "enterprise_saas_sales, restaurant_tech_consulting, fractional_cto",
    )

    print("\n--- Product / Service ---")
    data["product_service_capabilities"] = _ask_list(
        "What does your practice or offering deliver?",
        "gtm_strategy_consulting, fractional_sales_leadership",
    )

    print("\n--- Industries ---")
    data["target_industries"] = _ask_list(
        "Target industries",
        "restaurant_technology, quick_service_restaurants",
    )
    data["adjacent_industries"] = _ask_list(
        "Adjacent industries (optional)",
        "payments_fintech, retail_technology",
    )

    print("\n--- Target Customers and Employers ---")
    data["target_customers"] = _ask_list(
        "Target customers (for consulting / advisory)",
        "restaurant_tech_vendors_series_b_to_public",
    )
    data["target_employers"] = _ask_list(
        "Target employers (for full-time or fractional roles)",
        "restaurant_tech_vendors_vp_enterprise_or_above",
    )

    print("\n--- Pain You Can Solve ---")
    print("Pain types: gtm_execution_failure, franchise_adoption_resistance, enterprise_sales_stall,")
    print("            integration_failure, vendor_gap, leadership_gap, digital_transformation, other")
    data["credible_pain_types"] = _ask_list(
        "Pain you can credibly solve",
        "gtm_execution_failure, leadership_gap",
    )

    print("\n--- Opportunity Types ---")
    print("Types: sales, job, consulting, partnership, intro, content, research")
    data["preferred_opportunity_types"] = _ask_list(
        "Opportunity types to surface",
        "consulting, job, sales",
    )
    data["no_go_categories"] = _ask_list(
        "Things you do NOT want surfaced",
        "free_strategy_or_unpaid_thinking, commission_only_arrangements",
    )

    print("\n--- Communication Preferences ---")
    data["tone_style"] = _ask("Describe your communication style in plain English",
                               "direct, warm, practical, no buzzwords")
    data["prohibited_language"] = _ask_list(
        "Words / phrases to NEVER put in drafts",
        "synergy, leverage, cutting-edge, game-changing",
    )
    data["response_mode"] = _ask("RB response mode (expert/intermediate/beginner)", "expert")
    data["verbosity"] = _ask("Verbosity (concise/normal/detailed)", "concise")

    print("\n--- Intro and Engagement Boundaries ---")
    data["intro_philosophy"] = _ask("Intro philosophy (conservative/balanced/aggressive)", "balanced")
    data["intro_boundaries"] = _ask_list(
        "Hard rules for introductions",
        "no_intro_without_conviction, must_believe_in_both_sides",
    )
    data["engagement_no_gos"] = _ask_list(
        "Engagement models you will not accept",
        "free_strategy, commission_only",
    )

    print("\n--- Privacy ---")
    data["confidential_relationships"] = _ask_list(
        "Employers or clients that should stay confidential (optional)",
    )
    data["conflict_constraints"] = _ask_list(
        "Competitive conflicts to be aware of (optional)",
    )

    print("\n--- Constraints ---")
    data["geographic"] = _ask("Geographic preference (open/us_only/region)", "open")
    data["travel"] = _ask("Travel availability (open/limited/none)", "open")
    data["work_style"] = _ask("Work style (remote/hybrid/in_person/no_preference)", "no_preference")

    return data


# ---------------------------------------------------------------------------
# File generators
# ---------------------------------------------------------------------------

def _gen_profile_md(data: dict[str, Any], profile_id: str) -> str:
    name = data.get("name") or profile_id
    role = data.get("current_role") or "Unknown"
    company = data.get("current_company") or "Unknown"
    location = data.get("location") or ""
    linkedin = data.get("linkedin_url") or ""
    email = data.get("email") or ""
    career_context = data.get("career_context") or "unknown"
    industry = data.get("industry") or ""

    lines = [
        f"# {name} — Profile",
        "",
        f"profile_id: {profile_id}",
        "version: 1",
        f"source: bootstrap_{_TODAY}",
        "confidence: high",
        "",
        "## Identity",
        "",
    ]
    if location:
        lines.append(f"- {location}")
    if email:
        lines.append(f"- {email}")
    if linkedin:
        lines.append(f"- LinkedIn: {linkedin}")
    lines.extend([
        f"- Current role: {role} at {company}",
        "",
        "## Career Context",
        "",
        f"Career context: {career_context}",
    ])
    if industry:
        lines.append(f"Industry: {industry}")
    if data.get("sub_industry"):
        lines.append(f"Sub-industry: {data['sub_industry']}")
    if data.get("job_search_context"):
        lines.extend(["", "## Job Search Context", "", data["job_search_context"]])

    if data.get("capabilities"):
        lines.extend(["", "## Core Capabilities", ""])
        for cap in data["capabilities"]:
            lines.append(f"- {cap}")

    lines.extend(["", "## Current Operating Context", "",
                  f"Running as: {career_context}. Profile created {_TODAY}."])
    return "\n".join(lines) + "\n"


def _gen_preferences_yaml(data: dict[str, Any], profile_id: str) -> str:
    response_mode = data.get("response_mode") or "expert"
    verbosity = data.get("verbosity") or "concise"
    intro_philosophy = data.get("intro_philosophy") or "balanced"

    lines = [
        f"# {profile_id} — preferences.yaml",
        f"# Created: {_TODAY}",
        "",
        f"profile_id: {profile_id}",
        "",
        f"response_mode: {response_mode}",
        f"verbosity: {verbosity}",
        "time_horizon: this_week",
        "",
        f"intro_philosophy: {intro_philosophy}",
    ]

    if data.get("intro_boundaries"):
        lines.append("intro_constraints:")
        for b in data["intro_boundaries"]:
            lines.append(f"  - {b}")
    if data.get("engagement_no_gos"):
        lines.append("engagement_no_gos:")
        for n in data["engagement_no_gos"]:
            lines.append(f"  - {n}")

    lines.extend([
        "",
        "default_workflows:",
        "  daily_brief: enabled",
        "  opportunity_sensing: enabled",
        "  market_signals: enabled",
        "  relationship_signals: enabled",
    ])
    return "\n".join(lines) + "\n"


def _gen_opportunity_context_yaml(data: dict[str, Any], profile_id: str) -> str:
    def _list_block(key: str, items: list[str]) -> list[str]:
        if not items:
            return [f"{key}: []"]
        out = [f"{key}:"]
        for item in items:
            out.append(f"  - {item}")
        return out

    lines = [
        f"# {profile_id} — opportunity_context.yaml",
        f"# Created: {_TODAY}",
        "",
        f"profile_id: {profile_id}",
        "",
        f"career_context: {data.get('career_context') or 'unknown'}",
        "",
    ]
    lines += _list_block("capabilities", data.get("capabilities") or [])
    lines.append("")
    lines += _list_block("product_service_capabilities",
                         data.get("product_service_capabilities") or [])
    lines.append("")
    lines += _list_block("target_industries", data.get("target_industries") or [])
    lines.append("")
    lines += _list_block("adjacent_industries", data.get("adjacent_industries") or [])
    lines.append("")
    lines += _list_block("target_customers", data.get("target_customers") or [])
    lines.append("")
    lines += _list_block("target_employers", data.get("target_employers") or [])
    lines.append("")
    lines += _list_block("preferred_opportunity_types",
                         data.get("preferred_opportunity_types") or [])
    lines.append("")
    lines += _list_block("no_go_categories", data.get("no_go_categories") or [])
    lines.append("")
    lines += _list_block("credible_pain_types", data.get("credible_pain_types") or [])
    lines.append("")
    intro = data.get("intro_philosophy") or "balanced"
    lines.append(f"intro_philosophy: {intro}")
    lines.append("")
    lines += _list_block("intro_boundaries", data.get("intro_boundaries") or [])
    lines.extend([
        "",
        "constraints:",
        f"  geographic: {data.get('geographic') or 'open'}",
        f"  travel: {data.get('travel') or 'open'}",
        f"  work_style: {data.get('work_style') or 'no_preference'}",
    ])
    if data.get("job_search_context"):
        lines.extend(["", "job_search_active: true",
                      f"job_search_context: \"{data['job_search_context']}\""])
    else:
        lines.extend(["", "job_search_active: false", "job_search_context: null"])
    return "\n".join(lines) + "\n"


def _gen_voice_md(data: dict[str, Any], profile_id: str) -> str:
    tone = data.get("tone_style") or "direct, warm, practical"
    prohibited = data.get("prohibited_language") or []
    name = data.get("name") or profile_id

    lines = [
        f"# {name} — voice.md",
        f"# Created: {_TODAY}",
        "",
        f"profile_id: {profile_id}",
        "",
        "## Core Voice",
        "",
        tone,
        "",
        "## Prohibited Language",
        "",
    ]
    for item in prohibited:
        lines.append(f"- {item}")
    if not prohibited:
        lines.append("- (none specified)")
    lines.extend([
        "",
        "## Email and LinkedIn DM Style",
        "",
        "- Open with context, not a pitch",
        "- Specific ask, not open-ended fishing",
        "- Short and direct",
    ])
    return "\n".join(lines) + "\n"


def _gen_boundaries_md(data: dict[str, Any], profile_id: str) -> str:
    name = data.get("name") or profile_id
    engagement_no_gos = data.get("engagement_no_gos") or []
    intro_boundaries = data.get("intro_boundaries") or []
    conflict_constraints = data.get("conflict_constraints") or []
    confidential = data.get("confidential_relationships") or []

    lines = [
        f"# {name} — boundaries.md",
        f"# Created: {_TODAY}",
        "",
        f"profile_id: {profile_id}",
        "",
        "## Engagement Rules",
        "",
    ]
    if engagement_no_gos:
        lines.append("Will not accept:")
        for n in engagement_no_gos:
            lines.append(f"- {n}")
    else:
        lines.append("(No engagement no-gos specified.)")

    lines.extend(["", "## Intro Boundaries", ""])
    if intro_boundaries:
        for b in intro_boundaries:
            lines.append(f"- {b}")
    else:
        lines.append("(No intro boundaries specified.)")

    lines.extend(["", "## Conflict Rules", ""])
    if conflict_constraints:
        for c in conflict_constraints:
            lines.append(f"- {c}")
    else:
        lines.append("(No conflict constraints specified.)")

    lines.extend(["", "## Privacy Rules", ""])
    if confidential:
        lines.append("Confidential relationships / clients:")
        for c in confidential:
            lines.append(f"- {c}")
    else:
        lines.append("(No confidential relationships specified.)")

    return "\n".join(lines) + "\n"


def _gen_learning_log_entry(profile_id: str, source: str) -> str:
    entry = {
        "date": _TODAY,
        "type": "profile_created",
        "note": f"Profile created via bootstrap ({source}). All fields are user-stated, not inferred.",
        "confidence": "high",
        "source": source,
    }
    return json.dumps(entry)


# ---------------------------------------------------------------------------
# Write profile
# ---------------------------------------------------------------------------

def write_profile(
    profile_id: str,
    data: dict[str, Any],
    *,
    dry_run: bool = False,
    source_label: str = "interactive_bootstrap",
) -> dict[str, Any]:
    """Write all profile files to system/profiles/{profile_id}/.

    Returns a summary of what was written (or would be written in dry_run mode).
    """
    profile_dir = _PROFILES_DIR / profile_id
    files_to_write = {
        profile_dir / "profile.md": _gen_profile_md(data, profile_id),
        profile_dir / "preferences.yaml": _gen_preferences_yaml(data, profile_id),
        profile_dir / "opportunity_context.yaml": _gen_opportunity_context_yaml(data, profile_id),
        profile_dir / "voice.md": _gen_voice_md(data, profile_id),
        profile_dir / "boundaries.md": _gen_boundaries_md(data, profile_id),
        profile_dir / "learning_log.jsonl": _gen_learning_log_entry(profile_id, source_label) + "\n",
    }

    written: list[str] = []
    skipped: list[str] = []

    if not dry_run:
        profile_dir.mkdir(parents=True, exist_ok=True)

    for path, content in files_to_write.items():
        rel = str(path.relative_to(_SYSTEM_DIR.parent)) if _SYSTEM_DIR.parent in path.parents else str(path)
        if dry_run:
            print(f"  [dry-run] would write: {rel} ({len(content)} chars)")
            written.append(rel)
        else:
            path.write_text(content, encoding="utf-8")
            print(f"  wrote: {rel}")
            written.append(rel)

    return {
        "profile_id": profile_id,
        "profile_dir": str(profile_dir),
        "written": written,
        "skipped": skipped,
        "dry_run": dry_run,
    }


def confirm_and_write(
    profile_id: str,
    data: dict[str, Any],
    *,
    dry_run: bool = False,
    source_label: str = "interactive_bootstrap",
) -> dict[str, Any]:
    """Preview what will be written, ask for confirmation, then write."""
    print(f"\n=== Profile Preview: {profile_id} ===")
    print(f"  name: {data.get('name')}")
    print(f"  role: {data.get('current_role')} at {data.get('current_company')}")
    print(f"  career_context: {data.get('career_context')}")
    print(f"  capabilities ({len(data.get('capabilities') or [])}): "
          f"{', '.join((data.get('capabilities') or [])[:3])}{'…' if len(data.get('capabilities') or []) > 3 else ''}")
    print(f"  preferred_opportunity_types: {', '.join(data.get('preferred_opportunity_types') or [])}")
    print(f"  intro_philosophy: {data.get('intro_philosophy')}")
    print(f"\n  Will write to: system/profiles/{profile_id}/")

    if dry_run:
        return write_profile(profile_id, data, dry_run=True, source_label=source_label)

    try:
        confirm = input("\nWrite these files? [y/N]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\nAborted.")
        sys.exit(0)

    if confirm != "y":
        print("Aborted. No files written.")
        return {"profile_id": profile_id, "written": [], "skipped": [], "dry_run": False}

    return write_profile(profile_id, data, dry_run=False, source_label=source_label)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="RB first-run profile bootstrap")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--interactive", action="store_true",
                      help="Collect profile via interactive prompts")
    mode.add_argument("--from-template", dest="template_path",
                      help="Read a filled-in profile_intake_template.md")
    p.add_argument("--profile-id", dest="profile_id",
                   help="Profile ID to create (e.g. jane_smith). "
                        "Defaults to slug of name field if not given.")
    p.add_argument("--dry-run", action="store_true",
                   help="Preview only — do not write any files")
    p.add_argument("--json", action="store_true",
                   help="Emit result as JSON instead of human-readable output")
    args = p.parse_args(argv)

    if args.template_path:
        template_path = Path(args.template_path)
        if not template_path.exists():
            print(f"ERROR: template file not found: {template_path}", file=sys.stderr)
            return 1
        data = parse_template(template_path)
        source_label = f"template:{template_path.name}"
    elif args.interactive:
        data = collect_interactive()
        source_label = "interactive_bootstrap"
    else:
        p.print_help()
        print("\nHint: run with --interactive or --from-template path/to/template.md")
        return 0

    # Derive profile_id
    profile_id = args.profile_id
    if not profile_id:
        name = data.get("name") or ""
        if name:
            profile_id = _slug(name)
        else:
            profile_id = "unknown_profile"

    result = confirm_and_write(
        profile_id, data, dry_run=args.dry_run, source_label=source_label
    )

    if args.json:
        print(json.dumps(result, indent=2))
    elif not args.dry_run and result.get("written"):
        print(f"\nProfile '{profile_id}' created successfully.")
        print(f"Load it with: python3 system/scripts/user_profile.py --profile {profile_id}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
