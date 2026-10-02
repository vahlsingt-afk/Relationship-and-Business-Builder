"""
test_signal_synthesis.py — Tests for signal_synthesis.py (RB 9.27).

signal_synthesis.py aggregates signals from all RB stores for a named entity
and synthesizes a pattern classification with hypothesis and opportunity/risk.

Test groups:
  SS1 (8):  Entity matching + normalization
  SS2 (8):  Pattern keyword scoring (_score_text_for_patterns)
  SS3 (8):  Exit background detection (_has_exit_background)
  SS4 (10): Store readers — isolated fixtures (tmpdir injection)
  SS5 (10): Pattern scoring + dominant pattern logic
  SS6 (8):  synthesize_entity_signals() — integration with injected stores
  SS7 (8):  PAR Technology exit_positioning scenario (multi-signal convergence)
  SS8 (6):  CLI smoke (--json, unknown entity, empty stores)
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SYNTH_PATH = ROOT / "system" / "scripts" / "signal_synthesis.py"


# ── Load module ────────────────────────────────────────────────────────────────

def _load_synth():
    module_name = "signal_synthesis_test_module"
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, SYNTH_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


ss = _load_synth()

# ── Fixtures ───────────────────────────────────────────────────────────────────

# Active threads YAML with a PAR thread
_THREADS_WITH_PAR = """\
threads:
  - id: "T-2026-05-par-pilot"
    title: "PAR Technology pilot discussion"
    status: "active"
    type: "sales"
    companies: ["PAR Technology", "ACME Corp"]
    opened: "2026-05-01"
    context: "Evaluating PAR for POS integration"
"""

# Active threads YAML with no PAR
_THREADS_NO_PAR = """\
threads:
  - id: "T-2026-05-other"
    title: "Some other company discussion"
    status: "active"
    type: "sales"
    companies: ["Other Corp"]
    opened: "2026-05-01"
    context: "No PAR here"
