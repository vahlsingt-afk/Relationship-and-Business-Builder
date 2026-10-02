"""
test_brief_quality_checks.py

RB-2026-09-01: mechanical content-quality checks added to
brief_acceptance_check.py's existing FAIL/WARN gate (duplicate headlines,
DOM-artifact leaks, unfilled template tokens, bare-homepage links, banned
literal headers, Proof Dashboard ordering, thin sections). Grounded in a
catalog of real historical defects in render_intelligence_brief.py.
Detection only -- no auto-repair, per the explicit agreed scope: decide
auto-fix-vs-flag per rule later, only as real cases are found.

Where practical, tests run against real committed rendered output (not just
synthetic fixtures) -- prior structural tests in this codebase only ever
exercised mocked payloads through render functions, never genuinely
delivered files, which was the gap this catalog was built to close.
"""
from __future__ import annotations

import re
import sys
from datetime import datetime, timezone
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = TESTS_DIR.parent / "scripts"
BRIEFS_DIR = TESTS_DIR.parent / "briefs"
sys.path.insert(0, str(SCRIPTS_DIR))
import brief_acceptance_check as bac  # noqa: E402


# --- Real-output fixtures --------------------------------------------------
_REAL_CLEAN_INTEL_MD = (BRIEFS_DIR / "2026-09-01-intelligence-brief.md").read_text(encoding="utf-8")
_REAL_CLEAN_DAILY_MD = (BRIEFS_DIR / "2026-09-01-daily-brief.md").read_text(encoding="utf-8")
# The real historical defect this check catalog exists to catch: K: GP/
# Genius -- Field Intelligence repeated a D+ item verbatim (identical
# wrapped tracking URL, "Stripe Finalizes Deal to Acquire AI Startup
# OpenRouter...") on 2026-08-18 -- a gap in K's own dedup logic, which by
# its own docstring only guards against F: Watchlist, not D+.
_REAL_DUPLICATE_INTEL_MD = (BRIEFS_DIR / "2026-08-18-intelligence-brief.md").read_text(encoding="utf-8")


def _healthy_report(**overrides) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    report = {
        "source_health": {
            "sources": {name: {"status": "refreshed", "last_refreshed_at": now}
                        for name in bac.REQUIRED_SOURCES},
        },
        "delivery_check": {"automated_pass_count": 11, "automated_warn_count": 0,
                            "automated_fail_count": 0, "reasons": {}},
        "ecosystem_intelligence": {"entities": [], "signals": []},
        "loops": {"overdue": [], "due_today": [], "this_week": [], "future": [], "closed": []},
        "active_threads": [{"id": "T-1"}],
        "relationship_signals": {"signals": []},
        "cos_judgment": {"hard_truths": ["something real"]},
        "strategic_events": {"events": []},
    }
    report.update(overrides)
    return report


