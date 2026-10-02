from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))
import fdd_release_monitor as fdd


HTML = b'''<table><tbody>
<tr><td>Five Guys Restaurants, LLC</td><td>Sep 16, 2026</td><td class="cat-col">Fast Food &amp; Quick Service</td><td class="type-col">Complete FDD</td><td><a class="viewbtn" href="https://fdd.example/five-guys-2026/">View FDD</a></td></tr>
<tr><td>Unrelated Plumbing</td><td>Sep 15, 2026</td><td class="cat-col">Home Services</td><td class="type-col">Complete FDD</td><td><a class="viewbtn" href="https://fdd.example/plumbing/">View FDD</a></td></tr>
</tbody></table>'''


def test_parse_and_match_legal_suffix():
    rows = fdd.parse_index(HTML)
    assert len(rows) == 2
    assert rows[0]["category"] == "Fast Food & Quick Service"
    assert fdd._match_brand(rows[0]["name"], ["Five Guys"]) == "Five Guys"


def test_first_run_baselines_then_new_release_queues_review(tmp_path, monkeypatch):
    monkeypatch.setattr(fdd, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(fdd, "RESULT_PATH", tmp_path / "result.json")
    monkeypatch.setattr(fdd.wr, "mandatory_restaurant_brands", lambda: ["Five Guys"])
    monkeypatch.setattr(fdd, "archive_fdd_for_account", lambda candidate, **kwargs: {"status": "test_archived"})
    first = fdd.run(today=date(2026, 9, 16), fetcher=lambda: HTML)
    assert first["first_run_baseline"] is True
    assert first["new_candidates"] == []

    newer = HTML.replace(b"Sep 16, 2026", b"Sep 17, 2026").replace(
        b"five-guys-2026/", b"five-guys-2026-amended/"
    )
    second = fdd.run(today=date(2026, 9, 17), fetcher=lambda: newer)
    assert second["first_run_baseline"] is False
    assert len(second["new_candidates"]) == 1
    assert second["new_candidates"][0]["status"] == "pending_review"
    assert second["new_candidates"][0]["brand"] == "Five Guys"
    assert second["new_candidates"][0]["account_archive"]["status"] == "test_archived"


def test_archive_requires_established_brief(tmp_path, monkeypatch):
    monkeypatch.setattr(fdd.cpc, "ROOT", tmp_path / "customers_prospects")
    monkeypatch.setattr(fdd.cpc, "load_registry", lambda: {
        "registry": [{"account_id": "acct-five-guys", "aliases": ["Five Guys"]}]
    })
    result = fdd.archive_fdd_for_account({"brand": "Five Guys", "source_url": "https://example/fdd"})
    assert result["status"] == "skipped_no_established_background_brief"


def test_archive_discovers_validates_and_stores_pdf(tmp_path, monkeypatch):
    root = tmp_path / "customers_prospects"
    account = root / "accounts" / "five-guys"
    (account / "briefs" / "current").mkdir(parents=True)
    (account / "briefs" / "current" / "Background_Brief.md").write_text("# Five Guys")
    (account / "account.json").write_text('{"display_name":"Five Guys","aliases":[]}')
    monkeypatch.setattr(fdd.cpc, "ROOT", root)
    monkeypatch.setattr(fdd.cpc, "load_registry", lambda: {
        "registry": [{"account_id": "acct-five-guys", "aliases": []}]
    })
    page = b'<a href="/files/five-guys.pdf">View the Full FDD</a>'
    pdf = b"%PDF-1.7\nfixture"

    def fetch(url):
        if url.endswith(".pdf"):
            return pdf, url, "application/pdf"
        return page, "https://example/fdd/", "text/html"

    result = fdd.archive_fdd_for_account({
        "brand": "Five Guys", "fdd_date": "Sep 16, 2026",
        "document_type": "Complete FDD", "source_url": "https://example/fdd/",
    }, fetch_document=fetch)
    assert result["status"] == "archived"
    stored = root.parent / result["pdf_path"]
    assert stored.read_bytes() == pdf
    assert stored.parent == account / "sources" / "fdd"


def test_archive_rejects_login_or_html_placeholder(tmp_path, monkeypatch):
    root = tmp_path / "customers_prospects"
    account = root / "accounts" / "five-guys"
    (account / "briefs" / "current").mkdir(parents=True)
    (account / "briefs" / "current" / "Background_Brief.md").write_text("# Five Guys")
    (account / "account.json").write_text('{"display_name":"Five Guys"}')
    monkeypatch.setattr(fdd.cpc, "ROOT", root)
    monkeypatch.setattr(fdd.cpc, "load_registry", lambda: {
        "registry": [{"account_id": "acct-five-guys", "aliases": []}]
    })
    result = fdd.archive_fdd_for_account(
        {"brand": "Five Guys", "source_url": "https://example/fdd/"},
        fetch_document=lambda url: (b"<html>Log in</html>", url, "text/html"),
    )
    assert result["status"] == "blocked_no_public_pdf"
    assert not (account / "sources").exists()


def test_morning_pipeline_runs_fdd_monitor():
    source = (ROOT / "system" / "scripts" / "morning_pipeline.py").read_text()
    assert '_step("fdd_release_monitor"' in source