"""

# Market signals JSON with PAR exit signals
_MARKET_SIGNALS_PAR_EXIT = {
    "data": {
        "top": [
            {
                "company": "PAR Technology",
                "title": "PAR Technology under activist investor pressure to explore strategic alternatives",
                "pain_point_or_priority": "Board facing shareholder pressure; stock decline; strategic review underway",
                "strategic_relevance": "high",
                "source_name": "Reuters",
                "published_at": "2026-05-27T10:00:00Z",
                "url_canonical": "reuters.com/par-activist-2026",
                "source_quality": "high",
            },
            {
                "company": "PAR Technology",
                "title": "Oliver Ostertag named President at PAR Technology",
                "pain_point_or_priority": "Former M&A executive takes President role amid board pressure",
                "strategic_relevance": "high",
                "source_name": "BusinessWire",
                "published_at": "2026-05-20T08:00:00Z",
                "url_canonical": "businesswire.com/par-president-2026",
                "source_quality": "high",
            },
        ]
    }
}

# Market signals with no PAR
_MARKET_SIGNALS_EMPTY = {"data": {"top": []}}

# RB-2026-08-28: captured_at was hardcoded to 2026-05-25/24 --
# _social_overlay_signals() filters by a real datetime.now(timezone.utc)
# lookback window (tests use lookback_days=90), so those dates silently
# aged past 90 days as real time passed, well before this file's own logic
# was actually broken. Relative to real "now" so this can never drift stale
# again.
_NOW = datetime.now(timezone.utc)


def _iso_days_ago(days: int, hour: int = 12) -> str:
    dt = (_NOW - timedelta(days=days)).replace(hour=hour, minute=0, second=0, microsecond=0)
    return dt.isoformat().replace("+00:00", "Z")


# Social overlay with PAR posts showing M&A background
_SOCIAL_OVERLAY_PAR = {
    "posts": [
        {
            "id": "social-001",
            "author": {
                "name": "Oliver Ostertag",
                "headline": "President, Growth + AI at PAR Technology | M&A | Corporate Development",
            },
            "text": (
                "Just wrapped our Q2 partner summit at PAR. Excited about where "
                "we're heading. The restaurant industry consolidation continues to "
                "create interesting strategic opportunities."
            ),
            "captured_at": _iso_days_ago(5, hour=14),
        },
        {
            "id": "social-002",
            "author": {
                "name": "Joe Yetter",
                "headline": "CEO at PAR Technology",
            },
            "text": (
                "PAR is positioning for the next phase of growth. "
                "Strategic decisions ahead."
            ),
            "captured_at": _iso_days_ago(6, hour=9),
        },
    ]
}

# Social overlay with no PAR
_SOCIAL_OVERLAY_EMPTY = {"posts": []}

# Artifact registry with PAR stub
_REGISTRY_PAR = {
    "artifacts": [
        {
            "artifact_id": "micro_graph:par_technology",
            "name": "PAR Technology",
            "entity": "par technology",
            "status": "stub",
            "enrichment_count": 0,
            "freshness_date": "2026-05-01",
            "entity_aliases": ["PAR Tech"],
        }
    ]
}

# Ecosystem intelligence with PAR on watch list
_ECOSYSTEM_PAR_WATCHED = {
    "watch_list": [
        {
            "entity": "PAR Technology",
            "tier": 1,
            "last_signal_date": "2026-05-25",
        }
    ],
    "entities": [],
}

# Baseline contacts with Oliver Ostertag at PAR
_BASELINE_OLIVER = [
    {
        "id": "oliver-ostertag",
        "first_name": "Oliver",
        "last_name": "Ostertag",
        "company": "PAR Technology",
        "title": "President, Growth + AI",
        "tier": "tier_1",
        "notes": "Background in M&A and corporate development; investment banking experience",
    },
    {
        "id": "joe-yetter",
        "first_name": "Joe",
        "last_name": "Yetter",
        "company": "PAR Technology",
        "title": "Chief Executive Officer",
        "tier": "tier_1",
        "notes": "CEO since 2022",
    },
]


def _write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


# ── SS1: Entity matching + normalization ──────────────────────────────────────

class SS1_EntityMatching(unittest.TestCase):
    """_entity_matches() and _normalize() correctness."""

    def test_exact_match(self):
        self.assertTrue(ss._entity_matches("PAR Technology", "PAR Technology is interesting"))

    def test_case_insensitive(self):
        self.assertTrue(ss._entity_matches("PAR Technology", "par technology announced"))

    def test_token_overlap_all_match(self):
        # "PAR Technology" → tokens "par" (word-boundary-safe, <=4 chars) and
        # "technology" (substring, >4 chars) — both must be present.
        self.assertTrue(ss._entity_matches("PAR Technology", "par technology news"))

    def test_partial_token_no_match(self):
        # "Acme Corporation" needs both "acme" and "corporation" in text
        self.assertFalse(ss._entity_matches("Acme Corporation", "just acme news here"))

    def test_short_entity_name(self):
        # "PAR" is <=4 chars — matched word-boundary-safe via the token set.
        self.assertTrue(ss._entity_matches("PAR", "PAR announced today"))

    def test_no_match(self):
        self.assertFalse(ss._entity_matches("PAR Technology", "totally unrelated content"))

    def test_normalize_punctuation(self):
        # normalize should strip punctuation
        norm = ss._normalize("M&A, Corp-Dev!")
        self.assertNotIn("&", norm)
        self.assertNotIn(",", norm)

    def test_empty_text(self):
        self.assertFalse(ss._entity_matches("PAR Technology", ""))

    # ── RB-DEFECT (2026-09-16): live word-boundary collision regressions ──
    # Confirmed live before this fix: "Qu" (a real, actively-tracked
    # competitor) matched ANY text containing "quarter", "acquisition",
    # "equity", "require", "unique" -- any text with consecutive q-u letters
    # -- false-positiving into that entity's convergence signals daily via
    # every signal_synthesis store reader. "PAR" matched inside "comparable",
    # the same collision class already fixed once in
    # intelligence_triage.py's registered-artifact matcher but never here.

    def test_qu_does_not_match_quarter_or_acquisition(self):
        self.assertFalse(ss._entity_matches("Qu", "Same-store sales grew this quarter."))
        self.assertFalse(ss._entity_matches("Qu", "The deal closed after the acquisition."))
        self.assertFalse(ss._entity_matches("Qu", "Equity holders were unique in this deal."))

    def test_qu_still_matches_real_qu_mentions(self):
        self.assertTrue(ss._entity_matches("Qu", "Qu launched a new POS integration."))
        self.assertTrue(ss._entity_matches("Qu", "qu added Qu Pay to its platform."))

    def test_par_does_not_match_comparable(self):
        self.assertFalse(ss._entity_matches("PAR", "The vendor was comparable to others."))

    def test_multi_word_entity_short_first_token_still_required(self):
        # "NCR Voyix" must not match text that only has "Voyix"-adjacent
        # content without "NCR" -- both tokens are required, including the
        # short one, not just the long one.
        self.assertFalse(ss._entity_matches("NCR Voyix", "Voyix reported strong sales."))
        self.assertTrue(ss._entity_matches("NCR Voyix", "NCR Voyix reported strong sales."))


# ── SS2: Pattern keyword scoring ──────────────────────────────────────────────

class SS2_PatternKeywords(unittest.TestCase):
    """_score_text_for_patterns() assigns correct pattern tags."""

    def test_exit_keywords(self):
        text = "activist investor pushing strategic alternatives and board pressure on the company"
        scores = ss._score_text_for_patterns(text)
        self.assertGreater(scores[ss.PATTERN_EXIT], 0)
        self.assertEqual(scores[ss.PATTERN_GROWTH], 0)

    def test_growth_keywords(self):
        text = "record revenue growth, new customer win, expansion into new market"
        scores = ss._score_text_for_patterns(text)
        self.assertGreater(scores[ss.PATTERN_GROWTH], 0)

    def test_distress_keywords(self):
        text = "company announced layoffs and restructuring amid revenue decline"
        scores = ss._score_text_for_patterns(text)
        self.assertGreater(scores[ss.PATTERN_DISTRESS], 0)

    def test_consolidation_keywords(self):
        text = "acquisition of smaller competitor completed; merger creates new entity"
        scores = ss._score_text_for_patterns(text)
        self.assertGreater(scores[ss.PATTERN_CONSOLIDATION], 0)

    def test_transition_keywords(self):
        text = "new CEO appointed after leadership change; stepping down announcement"
        scores = ss._score_text_for_patterns(text)
        self.assertGreater(scores[ss.PATTERN_TRANSITION], 0)

    def test_neutral_text_no_scores(self):
        text = "company held its annual customer conference in Chicago"
        scores = ss._score_text_for_patterns(text)
        self.assertEqual(sum(scores.values()), 0)

    def test_multi_pattern_text(self):
        # activist + layoffs → exit + distress
        text = "activist investor pushing for ceo departure amid layoffs"
        scores = ss._score_text_for_patterns(text)
        self.assertGreater(scores[ss.PATTERN_EXIT], 0)
        self.assertGreater(scores[ss.PATTERN_DISTRESS], 0)

    def test_returns_all_patterns_except_stable_unknown(self):
        scores = ss._score_text_for_patterns("hello world")
        for p in (ss.PATTERN_EXIT, ss.PATTERN_GROWTH, ss.PATTERN_DISTRESS,
                  ss.PATTERN_CONSOLIDATION, ss.PATTERN_COMPETITIVE, ss.PATTERN_TRANSITION):
            self.assertIn(p, scores)
        self.assertNotIn(ss.PATTERN_STABLE, scores)
        self.assertNotIn(ss.PATTERN_UNKNOWN, scores)


# ── SS3: Exit background detection ────────────────────────────────────────────

class SS3_ExitBackground(unittest.TestCase):
    """_has_exit_background() catches M&A/finance backgrounds."""

    def test_ma_term(self):
        self.assertTrue(ss._has_exit_background("Background in M&A and corporate development"))

    def test_investment_banking(self):
        self.assertTrue(ss._has_exit_background("Former investment banking analyst"))

    def test_private_equity(self):
        self.assertTrue(ss._has_exit_background("Private equity experience at Blackstone"))

    def test_corp_dev(self):
        self.assertTrue(ss._has_exit_background("Led corp dev at Oracle for 5 years"))

    def test_no_exit_background(self):
        self.assertFalse(ss._has_exit_background("Sales and marketing executive, SaaS focused"))

    def test_empty_text(self):
        self.assertFalse(ss._has_exit_background(""))

    def test_venture_capital(self):
        self.assertTrue(ss._has_exit_background("Venture capital background, Series A through C"))

    def test_transaction_background(self):
        self.assertTrue(ss._has_exit_background("Led multiple transaction processes"))


# ── SS4: Store readers with injected fixtures ─────────────────────────────────

class SS4_StoreReaders(unittest.TestCase):
    """Each store reader returns correct signals from injected fixture files."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.tmpdir = Path(self.tmp)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, name: str, data) -> Path:
        p = self.tmpdir / name
        if isinstance(data, str):
            p.write_text(data, encoding="utf-8")
        else:
            p.write_text(json.dumps(data), encoding="utf-8")
        return p

    def test_active_threads_match(self):
        p = self._write("threads.yaml", _THREADS_WITH_PAR)
        sigs = ss._active_thread_signals("PAR Technology", p)
        self.assertEqual(len(sigs), 1)
        self.assertIn("PAR Technology pilot discussion", sigs[0]["description"])
        self.assertEqual(sigs[0]["signal_type"], "active_thread")

    def test_active_threads_no_match(self):
        p = self._write("threads.yaml", _THREADS_NO_PAR)
        sigs = ss._active_thread_signals("PAR Technology", p)
        self.assertEqual(len(sigs), 0)

    def test_market_signals_match(self):
        p = self._write("market_signals.json", _MARKET_SIGNALS_PAR_EXIT)
        sigs = ss._market_signals("PAR Technology", p)
        self.assertEqual(len(sigs), 2)
        # At least one should have exit pattern tags
        all_tags = [tag for s in sigs for tag in (s.get("pattern_tags") or [])]
        self.assertIn(ss.PATTERN_EXIT, all_tags)

    def test_market_signals_no_match(self):
        p = self._write("market_signals.json", _MARKET_SIGNALS_EMPTY)
        sigs = ss._market_signals("PAR Technology", p)
        self.assertEqual(len(sigs), 0)

    def test_social_overlay_match(self):
        p = self._write("social_overlay.json", _SOCIAL_OVERLAY_PAR)
        sigs = ss._social_overlay_signals("PAR Technology", p, lookback_days=90)
        self.assertEqual(len(sigs), 2)

    def test_social_overlay_exit_background_detected(self):
        p = self._write("social_overlay.json", _SOCIAL_OVERLAY_PAR)
        sigs = ss._social_overlay_signals("PAR Technology", p, lookback_days=90)
        oliver = next((s for s in sigs if "Oliver" in s["description"]), None)
        self.assertIsNotNone(oliver)
        self.assertTrue(oliver.get("author_exit_background"))

    def test_artifact_registry_match(self):
        p = self._write("registry.json", _REGISTRY_PAR)
        sig = ss._artifact_signal("PAR Technology", p)
        self.assertIsNotNone(sig)
        self.assertEqual(sig["artifact_id"], "micro_graph:par_technology")
        self.assertEqual(sig["artifact_status"], "stub")

    def test_baseline_contacts_exit_background(self):
        p = self._write("baseline.json", _BASELINE_OLIVER)
        contacts = ss._baseline_contacts("PAR Technology", p)
        self.assertEqual(len(contacts), 2)
        oliver = next((c for c in contacts if "Oliver" in c["name"]), None)
        self.assertIsNotNone(oliver)
        self.assertTrue(oliver["exit_background_signal"])

    def test_ecosystem_watch_list(self):
        p = self._write("ecosystem.json", _ECOSYSTEM_PAR_WATCHED)
        sigs = ss._ecosystem_signals("PAR Technology", p)
        watch_sigs = [s for s in sigs if s["signal_type"] == "watch_list"]
        self.assertEqual(len(watch_sigs), 1)
        self.assertIn("tier: 1", watch_sigs[0]["description"])

    def test_missing_file_returns_empty(self):
        p = self.tmpdir / "does_not_exist.json"
        sigs = ss._market_signals("PAR Technology", p)
        self.assertEqual(sigs, [])


