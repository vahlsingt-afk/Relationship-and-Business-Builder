#!/usr/bin/env python3
"""
network_analysis.py — strategic report card for the operator's network.

Synthesizes signals from across the system into a one-page assessment:
where the network is strong, where it's weak, where the bridges are,
how the composition compares to standard networking targets, and
what to do next.

Usage:
    python3 network_analysis.py                       # markdown report to stdout
    python3 network_analysis.py --write               # write system/analysis/<date>-network-analysis.md
    python3 network_analysis.py --json
    python3 network_analysis.py --cache               # write system/.cache/network_analysis.json
    python3 network_analysis.py --date 2026-05-15
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rb_core as core


ANALYSIS_DIR = core.SYSTEM_DIR / "analysis"


def render_markdown(rep: dict) -> str:
    out: list[str] = []
    out.append(f"# Network analysis — {rep['as_of']}\n")
    comp = rep["composition_health"]
    div = rep["diversity"]
    out.append(
        f"**{rep['baseline_total']:,} total entries.** Active RCs: {comp['active_rc_count']} "
        f"(inner {comp['inner_rc_count']}). LKI count: {comp['lki_count']}. "
        f"Card coverage: {comp['card_coverage_pct']}%. "
        f"Inner-tier RCs in their dormancy window: {comp['inner_in_window_pct']}%. "
        f"{rep['active_threads_count']} active threads on the board.\n"
    )
    out.append("---\n")

    # Strengths
    out.append("## Strengths\n")
    if rep["strengths"]:
        out.append("Top clusters by relationship depth — anchor presence + LKI+ count + average DRR + recency.\n")
        out.append("| Cluster | LKI+ | RCs (inner) | Avg DRR | % inner in window | Strength |")
        out.append("|---|---:|---:|---:|---:|---:|")
        for s in rep["strengths"][:5]:
            inner_label = f"{s['rc_count']} ({s['inner_rc_count']})"
            inner_names = (
                f"<br/><sub>{', '.join(s['inner_rcs'])}</sub>"
                if s["inner_rcs"] else ""
            )
            out.append(
                f"| **{s['company']}**{inner_names} | {s['lki_plus_count']} | "
                f"{inner_label} | {s['avg_drr_base']} | {s['recent_inner_pct']}% | {s['strength_score']} |"
            )
    else:
        out.append("(no clusters meet the minimum size threshold)")

    # Weaknesses
    out.append("\n## Weaknesses\n")
    w = rep["weaknesses"]

    if w["unanchored_clusters"]:
        out.append("**Unanchored clusters** — size ≥ 5 but no inner-tier RC. Top promotion-candidate territory:\n")
        out.append("| Cluster | Size | LKI | Gap score |")
        out.append("|---|---:|---:|---:|")
        for u in w["unanchored_clusters"][:6]:
            out.append(f"| `{u['company']}` | {u['total']} | {u['lki_count']} | {u['gap_score']} |")

    if w["rcs_without_last_touch"]:
        names = ", ".join(f"**{r['name']}** ({r['tier']})" for r in w["rcs_without_last_touch"])
        out.append(f"\n**RCs without `last_touch`** — invisible to the dormancy engine: {names}.")

    if w["missing_cards"]:
        names = ", ".join(f"{c['name']} ({c['tier']})" for c in w["missing_cards"])
        out.append(f"\n**Missing RC cards:** {names}.")

    if w["contact_field_gaps"]:
        inner = [g for g in w["contact_field_gaps"] if g["tier"] == "inner"]
        out.append(f"\n**Contact-field gaps:** {len(w['contact_field_gaps'])} active RCs missing email/phone "
                   f"({len(inner)} inner-tier).")

    if w["cooling_clusters"]:
        out.append("\n**Cooling clusters** — majority of RCs in the cluster past their dormancy threshold:\n")
        out.append("| Cluster | RCs | Cooling | % |")
        out.append("|---|---:|---:|---:|")
        for cc in w["cooling_clusters"][:5]:
            out.append(f"| `{cc['company']}` | {cc['rcs']} | {cc['cooling']} | {cc['cooling_pct']}% |")

    # Bridges
    out.append("\n## Bridges (weak-tie leverage)\n")
    out.append("Contacts who span 2+ Circles — they're the bridges between clusters that wouldn't otherwise connect (Granovetter weak-tie value).\n")
    if rep["bridges"]:
        out.append("| Name | Tier | Spans | Circles |")
        out.append("|---|---|---:|---|")
        for b in rep["bridges"][:10]:
            tier = b["rc_tier"] or "—"
            sc = b["signal_class"]
            out.append(f"| **{b['name']}** ({b['current_company'] or '—'}) | {sc}/{tier} | {b['bridge_span']} | {', '.join(f'`{c}`' for c in b['circles'])} |")
    else:
        out.append("(no multi-Circle contacts found — every contact is in 0 or 1 Circle)")

    # Composition health
    out.append("\n## Composition health\n")
    out.append("Comparison to Dunbar-derived target bands. These are guidelines, not commandments.\n")
    out.append("| Layer | Count | Target band | State |")
    out.append("|---|---:|---:|---|")
    out.append(f"| Inner-tier RCs | {comp['inner_rc_count']} | {comp['inner_rc_band'][0]}–{comp['inner_rc_band'][1]} | **{comp['inner_rc_band_state']}** |")
    out.append(f"| Active RCs (all tiers) | {comp['active_rc_count']} | {comp['active_rc_band'][0]}–{comp['active_rc_band'][1]} | **{comp['active_rc_band_state']}** |")
    out.append(f"| LKI | {comp['lki_count']} | {comp['lki_band'][0]}–{comp['lki_band'][1]} | **{comp['lki_band_state']}** |")
    out.append(f"| Card coverage | {comp['card_coverage_pct']}% | — | — |")
    out.append(f"| Inner-tier in window | {comp['inner_in_window_pct']}% | ≥ 60% | {'**ok**' if comp['inner_in_window_pct'] >= 60 else '**below**'} |")

    # Diversity
    out.append("\n## Cluster diversity\n")
    if div["over_concentration"]:
        out.append(f"⚠ **Over-concentration warning** — `{div['over_concentration']}` is >30% of your LKI+ network.\n")
    out.append(
        f"Total LKI+ contacts in named clusters: {div['total_lki_plus']}. "
        f"Clusters with 3+ members: {div['clusters_with_3_plus_members']}. "
        f"Median cluster size: {div['median_cluster_size']}.\n"
    )
    if div["top_clusters"]:
        out.append("\n| Cluster | Count | % of LKI+ |")
        out.append("|---|---:|---:|")
        for c in div["top_clusters"][:8]:
            out.append(f"| `{c['company']}` | {c['count']} | {c['pct_of_lki_plus']}% |")

    # Recommendations
    out.append("\n## Strategic recommendations\n")
    if rep["recommendations"]:
        out.append("Ranked by impact (high → low), with low-effort wins prioritized within each tier.\n")
        for i, r in enumerate(rep["recommendations"][:12], 1):
            imp = r["impact"].upper()
            eff = r["effort"]
            out.append(f"\n{i}. **[{imp} · {eff} effort]** {r['title']}")
            out.append(f"   _Reason: {r['reason']}_")
    else:
        out.append("(no recommendations surfaced — the graph is in good shape across the measured dimensions)")

    out.append("\n---\n")
    out.append(
        f"*Generated by `system/scripts/network_analysis.py` against baseline of "
        f"{rep['baseline_total']:,} entries on {rep['as_of']}. Re-run with "
        f"`python3 system/scripts/network_analysis.py --write` to refresh.*"
    )
    return "\n".join(out) + "\n"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--date", help="ISO date (default: system date)")
    p.add_argument("--json", action="store_true")
    p.add_argument("--write", action="store_true",
                   help="Persist the markdown to system/analysis/<date>-network-analysis.md")
    p.add_argument("--cache", action="store_true",
                   help="Write to system/.cache/network_analysis.json")
    args = p.parse_args()

    today = date.fromisoformat(args.date) if args.date else date.today()
    rep = core.network_analysis(today=today)

    if args.cache:
        core.write_cache("network_analysis", rep, source="network_analysis.py")

    if args.json:
        print(json.dumps(rep, indent=2, default=str))
        return 0

    md = render_markdown(rep)

    if args.write:
        ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
        target = ANALYSIS_DIR / f"{today.isoformat()}-network-analysis.md"
        target.write_text(md)
        print(f"Wrote {target.relative_to(core.PROJECT_DIR)}.")
        return 0

    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
