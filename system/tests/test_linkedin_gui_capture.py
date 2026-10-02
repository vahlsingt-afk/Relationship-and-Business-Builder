from pathlib import Path
import sys

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import linkedin_gui_capture as lgc


def test_normalizes_direction_and_skips_sponsored():
    payload = {
        "captured_at": "2026-09-17T14:00:00Z",
        "conversations": [
            {"name": "Hannah Hall", "display_time": "8:22 AM", "preview": "You: See you there", "unread": False},
            {"name": "John McCarthy", "display_time": "Sep 16", "preview": "John: Can you join?", "unread": True},
            {"name": "Ian Siegel", "display_time": "Sep 16", "preview": "Sponsored Try this", "sponsored": True},
        ],
    }
    rows = lgc.normalize_rows(payload)
    assert len(rows) == 2
    assert rows[0]["direction"] == "outbound"
    assert rows[0]["response_status"] == "responded"
    assert rows[1]["direction"] == "inbound"
    assert rows[1]["unread"] is True


def test_reconciles_same_day_notification_without_double_counting():
    existing = {"messages": [{
        "from": {"name": "John McCarthy"},
        "to": [{"name": "Todd Vahlsing"}],
        "date": "2026-09-16T20:23:39+00:00",
        "direction": "inbound",
        "content": "",
    }]}
    incoming = lgc.normalize_rows({
        "captured_at": "2026-09-17T14:00:00Z",
        "conversations": [{
            "name": "John McCarthy", "display_time": "Sep 16",
            "preview": "John: Can you join?", "unread": False,
        }],
    })
    reconciled, additions, enriched = lgc.reconcile_with_existing(existing, incoming)
    assert enriched == 1
    assert additions == []
    assert reconciled["messages"][0]["content"] == "Can you join?"