# ── SS5: Pattern scoring + dominant pattern logic ─────────────────────────────

class SS5_PatternScoring(unittest.TestCase):
    """_compute_pattern_scores() and _dominant_pattern() logic."""

    def _make_signal(self, tags: list[str], confidence: str = "medium") -> dict:
        return {"pattern_tags": tags, "confidence": confidence, "signal_id": "test"}

    def test_score_accumulates_by_confidence(self):
        sigs = [
            self._make_signal([ss.PATTERN_EXIT], "high"),   # +3
            self._make_signal([ss.PATTERN_EXIT], "medium"), # +2
            self._make_signal([ss.PATTERN_GROWTH], "low"),  # +1
        ]
        scores = ss._compute_pattern_scores(sigs)
        self.assertEqual(scores[ss.PATTERN_EXIT], 5)
        self.assertEqual(scores[ss.PATTERN_GROWTH], 1)

    def test_exit_background_bonus(self):
        sigs = [{"pattern_tags": [], "confidence": "low", "author_exit_background": True}]
        scores = ss._compute_pattern_scores(sigs)
        self.assertEqual(scores[ss.PATTERN_EXIT], 2)

    def test_dominant_pattern_clear_winner(self):
        scores = {
            ss.PATTERN_EXIT: 8,
            ss.PATTERN_GROWTH: 2,
            ss.PATTERN_DISTRESS: 1,
            ss.PATTERN_CONSOLIDATION: 0,
            ss.PATTERN_COMPETITIVE: 0,
            ss.PATTERN_TRANSITION: 0,
        }
        pattern, confidence = ss._dominant_pattern(scores)
        self.assertEqual(pattern, ss.PATTERN_EXIT)
        self.assertEqual(confidence, "high")

    def test_dominant_pattern_medium_confidence(self):
        scores = {p: 0 for p in ss.KNOWN_PATTERNS if p not in (ss.PATTERN_STABLE, ss.PATTERN_UNKNOWN)}
        scores[ss.PATTERN_EXIT] = 4
        pattern, confidence = ss._dominant_pattern(scores)
        self.assertEqual(pattern, ss.PATTERN_EXIT)
        self.assertEqual(confidence, "medium")

    def test_dominant_pattern_ambiguous_degrades_confidence(self):
        # Two patterns within 2 points of each other
        scores = {p: 0 for p in ss.KNOWN_PATTERNS if p not in (ss.PATTERN_STABLE, ss.PATTERN_UNKNOWN)}
        scores[ss.PATTERN_EXIT] = 7
        scores[ss.PATTERN_TRANSITION] = 6  # within 2 of exit
        pattern, confidence = ss._dominant_pattern(scores)
        self.assertEqual(pattern, ss.PATTERN_EXIT)
        self.assertEqual(confidence, "medium")  # degraded from high

    def test_no_signals_returns_unknown(self):
        pattern, confidence = ss._dominant_pattern({p: 0 for p in ss.KNOWN_PATTERNS
                                                     if p not in (ss.PATTERN_STABLE, ss.PATTERN_UNKNOWN)})
        self.assertEqual(pattern, ss.PATTERN_UNKNOWN)
        self.assertEqual(confidence, "low")

    def test_low_score_returns_low_confidence(self):
        scores = {p: 0 for p in ss.KNOWN_PATTERNS if p not in (ss.PATTERN_STABLE, ss.PATTERN_UNKNOWN)}
        scores[ss.PATTERN_GROWTH] = 2
        _, confidence = ss._dominant_pattern(scores)
        self.assertEqual(confidence, "low")

    def test_known_patterns_set_completeness(self):
        self.assertIn(ss.PATTERN_EXIT, ss.KNOWN_PATTERNS)
        self.assertIn(ss.PATTERN_GROWTH, ss.KNOWN_PATTERNS)
        self.assertIn(ss.PATTERN_DISTRESS, ss.KNOWN_PATTERNS)
        self.assertIn(ss.PATTERN_CONSOLIDATION, ss.KNOWN_PATTERNS)
        self.assertIn(ss.PATTERN_TRANSITION, ss.KNOWN_PATTERNS)
        self.assertIn(ss.PATTERN_UNKNOWN, ss.KNOWN_PATTERNS)

    def test_exit_background_signal_bonus(self):
        # exit_background_signal (baseline contacts) also triggers the bonus
        sigs = [{"pattern_tags": [], "confidence": "low", "exit_background_signal": True}]
        scores = ss._compute_pattern_scores(sigs)
        self.assertEqual(scores[ss.PATTERN_EXIT], 2)


