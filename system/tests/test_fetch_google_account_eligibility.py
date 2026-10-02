from unittest.mock import patch

from system.scripts import fetch_google


def test_all_skips_accounts_that_disallow_automated_google_fetch():
    accounts = [
        {"id": "personal", "enabled": True, "provider": "google", "feeds": ["email"]},
        {"id": "global-payments", "enabled": True, "provider": "microsoft365",
         "automated_fetch": False, "feeds": ["email", "calendar"]},
    ]
    with patch.object(fetch_google.core, "load_inbox_accounts", return_value=accounts):
        selected = fetch_google._accounts_for("all")
    assert [a["id"] for a in selected] == ["personal"]
