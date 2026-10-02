from datetime import date
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))
import intelligence_insight_ranking as ranking


def _item(title, signal_type="general", confidence="medium", disposition="monitor", entities=None):
    return {"title": title, "summary": title, "confidence": confidence,
            "disposition": disposition, "source_refs": ["source-1"],
            "extras": {"signal_type": signal_type, "pub_date": "2026-09-14",
                       "entities": entities or []}}


def test_account_relevant_material_event_outranks_generic_news():
    sections = {
        "weekly_plan_focus": [{"title": "Advance McDonald's Genius opportunity"}],
        "what_todd_doesnt_know_yet": [
            _item("General restaurant trend"),
            _item("McDonald's announces acquisition", "acquisition", "high", "act_today", ["McDonald's"]),
        ],
    }
    ranked = ranking.rank_insights(sections, today=date(2026, 9, 14))
    assert ranked[0]["item"]["title"] == "McDonald's announces acquisition"
    assert ranked[0]["score_breakdown"]["todd_relevance"] > 0


def test_ranking_is_deduplicated_and_capped():
    same = _item("Toast launches product", "product-launch")
    sections = {"what_todd_doesnt_know_yet": [same], "new_intelligence_today": [same]}
    assert len(ranking.rank_insights(sections, today=date(2026, 9, 14), limit=5)) == 1


def test_score_breakdown_sums_to_total():
    result = ranking.score_item(_item("New CEO", "exec-change", "high"),
                                objective_tokens=set(), today=date(2026, 9, 14))
    assert result["score"] == sum(result["score_breakdown"].values())