# ── SS6: synthesize_entity_signals() integration ──────────────────────────────

class SS6_SynthesizeIntegration(unittest.TestCase):
    """Full integration test with injected store paths."""

    def setUp(self):
        import shutil
        self.tmp = tempfile.mkdtemp()
        self.tmpdir = Path(self.tmp)
        self.ri_dir = self.tmpdir / "ri_events"
        self.ri_dir.mkdir()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, name: str, data) -> Path:
        p = self.tmpdir / name
        if isinstance(data, str):
            p.write_text(data, encoding="utf-8")
        else:
            p.write_text(json.dumps(data), encoding="utf-8")
        return p

    def _synthesize_par(self, **overrides):
        defaults = dict(
            lookback_days=90,
            baseline_path=self._write("baseline.json", _BASELINE_OLIVER),
            registry_path=self._write("registry.json", _REGISTRY_PAR),
            market_signals_path=self._write("market_signals.json", _MARKET_SIGNALS_PAR_EXIT),
            ri_events_dir=self.ri_dir,
            ri_cache_path=self.tmpdir / "passive_ri.json",
            active_threads_path=self._write("threads.yaml", _THREADS_WITH_PAR),
            ecosystem_path=self._write("ecosystem.json", _ECOSYSTEM_PAR_WATCHED),
            social_overlay_path=self._write("social_overlay.json", _SOCIAL_OVERLAY_PAR),
        )
        defaults.update(overrides)
        return ss.synthesize_entity_signals("PAR Technology", **defaults)

    def test_returns_contract_field(self):
        result = self._synthesize_par()
        self.assertEqual(result["contract"], "rb_entity_signal_synthesis_v1")

    def test_entity_name_preserved(self):
        result = self._synthesize_par()
        self.assertEqual(result["entity"], "PAR Technology")

    def test_signal_count_positive(self):
        result = self._synthesize_par()
        self.assertGreater(result["signal_count"], 0)

    def test_pattern_scores_returned(self):
        result = self._synthesize_par()
        self.assertIn("pattern_scores", result)
        self.assertIsInstance(result["pattern_scores"], dict)

    def test_watch_list_detected(self):
        result = self._synthesize_par()
        self.assertEqual(result["watch_list_status"], "watching")

    def test_active_thread_detected(self):
        result = self._synthesize_par()
        self.assertEqual(len(result["active_threads"]), 1)
        self.assertIn("T-2026-05-par-pilot", result["active_threads"])

    def test_artifact_id_returned(self):
        result = self._synthesize_par()
        self.assertEqual(result["artifact_id"], "micro_graph:par_technology")
        self.assertEqual(result["artifact_status"], "stub")

    def test_baseline_contacts_returned(self):
        result = self._synthesize_par()
        self.assertEqual(len(result["baseline_contacts"]), 2)

    def test_hypothesis_not_empty(self):
        result = self._synthesize_par()
        self.assertTrue(len(result["synthesis_hypothesis"]) > 20)

    def test_opportunity_or_risk_not_empty(self):
        result = self._synthesize_par()
        self.assertTrue(len(result["opportunity_or_risk"]) > 10)


