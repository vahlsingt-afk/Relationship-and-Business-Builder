#!/usr/bin/env python3
"""
refresh_all.py — rebuild every cache.

Runs each script with --cache so `system/.cache/` is fully populated. This is
the one command a session should run to make subsequent reads cheap.

Usage:
    python3 refresh_all.py
    python3 refresh_all.py --date 2026-05-15   # forwarded to date-aware scripts
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent


def run(cmd: list[str]) -> int:
    print(f"$ {' '.join(cmd)}")
    return subprocess.call(cmd)


def build_commands(*, date: str | None = None, confirm_passive_ri: bool = True,
                    py: str | None = None) -> list[list[str]]:
    """The ordered subprocess command list for a full refresh. Split out
    from main() so the sequencing (e.g. the campaign rebuild running before
    daily_brief.py) is directly testable."""
    date_args = ["--date", date] if date else []
    py = py or sys.executable or "python3"
    commands = [
        [py, str(SCRIPTS_DIR / "refresh_sources.py"), "--all", "--save-health"],
        [py, str(SCRIPTS_DIR / "validate_baseline.py"), "--cache", "--json"],
        [py, str(SCRIPTS_DIR / "legacy_transfer_watch.py"), "--cache", "--json"],
        [py, str(SCRIPTS_DIR / "source_watch.py"), "--cache", "--json"],
        # relationship_signals must run before daily_brief so the cached signals
        # report is available to consumers that read the cache directly.
        # RB-9.66-D: 48h window matches daily_brief.py's call; --cache also
        # persists the novelty fingerprint cache (relationship_signals_seen.json).
        [py, str(SCRIPTS_DIR / "relationship_signals.py"), "--cache", "--json", "--hours", "48", *date_args],
        [py, str(SCRIPTS_DIR / "passive_email_intelligence.py"), "--cache", "--json"],
        [py, str(SCRIPTS_DIR / "strategic_operators.py"), "--cache", "--json"],
        # passive_ri_ingest runs after relationship_signals so it can read the
        # cached signals and propose/record RI events before daily_brief reads them.
        [py, str(SCRIPTS_DIR / "passive_ri_ingest.py"), "--cache", *date_args],
        # RB ended-role/current-company cleanup (2026-08-06, remaining-work item 7):
        # rebuild every active campaign's roster/company artifacts now that the
        # baseline may have moved (e.g. an employment_status correction) —
        # campaign_engine.refresh_active_campaigns() is designed exactly for this
        # call site but was previously only reachable via --refresh-all by hand.
        [py, str(SCRIPTS_DIR / "campaign_engine.py"), "--refresh-all"],
        # Sprint F — intelligence_assessment runs after all gather-phase scripts
        # and before daily_brief.  It fetches web intelligence, classifies email
        # headlines, runs convergence analysis, and generates mutation proposals.
        # Use --cache to skip if already run today (idempotent).
        [py, str(SCRIPTS_DIR / "intelligence_assessment.py"), "--cache"],
        # RB-9.63: earnings_monitor fetches EDGAR 8-K filings for watchlist companies.
        # Runs daily — acquisitions, material events, and leadership changes file here
        # first. --cache = --fetch --save-health (idempotent).
        [py, str(SCRIPTS_DIR / "earnings_monitor.py"), "--cache"],
        # Confidence-Based Auto-Recording Phase 4 (2026-09-25): bridge the
        # rows earnings_monitor.py just captured into brand_profile_common's
        # recent_signals, so the mechanical brand brief sees them same-day.
        # --days 2 (not the 1-day default) covers a run that slips past
        # midnight; add_signal()'s dedupe makes the overlap a no-op.
        [py, str(SCRIPTS_DIR / "earnings_monitor.py"), "--bridge-to-brand-profiles", "--days", "2"],
        # RB-9.65-D: entity_alerts is a Google-News "alerts" layer for the
        # mandatory watchlist (M&A, funding, exec moves, layoffs, partnerships).
        # Rotates ~8 entities/day; idempotent, never fails the pipeline.
        [py, str(SCRIPTS_DIR / "entity_alerts.py"), "--cache"],
        # RB-9.67-A/B/D: signal_correlation correlates web_scan + entity_alerts
        # to detect momentum (multiple signals on one entity within 7 days),
        # flagging opportunity-relevant clusters for Section 4.
        [py, str(SCRIPTS_DIR / "signal_correlation.py"), "--cache"],
    ]
    if confirm_passive_ri:
        commands.append([
            py, str(SCRIPTS_DIR / "passive_ri_ingest.py"),
            "confirm", "--all", "--high-confidence-only",
        ])
    commands.extend([
        [py, str(SCRIPTS_DIR / "daily_brief.py"), "--cache", "--dry-run", *date_args],
        [py, str(SCRIPTS_DIR / "action_drafts.py"), "--cache"],
        [py, str(SCRIPTS_DIR / "meeting_prep.py"), "--for-today", *date_args],
        [py, str(SCRIPTS_DIR / "loop_autopilot.py"), "--phase", "morning"],
        [py, str(SCRIPTS_DIR / "gap_detection.py"), "--cache", "--json"],
        [py, str(SCRIPTS_DIR / "loop_parser.py"), "--cache", "--json", *date_args],
        [py, str(SCRIPTS_DIR / "network_gap.py"), "--cache", "--json"],
        [py, str(SCRIPTS_DIR / "drr_score.py"), "--cache", "--json", "--limit", "100", *date_args],
    ])
    return commands


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--date", help="ISO date forwarded to date-aware scripts.")
    # RB-2026-08-24: flipped to default-on per Todd's explicit decision --
    # high-confidence-only passive RI events were sitting in a pending cache
    # every day, never actually applied, unless this flag was passed by
    # hand. --no-confirm-passive-ri is the escape hatch if this ever needs
    # to go back to proposal-only.
    p.add_argument("--confirm-passive-ri", dest="confirm_passive_ri", action="store_true", default=True,
                   help="Apply newly proposed passive RI projections during refresh (default: on).")
    p.add_argument("--no-confirm-passive-ri", dest="confirm_passive_ri", action="store_false",
                   help="Disable auto-applying passive RI projections; leave them pending only.")
    args = p.parse_args()
    commands = build_commands(date=args.date, confirm_passive_ri=args.confirm_passive_ri)
    failures = 0
    for cmd in commands:
        # Suppress stdout for the noisy scripts; just record exit code.
        rc = subprocess.run(cmd, capture_output=True)
        ok = "OK" if rc.returncode == 0 else f"FAIL ({rc.returncode})"
        print(f"  {ok}  {Path(cmd[1]).name}")
        if rc.returncode != 0:
            failures += 1
            sys.stderr.write(rc.stderr.decode(errors="replace"))
    print()
    if failures:
        print(f"{failures} script(s) failed.")
        return 1
    print("All caches refreshed in system/.cache/.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
