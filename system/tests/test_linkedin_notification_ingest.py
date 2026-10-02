from pathlib import Path
import sys


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import linkedin_notification_ingest as lni
import linkedin_messaging as lim


def _thread(*, name="Jane Smith via LinkedIn", subject="Jane Smith sent you a message",
            labels=None, thread_id="abc123"):
    return {
        "thread_id": thread_id,
        "subject": subject,
        "last_message_at": "Wed, 16 Sep 2026 18:30:00 -0500",
        "last_message_from": {
            "email": "messages-noreply@linkedin.com",
            "name": name,
        },
        "labels": labels or ["CATEGORY_SOCIAL"],
        "unread": True,
        "snippet": "Jane Smith sent you a message: Can we connect tomorrow?",
    }


def test_archived_message_notification_is_captured():
    message = lni.notification_to_message(_thread())
    assert message is not None
    assert message["from"]["name"] == "Jane Smith"
    assert message["direction"] == "inbound"
    assert message["response_status"] == "unknown"
    assert "INBOX" not in message["gmail_labels"]


def test_trash_notification_is_captured_and_non_message_is_rejected():
    message = lni.notification_to_message(_thread(labels=["TRASH", "CATEGORY_SOCIAL"]))
    assert message is not None
    assert "TRASH" in message["gmail_labels"]
    archive = _thread(
        name="LinkedIn",
        subject="Your full LinkedIn data archive is ready!",
        thread_id="archive1",
    )
    assert lni.notification_to_message(archive) is None


def test_merge_is_idempotent():
    message = lni.notification_to_message(_thread())
    merged, added = lni.merge_messages({"source": "linkedin_data_export", "messages": []}, [message])
    assert added == 1
    merged_again, added_again = lni.merge_messages(merged, [message])
    assert added_again == 0
    assert len(merged_again["messages"]) == 1


def test_legacy_hyphenated_self_url_is_repaired_on_load(tmp_path, monkeypatch):
    inbox = tmp_path / "linkedin.messages.json"
    inbox.write_text(__import__("json").dumps({"messages": [{
        "direction": "inbound",
        "from": {"name": "Todd Vahlsing", "profile_url": "https://www.linkedin.com/in/todd-vahlsing", "is_self": False},
        "to": [{"name": "Someone Else"}],
    }]}))
    monkeypatch.setattr(lim, "INBOX_PATH", inbox)
    loaded = lim.load_inbox()
    assert loaded["messages"][0]["direction"] == "outbound"
    assert loaded["messages"][0]["from"]["is_self"] is True