# ── SS6b: aliases= -- RB-DEFECT (2026-09-16) ───────────────────────────────────
# A signal mentioning a tracked entity only by an alternate name (e.g. "NCR"
# for the competitor whose canonical display_name is "NCR Voyix") previously
# never counted toward that entity's synthesis at all. Live impact:
# entity_convergence_scan.py -- RB's actual "connect the dots" engine -- would
# silently under-count convergence for any company with a rebrand, an
# abbreviation, or an acquisition-driven name change.

_MARKET_SIGNALS_NCR_ALIAS_ONLY = {
    "data": {"top": [{
        "company": "NCR", "title": "NCR announces new CEO amid restructuring",
        "pain_point_or_priority": "Leadership transition underway",
        "strategic_relevance": "high", "source_name": "Reuters",
        "published_at": "2026-09-01T10:00:00Z", "url_canonical": "reuters.com/ncr-ceo-2026",
        "source_quality": "high",
    }]}
}

_MARKET_SIGNALS_NCR_BOTH_NAMES = {
    "data": {"top": [
        {
            "company": "NCR", "title": "NCR Voyix announces new CEO amid restructuring",
            "pain_point_or_priority": "Leadership transition underway",
            "strategic_relevance": "high", "source_name": "Reuters",
            "published_at": "2026-09-01T10:00:00Z", "url_canonical": "reuters.com/ncr-ceo-2026",
            "source_quality": "high",
        },
    ]}
}


