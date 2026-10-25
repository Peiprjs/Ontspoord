from datetime import datetime

from src.models import DigestReport, DisruptionEvent


def test_format_for_prompt_strips_bus_details_by_default():
    event = DisruptionEvent(
        provider="Arriva",
        mode="bus",
        title="Bus omleiding",
        region_or_route="Lijn 12, Halte Centrum (tijdelijk)",
        start_time=datetime(2026, 10, 25, 8, 0),
        end_time=datetime(2026, 10, 25, 10, 0),
        impact="Omleiding",
        advice="Check app",
        source_url="https://example.com",
    )

    prompt_text = event.format_for_prompt()
    assert "Route/Region: Arriva regionaal busvervoer" in prompt_text
    assert "Lijn 12" not in prompt_text


def test_format_for_prompt_keeps_bus_details_when_requested():
    event = DisruptionEvent(
        provider="Arriva",
        mode="bus",
        title="Bus omleiding",
        region_or_route="Lijn 12, Halte Centrum (tijdelijk)",
        impact="Omleiding",
        advice="Check app",
        source_url="https://example.com",
    )

    prompt_text = event.format_for_prompt(include_bus_details=True)
    assert "Route/Region: Lijn 12, Halte Centrum (tijdelijk)" in prompt_text


def test_digest_report_get_messages_to_send_filters_empty_values():
    report = DigestReport(
        strikes_message="  ",
        works_message="\nWerkzaamheden\n",
        general_status_message="  Status  ",
    )

    assert report.get_messages_to_send() == ["Werkzaamheden", "Status"]
