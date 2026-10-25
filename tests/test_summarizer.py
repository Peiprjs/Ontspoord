from unittest.mock import MagicMock, patch
import pytest

from src.models import DisruptionEvent
from src.summarizer import (
    DisruptionSummarizer,
    is_no_message,
    has_substantive_strikes,
    sanitize_telegram_html,
)


@pytest.fixture
def no_strike_event():
    return DisruptionEvent(
        provider="9292",
        mode="general",
        title="Stakingen in het openbaar vervoer",
        region_or_route="Nederland",
        impact="Tot en met 4 november wordt er niet gestaakt in het openbaar vervoer.",
        advice="Reis volgens reguliere dienstregeling.",
        source_url="https://9292.nl/actueel/staking-ov/",
        is_strike=True,
    )


@pytest.fixture
def actual_strike_event():
    return DisruptionEvent(
        provider="NS",
        mode="train",
        title="Landelijke staking",
        region_or_route="Landelijk",
        impact="Geen treinen",
        advice="Reis niet",
        source_url="https://ns.nl",
        is_strike=True,
    )


@pytest.fixture
def actual_work_event():
    return DisruptionEvent(
        provider="ProRail",
        mode="train",
        title="Spoorvernieuwing",
        region_or_route="Utrecht - Amsterdam",
        impact="Minder treinen",
        advice="Check reisplanner",
        source_url="https://ns.nl",
        is_strike=False,
    )


def test_is_no_message():
    assert is_no_message("NO_MESSAGE") is True
    assert is_no_message("NO MESSAGE") is True
    assert is_no_message("<b>GEEN_BERICHT</b>") is True
    assert is_no_message("skip") is True
    assert is_no_message("Er zijn voorlopig geen stakingen gepland.") is True
    assert is_no_message("") is True
    assert is_no_message(None) is True
    assert is_no_message("<b>🔴 OV MONITOR: STAKINGEN</b>\nForse uitval van treinen") is False


def test_has_substantive_strikes(no_strike_event, actual_strike_event):
    assert has_substantive_strikes([no_strike_event]) is False
    assert has_substantive_strikes([actual_strike_event]) is True
    assert has_substantive_strikes([]) is False


def test_sanitize_strips_key_takeaways():
    text = (
        "<b>🟢 OV MONITOR: ALGEMENE STATUS</b>\n"
        "• <b>Trein:</b> Rijdt goed\n\n"
        "<b>Key Takeaways:</b>\n"
        "Plan your trip ahead\n\n"
        "<b>Belangrijkste aandachtspunten:</b>\n"
        "Check vertrektijden"
    )
    cleaned = sanitize_telegram_html(text)
    assert "Key Takeaways" not in cleaned
    assert "Belangrijkste aandachtspunten" not in cleaned
    assert "Plan your trip ahead" not in cleaned
    assert "Check vertrektijden" not in cleaned


def test_gemma_decides_no_strike_message_and_green_status(no_strike_event):
    mock_client = MagicMock()
    mock_client.chat.side_effect = [
        # Message 1 (strikes): Gemma decides no message is needed
        {"message": {"content": "NO_MESSAGE"}},
        # Message 3 (general status): Green status
        {"message": {"content": "<b>🟢 OV MONITOR NEDERLAND: ALGEMENE STATUS</b>\n\n<b>Netwerkstatus:</b>\nREGULIERE DIENSTREGELING\n\n<b>Overzicht per modaliteit:</b>\n• <b>Treinverbindingen:</b> Goed\n• <b>Busnetwerk:</b> Goed"}}
    ]

    with patch("ollama.Client", return_value=mock_client):
        summarizer = DisruptionSummarizer(language="nl")
        report = summarizer.generate_report([no_strike_event])

        assert report.strikes_message is None
        assert report.works_message is None
        assert "🟢" in report.general_status_message
        assert "Key Takeaways" not in report.general_status_message
        assert "Belangrijkste aandachtspunten" not in report.general_status_message


def test_gemma_removes_key_takeaways_even_if_generated():
    mock_client = MagicMock()
    mock_client.chat.side_effect = [
        {"message": {"content": (
            "<b>🟢 PT MONITOR: GENERAL STATUS</b>\n\n"
            "<b>Network Status:</b>\nREGULAR SCHEDULE\n\n"
            "<b>Overview by Modality:</b>\n• <b>Train:</b> Good\n\n"
            "<b>Key Takeaways:</b>\n• Commuters should be aware of this takeaway."
        )}}
    ]

    with patch("ollama.Client", return_value=mock_client):
        summarizer = DisruptionSummarizer(language="en")
        report = summarizer.generate_report([])

        assert "Key Takeaways" not in report.general_status_message
        assert "Commuters should be aware of this takeaway" not in report.general_status_message


def test_fallback_report_skips_negative_strike_notice(no_strike_event):
    # Tests that even in fallback mode, an event saying "no strikes until Nov 4"
    # skips sending the strike message and sets status to green without key takeaways
    summarizer = DisruptionSummarizer(language="nl")
    report = summarizer._fallback_report([no_strike_event], "25-10 t/m 01-11")

    assert report.strikes_message is None
    assert "🟢" in report.general_status_message
    assert "Belangrijkste aandachtspunten" not in report.general_status_message
    assert "Key Takeaways" not in report.general_status_message


def test_fallback_report_includes_actual_strikes_and_red_status(actual_strike_event):
    summarizer = DisruptionSummarizer(language="nl")
    report = summarizer._fallback_report([actual_strike_event], "25-10 t/m 01-11")

    assert report.strikes_message is not None
    assert "🔴" in report.general_status_message
    assert "Belangrijkste aandachtspunten" not in report.general_status_message
    assert "Key Takeaways" not in report.general_status_message
