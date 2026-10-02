"""RB's operating week ends Friday close of business."""
from datetime import date
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import rb_core as core  # noqa: E402


def _loop(loop_id: str, target: date) -> core.Loop:
    return core.Loop(loop_id, date(2026, 8, 1), loop_id, "test", target, "open", False)


def test_thursday_this_week_stops_at_friday():
    buckets = core.loops_by_status([
        _loop("fri", date(2026, 8, 14)),
        _loop("sat", date(2026, 8, 15)),
        _loop("sun", date(2026, 8, 16)),
    ], date(2026, 8, 13))
    assert [x.id for x in buckets["this_week"]] == ["fri"]
    assert [x.id for x in buckets["future"]] == ["sat", "sun"]


def test_weekend_rolls_to_coming_friday():
    buckets = core.loops_by_status([
        _loop("next-fri", date(2026, 8, 21)),
        _loop("next-sat", date(2026, 8, 22)),
    ], date(2026, 8, 15))
    assert [x.id for x in buckets["this_week"]] == ["next-fri"]
    assert [x.id for x in buckets["future"]] == ["next-sat"]
