#!/usr/bin/env python3
"""life_goals_onboarding.py — interactive onboarding to configure life goals for the RB daily brief.

Guides any user through defining their life domains, goals, targets, and tracking
methods. Writes life_goals.yaml — the config file the daily brief reads to render
the Life Lens section.

Usage:
    python3 life_goals_onboarding.py                  # full onboarding
    python3 life_goals_onboarding.py --edit           # re-run with existing answers pre-filled
    python3 life_goals_onboarding.py --show           # display current config
    python3 life_goals_onboarding.py --add-goal       # add a single goal interactively
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rb_core as core

try:
    import yaml
except ImportError:
    print("ERROR: pyyaml not installed. Run: pip3 install pyyaml")
    sys.exit(1)

GOALS_PATH = core.SYSTEM_DIR / "life_goals.yaml"

# ---------------------------------------------------------------------------
# Default domain catalogue — user picks which apply, customises targets
# ---------------------------------------------------------------------------

DOMAIN_CATALOGUE = [
    {
        "id": "health",
        "label": "Health & Fitness",
        "description": "Exercise, sleep, nutrition, medical",
        "default_goals": [
            {
                "id": "exercise",
                "label": "Exercise",
                "target_unit": "days/week",
                "default_target": 5,
                "data_source": "strava",
                "alert_if_missed_days": 2,
                "strava_activity_types": ["Run", "Walk", "Ride", "Workout", "WeightTraining", "Hike"],
                "brief_icon": "🏃",
            },
        ],
    },
    {
        "id": "spiritual",
        "label": "Faith & Spiritual Life",
        "description": "Prayer, devotions, Scripture, church",
        "default_goals": [
            {
                "id": "prayer",
                "label": "Morning Prayer",
                "target_unit": "days/week",
                "default_target": 7,
                "data_source": "manual",
                "alert_if_missed_days": 1,
                "brief_icon": "🙏",
            },
            {
                "id": "devotions",
                "label": "Devotions / Scripture",
                "target_unit": "days/week",
                "default_target": 7,
                "data_source": "manual",
                "alert_if_missed_days": 2,
                "brief_icon": "📖",
            },
        ],
    },
    {
        "id": "family",
        "label": "Family & Marriage",
        "description": "Spouse connection, date nights, family time",
        "default_goals": [
            {
                "id": "date_night",
                "label": "Date Night",
                "target_unit": "per week",
                "default_target": 1,
                "data_source": "calendar",
                "calendar_keywords": ["date night", "dinner with", "just us"],
                "alert_if_gap_days": 10,
                "brief_icon": "❤️",
            },
            {
                "id": "spouse_connection",
                "label": "Daily connection with spouse",
                "target_unit": "days/week",
                "default_target": 7,
                "data_source": "manual",
                "alert_if_missed_days": 2,
                "brief_icon": "👫",
            },
        ],
    },
    {
        "id": "church",
        "label": "Church & Ministry",
        "description": "Church attendance, ministry roles, community",
        "default_goals": [
            {
                "id": "church_attendance",
                "label": "Church Attendance",
                "target_unit": "per week",
                "default_target": 1,
                "data_source": "calendar",
                "calendar_keywords": ["church", "service", "worship"],
                "alert_if_gap_days": 14,
                "brief_icon": "⛪",
            },
        ],
    },
    {
        "id": "projects",
        "label": "Projects (Home / Cabin / Personal)",
        "description": "Home improvement, cabin, personal builds",
        "default_goals": [
            {
                "id": "project_progress",
                "label": "Project work session",
                "target_unit": "per week",
                "default_target": 2,
                "data_source": "manual",
                "alert_if_missed_days": 7,
                "brief_icon": "🔨",
            },
        ],
    },
    {
        "id": "recreation",
        "label": "Rest & Recreation",
        "description": "Fun, hobbies, downtime — things to look forward to",
        "default_goals": [
            {
                "id": "recreation_event",
                "label": "Planned recreation/fun",
                "target_unit": "per week",
                "default_target": 1,
                "data_source": "calendar",
                "calendar_keywords": ["golf", "fishing", "cabin", "vacation", "game", "concert", "fun"],
                "alert_if_gap_days": 14,
                "brief_icon": "🎯",
            },
        ],
    },
    {
        "id": "author",
        "label": "Author / Writing",
        "description": "Books, articles, manuscripts in progress",
        "default_goals": [
            {
                "id": "writing_session",
                "label": "Writing session",
                "target_unit": "per week",
                "default_target": 3,
                "data_source": "manual",
                "alert_if_missed_days": 3,
                "brief_icon": "✍️",
            },
        ],
    },
    {
        "id": "learning",
        "label": "Learning & Development",
        "description": "Books, courses, podcasts, intentional learning",
        "default_goals": [
            {
                "id": "learning_session",
                "label": "Learning session",
                "target_unit": "per week",
                "default_target": 3,
                "data_source": "manual",
                "alert_if_missed_days": 4,
                "brief_icon": "📚",
            },
        ],
    },
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _prompt(question: str, default: str = "") -> str:
    if default:
        answer = input(f"{question} [{default}]: ").strip()
        return answer if answer else default
    return input(f"{question}: ").strip()


def _prompt_int(question: str, default: int) -> int:
    raw = _prompt(question, str(default))
    try:
        return int(raw)
    except ValueError:
        return default


def _prompt_yn(question: str, default: bool = True) -> bool:
    suffix = " [Y/n]" if default else " [y/N]"
    raw = input(f"{question}{suffix}: ").strip().lower()
    if not raw:
        return default
    return raw.startswith("y")


def _print_section(title: str) -> None:
    print(f"\n{'─' * 60}")
    print(f"  {title}")
    print('─' * 60)


# ---------------------------------------------------------------------------
# Onboarding flow
# ---------------------------------------------------------------------------

def run_onboarding(edit_mode: bool = False) -> dict:
    existing: dict = {}
    if edit_mode and GOALS_PATH.exists():
        existing = yaml.safe_load(GOALS_PATH.read_text()) or {}

    print("\n" + "=" * 60)
    print("  RB Life Goals Onboarding")
    print("  Configure your life domains, goals, and targets.")
    print("  This powers the Life Lens section of your daily brief.")
    print("=" * 60)
    print()
    print("RB tracks goals across life domains and surfaces progress,")
    print("streaks, and alerts in your morning brief — the same way a")
    print("world-class CoS would remind you to not let the urgent crowd")
    print("out the important.")
    print()

    # --- User identity ---
    _print_section("1. About You")
    user_name = _prompt("Your first name", existing.get("user_name", ""))
    spouse_name = _prompt("Spouse/partner name (leave blank if not applicable)",
                          existing.get("spouse_name", ""))

    # --- Domain selection ---
    _print_section("2. Your Life Domains")
    print("Select the domains you want to track in your daily brief.")
    print("You can always add more later with --add-goal.\n")

    enabled_domains: list[dict] = []

    for domain in DOMAIN_CATALOGUE:
        existing_domain = next((d for d in existing.get("domains", [])
                                if d["id"] == domain["id"]), None)
        default_enabled = bool(existing_domain)
        enable = _prompt_yn(f"  Track {domain['label']} ({domain['description']})?",
                            default_enabled)
        if not enable:
            continue

        # --- Goals within domain ---
        configured_goals: list[dict] = []
        for goal_template in domain["default_goals"]:
            ex_goal = next((g for g in (existing_domain or {}).get("goals", [])
                            if g["id"] == goal_template["id"]), None)

            print(f"\n    Goal: {goal_template['label']}")

            # Personalise label
            label = goal_template["label"]
            if domain["id"] == "family" and goal_template["id"] == "date_night" and spouse_name:
                label = f"Date Night with {spouse_name}"
            if domain["id"] == "family" and goal_template["id"] == "spouse_connection" and spouse_name:
                label = f"Daily connection with {spouse_name}"

            # Target
            current_target = (ex_goal or {}).get("target", goal_template["default_target"])
            target = _prompt_int(
                f"    Target ({goal_template['target_unit']})",
                current_target
            )

            goal = {k: v for k, v in goal_template.items()
                    if k not in ("default_target", "target_unit")}
            goal["label"] = label
            goal["target"] = target
            goal["target_unit"] = goal_template["target_unit"]
            if goal.get("data_source") == "manual":
                goal["enabled"] = True
            configured_goals.append(goal)

        # Allow user to add a custom goal in this domain
        while True:
            add_custom = _prompt_yn(
                f"\n  Add a custom goal under {domain['label']}?", False)
            if not add_custom:
                break
            custom_id = _prompt("    Goal ID (e.g. 'journaling', 'sleep')").lower().replace(" ", "_")
            custom_label = _prompt("    Goal label (shown in brief)")
            custom_target = _prompt_int("    Target (number)", 1)
            custom_unit = _prompt("    Unit (e.g. 'days/week', 'per week')", "days/week")
            custom_source = _prompt("    Data source (manual / calendar / strava)", "manual")
            custom_icon = _prompt("    Icon (emoji)", "⭐")
            configured_goals.append({
                "id": custom_id,
                "label": custom_label,
                "target": custom_target,
                "target_unit": custom_unit,
                "data_source": custom_source,
                "alert_if_missed_days": 2,
                "brief_icon": custom_icon,
                "enabled": True,
            })

        enabled_domains.append({
            "id": domain["id"],
            "label": domain["label"],
            "goals": configured_goals,
        })

    # --- Projects ---
    _print_section("3. Projects (optional)")
    projects: list[dict] = existing.get("personal_projects", [])
    print("List your active personal projects (home, cabin, writing, etc.)")
    print("Leave blank to skip.\n")
    if not projects:
        while True:
            project_name = _prompt("  Project name (blank to finish)", "")
            if not project_name:
                break
            project_status = _prompt("  Current status / next action", "")
            projects.append({
                "name": project_name,
                "status": project_status,
                "last_updated": date.today().isoformat(),
            })
    else:
        keep = _prompt_yn(f"  You have {len(projects)} existing projects. Keep them?", True)
        if not keep:
            projects = []

    # --- Books / Author ---
    _print_section("4. Books & Writing Projects (optional)")
    books: list[dict] = existing.get("author_projects", [])
    if not books:
        has_books = _prompt_yn("  Are you working on any books or major writing projects?", False)
        if has_books:
            while True:
                title = _prompt("  Book / project title (blank to finish)", "")
                if not title:
                    break
                current_chapter = _prompt("  Where are you? (e.g. 'Chapter 4 of 12', 'First draft')", "")
                goal_desc = _prompt("  Completion goal (e.g. 'Finish Ch.6 by Aug 1')", "")
                books.append({
                    "title": title,
                    "current_state": current_chapter,
                    "goal": goal_desc,
                    "last_session": None,
                })

    # --- Strava ---
    _print_section("5. Strava Integration (optional)")
    strava_config = existing.get("strava", {})
    has_strava = _prompt_yn("  Do you use Strava for exercise tracking?",
                            bool(strava_config.get("client_id")))
    if has_strava:
        print("  Get your credentials from strava.com/settings/api")
        print("  Create an app named 'RB Life Lens' — the callback URL can be http://localhost")
        client_id = _prompt("  Strava Client ID", str(strava_config.get("client_id", "")))
        client_secret = _prompt("  Strava Client Secret", strava_config.get("client_secret", ""))
        refresh_token = _prompt("  Strava Refresh Token (run strava_auth.py if you don't have this yet)",
                                strava_config.get("refresh_token", ""))
        strava_config = {
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "enabled": bool(client_id and client_secret),
        }

    # --- Calendar keywords for date night / recreation ---
    _print_section("6. Calendar Keywords (optional)")
    print("  RB scans your calendar to detect date nights and recreation.")
    print("  Add any calendar event keywords that indicate personal time.\n")
    calendar_personal_keywords = existing.get("calendar_personal_keywords", [
        "date night", "dinner with mary", "cabin", "golf", "vacation",
        "game", "concert", "church", "family", "fun",
    ])
    if spouse_name:
        calendar_personal_keywords.append(f"dinner with {spouse_name.lower()}")
        calendar_personal_keywords = list(dict.fromkeys(calendar_personal_keywords))  # dedup
    show_keywords = _prompt_yn(f"  Current keywords: {calendar_personal_keywords[:5]}... Keep them?", True)
    if not show_keywords:
        raw = _prompt("  Enter comma-separated keywords")
        calendar_personal_keywords = [k.strip() for k in raw.split(",") if k.strip()]

    # --- Assemble config ---
    config = {
        "version": "1.0",
        "last_updated": date.today().isoformat(),
        "user_name": user_name,
        "spouse_name": spouse_name,
        "domains": enabled_domains,
        "personal_projects": projects,
        "author_projects": books,
        "calendar_personal_keywords": calendar_personal_keywords,
    }
    if has_strava and strava_config:
        config["strava"] = strava_config

    return config


def save_config(config: dict) -> None:
    GOALS_PATH.write_text(
        yaml.dump(config, default_flow_style=False, allow_unicode=True, sort_keys=False),
        encoding="utf-8"
    )
    print(f"\n✓ Life goals saved to {GOALS_PATH}")
    print("\nNext steps:")
    if config.get("strava", {}).get("enabled"):
        print("  • Run: python3 strava_sync.py --dry-run  (verify Strava connection)")
    print("  • Run: python3 personal_log.py  (log today's manual check-ins)")
    print("  • Re-render your brief: python3 render_daily_brief.py --force")


def show_config() -> None:
    if not GOALS_PATH.exists():
        print("No life_goals.yaml found. Run onboarding first.")
        return
    config = yaml.safe_load(GOALS_PATH.read_text())
    print(f"\nLife Goals — {config.get('user_name', 'User')} (updated {config.get('last_updated', '?')})\n")
    for domain in config.get("domains", []):
        print(f"  {domain['label']}")
        for goal in domain.get("goals", []):
            icon = goal.get("brief_icon", "•")
            target = goal.get("target", "?")
            unit = goal.get("target_unit", "")
            source = goal.get("data_source", "manual")
            print(f"    {icon} {goal['label']}: {target} {unit} [{source}]")
    if config.get("author_projects"):
        print("\n  Author Projects:")
        for b in config["author_projects"]:
            print(f"    ✍️  {b['title']} — {b.get('current_state', 'in progress')}")
    if config.get("personal_projects"):
        print("\n  Personal Projects:")
        for p in config["personal_projects"]:
            print(f"    🔨 {p['name']} — {p.get('status', '')}")


def main() -> int:
    p = argparse.ArgumentParser(description="RB Life Goals Onboarding")
    p.add_argument("--edit", action="store_true", help="Re-run with existing answers pre-filled")
    p.add_argument("--show", action="store_true", help="Show current config")
    p.add_argument("--add-goal", action="store_true", help="Add a goal to an existing config")
    args = p.parse_args()

    if args.show:
        show_config()
        return 0

    config = run_onboarding(edit_mode=args.edit)

    print("\n" + "=" * 60)
    print("  Review your life goals config:")
    print("=" * 60)
    for domain in config.get("domains", []):
        print(f"\n  {domain['label']}")
        for goal in domain.get("goals", []):
            print(f"    {goal.get('brief_icon', '•')} {goal['label']}: "
                  f"{goal['target']} {goal['target_unit']} [{goal['data_source']}]")

    confirm = _prompt_yn("\nSave this config?", True)
    if confirm:
        save_config(config)
    else:
        print("Not saved.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