class SS6b_AliasExpansion(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.tmpdir = Path(self.tmp)
        self.ri_dir = self.tmpdir / "ri_events"
        self.ri_dir.mkdir()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, name: str, data) -> Path:
        p = self.tmpdir / name
        p.write_text(json.dumps(data), encoding="utf-8")
        return p

    def _synthesize(self, entity_name: str, market_signals: dict, **overrides):
        defaults = dict(
            baseline_path=self._write("baseline.json", []),
            registry_path=self._write("registry.json", {"artifacts": []}),
            market_signals_path=self._write("market_signals.json", market_signals),
            ri_events_dir=self.ri_dir,
            ri_cache_path=self.tmpdir / "passive_ri.json",
            active_threads_path=self._write("threads.yaml", {"threads": []}),
            ecosystem_path=self._write("ecosystem.json", {"entities": [], "watch_list": []}),
            social_overlay_path=self._write("social_overlay.json", {"data": {"posts": []}}),
        )
        defaults.update(overrides)
        return ss.synthesize_entity_signals(entity_name, **defaults)

    def test_without_aliases_a_bare_alternate_name_mention_is_missed(self):
        """The gap this closes: no aliases= means "NCR Voyix" never finds a
        signal that only says "NCR" -- confirmed via _entity_matches directly
        earlier; this proves the same gap at the synthesize_entity_signals
        level real callers actually use."""
        result = self._synthesize("NCR Voyix", _MARKET_SIGNALS_NCR_ALIAS_ONLY)
        self.assertEqual(result["signal_count"], 0)

    def test_with_aliases_the_alternate_name_mention_is_found(self):
        result = self._synthesize("NCR Voyix", _MARKET_SIGNALS_NCR_ALIAS_ONLY, aliases=["NCR"])
        self.assertGreater(result["signal_count"], 0)

    def test_signal_matching_both_primary_and_alias_counts_once_not_twice(self):
        """Merged evidence is deduped by signal_id BEFORE pattern scoring --
        a signal whose text happens to satisfy both the primary name and an
        alias must not be double-counted as stronger evidence than it is."""
        result = self._synthesize("NCR Voyix", _MARKET_SIGNALS_NCR_BOTH_NAMES, aliases=["NCR"])
        self.assertEqual(result["signal_count"], 1)

    def test_alias_identical_to_primary_name_is_not_duplicated(self):
        """Passing the same name as both entity_name and an alias (a
        plausible caller mistake) must not double-scan or double-count."""
        result = self._synthesize("NCR Voyix", _MARKET_SIGNALS_NCR_BOTH_NAMES, aliases=["NCR Voyix", "ncr voyix"])
        self.assertEqual(result["signal_count"], 1)

    def test_no_aliases_argument_behaves_exactly_as_before(self):
        result_default = self._synthesize("NCR Voyix", _MARKET_SIGNALS_NCR_BOTH_NAMES)
        result_explicit_none = self._synthesize("NCR Voyix", _MARKET_SIGNALS_NCR_BOTH_NAMES, aliases=None)
        result_empty_list = self._synthesize("NCR Voyix", _MARKET_SIGNALS_NCR_BOTH_NAMES, aliases=[])
        self.assertEqual(result_default["signal_count"], result_explicit_none["signal_count"])
        self.assertEqual(result_default["signal_count"], result_empty_list["signal_count"])