def _minimal_clean_intel_md() -> str:
    """Small, hand-built, structurally complete intelligence brief with no
    defects -- used where a real file is too large/noisy to assert exact
    counts against."""
    def headlines(section: str, n: int) -> str:
        return "\n\n".join(
            f"[{section} Headline {i}](https://example.com/{section.lower()}/{i})\n"
            f"Example Source | 2026-09-01\n"
            f"*Summary text.*\n"
            f"**Why it matters:** Example reasoning.\n"
            f"**[Read more →](https://example.com/{section.lower()}/{i})**"
            for i in range(1, n + 1)
        )
    return (
        "## What Changed Today\n\nReal content here, not empty, at least 20 chars.\n\n"
        f"## A: World Headlines\n\n{headlines('A', 5)}\n\n"
        f"## B: National Headlines\n\n{headlines('B', 5)}\n\n"
        f"## C: Restaurant Industry\n\n{headlines('C', 5)}\n\n"
        f"## D: Restaurant Technology\n\n{headlines('D', 5)}\n\n"
        "## D+: Curated Trade Reads\n\n"
        # RB-DEFECT (2026-09-15): these three were literally titled "Trade
        # Read One/Two/Three" -- placeholder text that shares "trade"+"read"
        # with itself and false-positived on check_no_duplicate_story_clusters'
        # word-overlap heuristic (the same class of bug already fixed once
        # for real "near 52-week low" headlines). Real D+ headlines are
        # always distinct real-world story titles (see render_intelligence_
        # brief.py's D+ block) -- this fixture should reflect that, not
        # accidentally test the detector against its own placeholder naming.
        "### [Payments convergence accelerates across restaurant tech](https://trade.example.com/1)\n\n*Trade Source · Aug 31, 2026*\n\n"
        "**Why it matters:** Reasoning.\n\n"
        "### [Drive-thru AI adoption expands among multi-unit operators](https://trade.example.com/2)\n\n*Trade Source · Aug 31, 2026*\n\n"
        "**Why it matters:** Reasoning.\n\n"
        "### [Labor cost pressure reshapes QSR staffing models](https://trade.example.com/3)\n\n*Trade Source · Aug 31, 2026*\n\n"
        "**Why it matters:** Reasoning.\n\n"
        "## J: Proof Dashboard\n\nSome dashboard content.\n"
    )


# --- check_no_cross_section_duplicate_headlines ----------------------------

def test_no_cross_section_duplicate_headlines_clean_real_output():
    result = bac.check_no_cross_section_duplicate_headlines(_REAL_CLEAN_INTEL_MD)
    assert result["passed"] is True


def test_no_cross_section_duplicate_headlines_catches_real_historical_defect():
    result = bac.check_no_cross_section_duplicate_headlines(_REAL_DUPLICATE_INTEL_MD)
    assert result["passed"] is False
    assert "Stripe" in result["detail"]


def test_no_cross_section_duplicate_headlines_synthetic_defect():
    md = (
        "## A: World Headlines\n\n[Same Story](https://example.com/story)\n\n"
        "## B: National Headlines\n\n[Same Story](https://example.com/story)\n"
    )
    result = bac.check_no_cross_section_duplicate_headlines(md)
    assert result["passed"] is False


def test_no_cross_section_duplicate_headlines_ignores_own_read_more_link():
    """Every real item legitimately repeats its own URL once as the title
    link and once as '**[Read more →](url)**' -- that must not itself count
    as a duplicate."""
    md = (
        "## A: World Headlines\n\n[A Story](https://example.com/story)\n"
        "Source | 2026-09-01\n*Summary.*\n**[Read more →](https://example.com/story)**\n"
    )
    result = bac.check_no_cross_section_duplicate_headlines(md)
    assert result["passed"] is True


# --- check_no_cross_day_repeat_without_grace --------------------------------

def test_no_cross_day_repeat_not_checked_without_prior_day():
    result = bac.check_no_cross_day_repeat_without_grace("## A: World Headlines\n", None)
    assert result["passed"] is None


def test_no_cross_day_repeat_flags_repeated_url():
    prior = "## A: World Headlines\n\n[Old Story](https://example.com/x)\n"
    today = "## A: World Headlines\n\n[Old Story](https://example.com/x)\n"
    result = bac.check_no_cross_day_repeat_without_grace(today, prior)
    assert result["passed"] is False
    assert result["severity"] == "warn"


def test_no_cross_day_repeat_passes_when_no_overlap():
    prior = "## A: World Headlines\n\n[Old Story](https://example.com/x)\n"
    today = "## A: World Headlines\n\n[New Story](https://example.com/y)\n"
    result = bac.check_no_cross_day_repeat_without_grace(today, prior)
    assert result["passed"] is True


# --- check_no_dom_html_artifacts --------------------------------------------

def test_no_dom_html_artifacts_clean_real_output():
    result = bac.check_no_dom_html_artifacts(
        {"intelligence": _REAL_CLEAN_INTEL_MD, "daily": _REAL_CLEAN_DAILY_MD})
    assert result["passed"] is True


