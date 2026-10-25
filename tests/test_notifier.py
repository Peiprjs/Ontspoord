from unittest.mock import MagicMock, patch

from src.models import DigestReport
from src.notifier import TelegramNotifier, chunk_html_message


def test_chunk_html_message_splits_long_text():
    text = "A" * 20 + "\n\n" + "B" * 20
    chunks = chunk_html_message(text, max_length=25)
    assert len(chunks) == 2
    assert chunks[0] == "A" * 20
    assert chunks[1] == "B" * 20


def test_send_digest_returns_false_when_unconfigured():
    notifier = TelegramNotifier(token="", chat_id="")
    assert notifier.send_digest("<b>Hello</b>") is False


def test_send_report_sends_messages_and_sleeps_between(monkeypatch):
    notifier = TelegramNotifier(token="123:abc", chat_id="123")
    report = DigestReport(
        strikes_message="m1",
        works_message="m2",
        general_status_message="m3",
    )
    sent = []

    monkeypatch.setattr(notifier, "send_digest", lambda message: sent.append(message) or True)
    sleep_calls = []
    monkeypatch.setattr("src.notifier.time.sleep", lambda value: sleep_calls.append(value))

    assert notifier.send_report(report) is True
    assert sent == ["m1", "m2", "m3"]
    assert sleep_calls == [0.5, 0.5]


def test_send_chunk_with_retry_uses_plain_text_fallback():
    notifier = TelegramNotifier(token="123:abc", chat_id="123")
    resp_bad = MagicMock(status_code=400, text="can't parse entities")
    resp_ok = MagicMock(status_code=200, text="ok")

    with patch("httpx.Client.post", side_effect=[resp_bad, resp_ok]):
        assert notifier._send_chunk_with_retry("<b>bad</b>") is True