# ── SS7: PAR Technology exit_positioning scenario ─────────────────────────────

class SS7_PARExitScenario(unittest.TestCase):
    """
    The world-class CoS scenario: Oliver Ostertag (M&A background) + President title
    + activist investor pressure + Joe Yetter post → exit_positioning for PAR.
    All signals injected via fixtures; no real data dependency.
    """

    def setUp(self):
        import shutil
        self.tmp = tempfile.mkdtemp()
        self.tmpdir = Path(self.tmp)
        self.ri_dir = self.tmpdir / "ri_events"
        self.ri_dir.mkdir()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _full_par_synthesis(self):
        """Run full synthesis with all PAR exit signals loaded."""
        return ss.synthesize_entity_signals(
            "PAR Technology",
            lookback_days=90,
            baseline_path=self.tmpdir / "baseline.json",
            registry_path=self.tmpdir / "registry.json",
            market_signals_path=self.tmpdir / "market_signals.json",
            ri_events_dir=self.ri_dir,
            ri_cache_path=self.tmpdir / "passive_ri.json",
            active_threads_path=self.tmpdir / "threads.yaml",
            ecosystem_path=self.tmpdir / "ecosystem.json",
            social_overlay_path=self.tmpdir / "social_overlay.json",
        )

    def _write_all_stores(self):
        (self.tmpdir / "baseline.json").write_text(json.dumps(_BASELINE_OLIVER))
        (self.tmpdir / "registry.json").write_text(json.dumps(_REGISTRY_PAR))
        (self.tmpdir / "market_signals.json").write_text(json.dumps(_MARKET_SIGNALS_PAR_EXIT))
        (self.tmpdir / "threads.yaml").write_text(_THREADS_NO_PAR)
        (self.tmpdir / "ecosystem.json").write_text(json.dumps(_ECOSYSTEM_PAR_WATCHED))
        (self.tmpdir / "social_overlay.json").write_text(json.dumps(_SOCIAL_OVERLAY_PAR))

    def test_exit_pattern_dominant(self):
        """With M&A background + activist signals, exit_positioning should dominate."""
        self._write_all_stores()
        result = self._full_par_synthesis()
        self.assertEqual(result["dominant_pattern"], ss.PATTERN_EXIT,
                         f"Expected exit_positioning but got {result['dominant_pattern']}. "
                         f"Scores: {result['pattern_scores']}")

    def test_exit_pattern_score_highest(self):
        self._write_all_stores()
        result = self._full_par_synthesis()
        scores = result["pattern_scores"]
        max_pattern = max(scores, key=lambda p: scores[p])
        self.assertEqual(max_pattern, ss.PATTERN_EXIT,
                         f"exit_positioning should have highest score. Scores: {scores}")

    def test_oliver_exit_background_contributes(self):
        """Baseline contacts with M&A background should add exit signal weight."""
        self._write_all_stores()
        result = self._full_par_synthesis()
        # Oliver and Joe are in baseline_contacts; Oliver has exit background
        oliver = next((c for c in result["baseline_contacts"] if "Oliver" in c["name"]), None)
        self.assertIsNotNone(oliver)
        self.assertTrue(oliver["exit_background_signal"],
                        "Oliver's M&A background should be flagged as exit_background_signal")

    def test_social_signal_author_exit_background(self):
        """Social overlay posts from M&A-background authors should flag author_exit_background."""
        self._write_all_stores()
        result = self._full_par_synthesis()
        social_sigs = [s for s in result["signals"] if s["signal_type"] == "social_post"]
        oliver_post = next((s for s in social_sigs if "Oliver" in s["description"]), None)
        self.assertIsNotNone(oliver_post, "Should have a social signal from Oliver")
        self.assertTrue(oliver_post.get("author_exit_background"),
                        "Oliver's post should have author_exit_background=True")

    def test_opportunity_risk_exit_language(self):
        """Opportunity/risk text for exit_positioning should mention decision-makers or timing."""
        self._write_all_stores()
        result = self._full_par_synthesis()
        if result["dominant_pattern"] == ss.PATTERN_EXIT:
            opp = result["opportunity_or_risk"].lower()
            self.assertTrue(
                "decision" in opp or "timing" in opp or "risk" in opp,
                f"Expected exit risk language, got: {result['opportunity_or_risk']}"
            )

    def test_hypothesis_contains_par(self):
        self._write_all_stores()
        result = self._full_par_synthesis()
        self.assertIn("PAR", result["synthesis_hypothesis"])

    def test_signal_count_includes_all_stores(self):
        """With all stores populated, signal count should be >= 5."""
        self._write_all_stores()
        result = self._full_par_synthesis()
        self.assertGreaterEqual(result["signal_count"], 5,
                                f"Expected ≥5 signals from all stores; got {result['signal_count']}")

    def test_empty_stores_returns_unknown(self):
        """With empty stores, pattern should be unknown."""
        (self.tmpdir / "baseline.json").write_text("[]")
        (self.tmpdir / "registry.json").write_text('{"artifacts": []}')
        (self.tmpdir / "market_signals.json").write_text(json.dumps(_MARKET_SIGNALS_EMPTY))
        (self.tmpdir / "threads.yaml").write_text(_THREADS_NO_PAR)
        (self.tmpdir / "ecosystem.json").write_text('{"watch_list": [], "entities": []}')
        (self.tmpdir / "social_overlay.json").write_text(json.dumps(_SOCIAL_OVERLAY_EMPTY))
        result = self._full_par_synthesis()
        self.assertEqual(result["dominant_pattern"], ss.PATTERN_UNKNOWN)
        self.assertEqual(result["signal_count"], 0)


