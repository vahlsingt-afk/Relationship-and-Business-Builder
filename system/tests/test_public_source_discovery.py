import sys
from datetime import date
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import public_source_discovery as psd


RSS = b'''<rss><channel>
<item><title>Toast expands restaurant technology platform</title><source url="https://newtrade.example">New Trade</source></item>
<item><title>Toast adds payments product</title><source url="https://newtrade.example">New Trade</source></item>
<item><title>Toast digital strategy</title><source url="https://known.example">Known</source></item>
</channel></rss>'''


def test_parse_publishers_requires_entity_in_headline():
    rows = psd.parse_publishers(RSS, "Toast")
    assert len(rows) == 3
    assert rows[0]["domain"] == "newtrade.example"


def test_run_queues_repeated_unconfigured_domain_without_mutating_sources(tmp_path):
    with patch.object(psd, "CACHE_PATH", tmp_path / "discovery.json"), \
         patch.object(psd, "ROTATION_PATH", tmp_path / "rotation.json"), \
         patch.object(psd, "entity_pool", return_value=["Toast", "PAR Technology"]), \
         patch.object(psd, "_configured_domains", return_value={"known.example"}):
        def fake_fetch(entity):
            return psd.parse_publishers(RSS.replace(b"Toast", entity.encode()), entity)
        result = psd.run(today=date(2026, 9, 14), batch_size=2, fetcher=fake_fetch)
    candidate = next(c for c in result["candidates"] if c["domain"] == "newtrade.example")
    assert candidate["status"] == "observing"
    assert candidate["distinct_entities"] == 2
    assert result["policy"].startswith("proposal_only")

    # A second day's independent observation promotes it for review.
    with patch.object(psd, "CACHE_PATH", tmp_path / "discovery.json"), \
         patch.object(psd, "ROTATION_PATH", tmp_path / "rotation.json"), \
         patch.object(psd, "entity_pool", return_value=["Toast", "PAR Technology"]), \
         patch.object(psd, "_configured_domains", return_value={"known.example"}):
        result2 = psd.run(today=date(2026, 9, 15), batch_size=2, fetcher=fake_fetch)
    candidate2 = next(c for c in result2["candidates"] if c["domain"] == "newtrade.example")
    assert candidate2["status"] == "pending_review"


def test_pipeline_runs_source_discovery_before_capture_assessment():
    src = (Path(__file__).resolve().parent.parent / "scripts" / "morning_pipeline.py").read_text()
    assert src.index('_step("public_source_discovery"') < src.index('_step("capture_process_all"')