def test_no_dom_html_artifacts_catches_leaked_dom_markup():
    """RB-DEFECT-2026-08-11 class: a broken page fetch leaking raw CSS/JS
    class names into a story's summary field."""
    text = "Summary: ]:pointer-events-auto R6Vx5W_threadScrollVars scroll-mb-[calc(var(--x,0px))]"
    result = bac.check_no_dom_html_artifacts({"intelligence": text})
    assert result["passed"] is False


# --- check_no_unfilled_template_placeholders --------------------------------

def test_no_unfilled_template_placeholders_clean_real_output():
    result = bac.check_no_unfilled_template_placeholders(
        {"intelligence": _REAL_CLEAN_INTEL_MD, "daily": _REAL_CLEAN_DAILY_MD})
    assert result["passed"] is True


def test_no_unfilled_template_placeholders_catches_unformatted_fstring():
    result = bac.check_no_unfilled_template_placeholders({"daily": "Amount due: {amount}"})
    assert result["passed"] is False


def test_no_unfilled_template_placeholders_does_not_flag_real_tbd_reporting():
    """Live-testing catch: 'exact date/time TBD, confirm against FSTEC
    schedule' in the real 2026-09-01 daily brief is genuine accurate
    reporting of an unknown, not a leaked template token -- TBD/TODO were
    deliberately excluded from the pattern after this false positive."""
    result = bac.check_no_unfilled_template_placeholders(
        {"daily": "exact date/time TBD, confirm against schedule"})
    assert result["passed"] is True


# --- check_headlines_have_direct_article_links ------------------------------

def test_headlines_have_direct_article_links_clean_real_output():
    result = bac.check_headlines_have_direct_article_links(_REAL_CLEAN_INTEL_MD)
    assert result["passed"] is True


def test_headlines_have_direct_article_links_catches_bare_homepage():
    md = "## A: World Headlines\n\n[Some Story](https://example.com)\n"
    result = bac.check_headlines_have_direct_article_links(md)
    assert result["passed"] is False


# --- check_section_headline_counts (WARN) -----------------------------------

def test_section_headline_counts_flags_thin_section():
    md = "## A: World Headlines\n\n[Only One](https://example.com/1)\n"
    result = bac.check_section_headline_counts(md)
    assert result["passed"] is False
    assert result["severity"] == "warn"


def test_section_headline_counts_passes_with_five_to_seven():
    result = bac.check_section_headline_counts(_minimal_clean_intel_md())
    assert result["passed"] is True


# --- check_prohibited_literal_headers_absent --------------------------------

def test_prohibited_literal_headers_absent_clean_real_output():
    result = bac.check_prohibited_literal_headers_absent(_REAL_CLEAN_INTEL_MD)
    assert result["passed"] is True


def test_prohibited_literal_headers_absent_catches_banned_header():
    md = "## A: World Headlines\n\nRB Take: this is bad.\n"
    result = bac.check_prohibited_literal_headers_absent(md)
    assert result["passed"] is False


# --- check_proof_dashboard_is_last_section ----------------------------------

def test_proof_dashboard_is_last_section_clean_real_output():
    result = bac.check_proof_dashboard_is_last_section(_REAL_CLEAN_INTEL_MD)
    assert result["passed"] is True


def test_proof_dashboard_is_last_section_catches_leading_dashboard():
    md = "## J: Proof Dashboard\n\nstuff\n\n## A: World Headlines\n\nmore stuff\n"
    result = bac.check_proof_dashboard_is_last_section(md)
    assert result["passed"] is False


def test_proof_dashboard_is_last_section_not_checked_when_absent():
    result = bac.check_proof_dashboard_is_last_section("## A: World Headlines\n\nstuff\n")
    assert result["passed"] is None


# --- check_newsletter_section_has_min_links (WARN) --------------------------

def test_newsletter_section_has_min_links_clean_real_output():
    result = bac.check_newsletter_section_has_min_links(_REAL_CLEAN_INTEL_MD)
    assert result["passed"] is True