# ── SS8: CLI smoke tests ───────────────────────────────────────────────────────

class SS8_CLI(unittest.TestCase):
    """CLI entry point smoke tests."""

    def _run_cli(self, args: list[str]) -> tuple[int, str]:
        """Run the CLI main() with args, return (exit_code, stdout)."""
        import io
        from contextlib import redirect_stdout
        import sys

        original_argv = sys.argv
        try:
            sys.argv = ["signal_synthesis.py"] + args
            stdout_capture = io.StringIO()
            try:
                with redirect_stdout(stdout_capture):
                    exit_code = ss.main()
            except SystemExit as e:
                exit_code = e.code if isinstance(e.code, int) else 1
            return exit_code or 0, stdout_capture.getvalue()
        finally:
            sys.argv = original_argv

    def test_json_flag_returns_valid_json(self):
        code, output = self._run_cli(["--entity", "ZZZ Nonexistent Corp XYZ", "--json"])
        self.assertEqual(code, 0)
        data = json.loads(output)
        self.assertEqual(data["contract"], "rb_entity_signal_synthesis_v1")

    def test_unknown_entity_returns_unknown_pattern(self):
        code, output = self._run_cli(["--entity", "ZZZ Nonexistent Corp XYZ", "--json"])
        self.assertEqual(code, 0)
        data = json.loads(output)
        self.assertEqual(data["dominant_pattern"], ss.PATTERN_UNKNOWN)

    def test_json_output_has_required_keys(self):
        code, output = self._run_cli(["--entity", "ZZZ Nonexistent Corp XYZ", "--json"])
        data = json.loads(output)
        required_keys = [
            "entity", "signal_count", "signals", "pattern_scores",
            "dominant_pattern", "pattern_confidence", "synthesis_hypothesis",
            "opportunity_or_risk", "active_threads", "watch_list_status",
            "artifact_id", "artifact_status", "baseline_contacts",
            "staleness_flags", "contract",
        ]
        for k in required_keys:
            self.assertIn(k, data, f"Missing key: {k}")

    def test_text_output_no_exception(self):
        code, output = self._run_cli(["--entity", "ZZZ Nonexistent Corp XYZ"])
        self.assertEqual(code, 0)
        self.assertIn("Entity:", output)

    def test_days_flag_accepted(self):
        code, output = self._run_cli(["--entity", "ZZZ Corp", "--days", "30", "--json"])
        self.assertEqual(code, 0)
        data = json.loads(output)
        self.assertEqual(data["lookback_days"], 30)

    def test_missing_entity_flag_exits(self):
        code, _ = self._run_cli([])
        self.assertNotEqual(code, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
