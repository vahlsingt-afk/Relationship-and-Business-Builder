import json
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import gatherer  # noqa: E402


def _item(title, date, *, domain="restaurant_technology", signal="deployment", entities=None, url="https://example.com/a"):
    return {"title": title, "summary": "Fresh change", "confidence": "medium", "source_refs": ["Example"],
            "extras": {"domain": domain, "entities": entities or [], "signal_type": signal,
                       "source_url": url, "pub_date": date, "source_name": "Example"}}


def test_gatherer_enforces_window_dedup_and_world_noise(tmp_path):
    ecosystem = tmp_path / "ecosystem.json"
    ecosystem.write_text(json.dumps({"entities": [{"name": "Brand A", "status": "active", "aliases": ["A Brand"]}]}))
    web = {
        "industry_technology": [
            _item("Brand A deploys a new platform", "2026-10-02"),
            _item("Duplicate", "2026-10-02", url="https://example.com/a?utm_source=x"),
            _item("Old item", "2026-09-29", url="https://example.com/old"),
        ],
        "world_national": [_item("Unrelated politics", "2026-10-02", domain="world_national", signal="general", url="https://example.com/world")],
    }
    packet = gatherer.build_packet(web, now=datetime(2026, 10, 2, 12, tzinfo=timezone.utc), ecosystem_path=ecosystem)
    assert packet["contract"] == gatherer.CONTRACT
    assert len(packet["changes"]) == 1
    assert packet["changes"][0]["entities"] == ["Brand A"]
    assert packet["stats"]["duplicates_removed"] == 1
    assert packet["stats"]["excluded_outside_window"] == 1
    assert packet["stats"]["excluded_world_noise"] == 1
    assert packet["hunter_escalations"][0]["recommended_playbook"] == "customer_deployment_validation"


def test_gatherer_marks_every_change_as_candidate(tmp_path):
    ecosystem = tmp_path / "ecosystem.json"
    ecosystem.write_text('{"entities": []}')
    packet = gatherer.build_packet({"industry_primary": [_item("Restaurant expansion", "2026-10-02", signal="expansion")]},
                                   now=datetime(2026, 10, 2, 18, tzinfo=timezone.utc), ecosystem_path=ecosystem)
    assert packet["changes"][0]["verification_state"] == "candidate"
    assert "not proof" in packet["coverage"]["limitations"][0]