def test_newsletter_section_has_min_links_flags_thin_section():
    md = "## D+: Curated Trade Reads\n\n### [One](https://example.com/1)\n\n### [Two](https://example.com/2)\n"
    result = bac.check_newsletter_section_has_min_links(md)
    assert result["passed"] is False


# --- check_brief() integration ----------------------------------------------

def test_check_brief_integration_passes_on_full_clean_fixture():
    # RB-DEFECT-072: delivery_check is now computed live (real on-disk
    # publish state / launchd), not read from report["delivery_check"] --
    # inject a synthetic passing finding so this stays deterministic.
    result = bac.check_brief(
        _healthy_report(),
        rendered_md={"intelligence": _minimal_clean_intel_md(), "daily": "## Day Ahead\n\nstuff\n"},
        previous_intel_md="## A: World Headlines\n\n[Unrelated Old Story](https://example.com/old)\n",
        delivery_readiness=bac._finding("delivery_check", "fail", True, "synthetic: healthy for test"),
    )
    assert result["passed"] is True
    assert result["fail_count"] == 0


def test_check_brief_integration_known_bad_composite_fails_with_expected_findings():
    bad_intel_md = (
        "## What Changed Today\n\nReal content here, not empty, at least 20 chars.\n\n"
        "## J: Proof Dashboard\n\nleading, should be last\n\n"
        "## A: World Headlines\n\n[Dup Story](https://example.com/dup)\n\n"
        "## B: National Headlines\n\n[Dup Story](https://example.com/dup)\n\n"
        "RB Take: banned header text\n"
    )
    result = bac.check_brief(_healthy_report(), rendered_md={"intelligence": bad_intel_md})
    assert result["passed"] is False
    failing = {f["check"] for f in result["findings"] if f["severity"] == "fail" and f["passed"] is False}
    assert "no_cross_section_duplicate_headlines" in failing
    assert "prohibited_literal_headers_absent" in failing
    assert "proof_dashboard_is_last_section" in failing


# --- Wiring regression -------------------------------------------------------

def test_brief_acceptance_gate_still_wired_required_before_send_steps():
    """Regression guard on the real wiring in morning_pipeline.py: the
    acceptance gate (which these new checks now live inside) must still run
    as a required=True step before both brief-send steps, or a bad brief
    could ship ungated.

    RB-DEFECT-072: the gate call moved into _run_acceptance_gate_with_repair
    (step_fn is injectable for testing, defaulting to the real _step) --
    updated to match that structure while still asserting the same real
    guarantee: production wiring still runs a required=True gate before
    either send step, and step_fn's default really is the real _step, not
    a no-op."""
    pipeline_src = (SCRIPTS_DIR / "morning_pipeline.py").read_text(encoding="utf-8")
    gate_match = re.search(
        r'acceptance_step\s*=\s*step_fn\("brief_acceptance_gate",\s*\[.*?\],\s*required=True\)',
        pipeline_src, re.DOTALL,
    )
    assert gate_match is not None, (
        "brief_acceptance_check.py must still be wired as a required=True step "
        "named acceptance_step inside _run_acceptance_gate_with_repair"
    )
    assert 'step_fn = step_fn or _step' in pipeline_src, (
        "step_fn must default to the real _step (subprocess-based) in production, "
        "not silently become a no-op"
    )
    call_match = re.search(
        r'gate_outcome\s*=\s*_run_acceptance_gate_with_repair\(py, today, date_args, build_steps\)',
        pipeline_src,
    )
    assert call_match is not None, (
        "run_pipeline() must call _run_acceptance_gate_with_repair with the real "
        "step function (no step_fn override) in production"
    )
    gate_idx = call_match.start()
    send_intel_idx = pipeline_src.index('_step("send_intelligence_brief_email"')
    send_daily_idx = pipeline_src.index('_step("send_daily_brief_email"')
    assert gate_idx < send_intel_idx
    assert gate_idx < send_daily_idx
