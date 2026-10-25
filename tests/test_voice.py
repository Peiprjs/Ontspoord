from unittest.mock import MagicMock, patch
import pytest

from src.models import DisruptionEvent
from src.voice import (
    CALM_MAX_SECONDS,
    DISRUPTIONS_MAX_SECONDS,
    clean_voice_script,
    count_spoken_words,
    enforce_time_limit,
    estimate_speech_duration_seconds,
    generate_voice_script,
    ElevenLabsVoiceService,
    model_supports_audio_tags,
    prepare_script_for_model,
    send_voice_summary,
)
from src.notifier import TelegramNotifier


@pytest.fixture
def sample_strike_event():
    return DisruptionEvent(
        provider="NS",
        mode="train",
        title="Landelijke staking vakbonden",
        region_or_route="Landelijk",
        impact="Geen treinverkeer mogelijk",
        advice="Reis niet met de trein",
        source_url="https://www.ns.nl",
        is_strike=True,
    )


@pytest.fixture
def sample_work_event():
    return DisruptionEvent(
        provider="ProRail",
        mode="train",
        title="Groot onderhoud spoor",
        region_or_route="Utrecht - Amsterdam",
        impact="Minder treinen en omreizen",
        advice="Check de reisplanner",
        source_url="https://www.ns.nl",
        is_strike=False,
    )


def test_count_spoken_words():
    script = "[announcer] Attention commuters! [urgent] Strike actions are active. [calm] Safe travels!"
    count = count_spoken_words(script)
    assert count == 8


def test_clean_voice_script():
    raw_llm = (
        "<thought>Thinking about the speech</thought>\n"
        "**[announcer]** <b>Attention travelers!</b> [urgent] Track works ahead. "
        "```\n[sigh] Expect delays.\n```"
    )
    cleaned = clean_voice_script(raw_llm)
    assert "<thought>" not in cleaned
    assert "<b>" not in cleaned
    assert "**" not in cleaned
    assert "```" not in cleaned
    assert "[announcer]" in cleaned
    assert "[urgent]" in cleaned
    assert "[sigh]" in cleaned


def test_enforce_time_limit_calm():
    # Long text with 60 words (way over 15s)
    long_script = "[cheerfully] " + "word " * 60 + "[calm] Goodbye."
    trimmed = enforce_time_limit(long_script, max_seconds=CALM_MAX_SECONDS)
    duration = estimate_speech_duration_seconds(trimmed)
    assert duration <= CALM_MAX_SECONDS


def test_enforce_time_limit_disruptions():
    # Very long text with 150 words (way over 45s)
    long_script = "[announcer] " + "disruption " * 150 + "[calm] Goodbye."
    trimmed = enforce_time_limit(long_script, max_seconds=DISRUPTIONS_MAX_SECONDS)
    duration = estimate_speech_duration_seconds(trimmed)
    assert duration <= DISRUPTIONS_MAX_SECONDS


@pytest.mark.parametrize("lang", ["nl", "en"])
def test_generate_voice_script_calm_strictly_under_15_seconds(lang):
    # Empty events = calm condition
    events = []
    script = generate_voice_script(events, language=lang)
    est_duration = estimate_speech_duration_seconds(script)

    assert est_duration <= CALM_MAX_SECONDS, f"Calm script exceeded 15s limit: {est_duration}s ({script})"
    assert "[" in script and "]" in script


@pytest.mark.parametrize("lang", ["nl", "en"])
def test_generate_voice_script_disruptions_strictly_under_45_seconds(sample_strike_event, sample_work_event, lang):
    conditions = [
        [sample_strike_event],
        [sample_work_event],
        [sample_strike_event, sample_work_event],
    ]

    for events in conditions:
        script = generate_voice_script(events, language=lang)
        est_duration = estimate_speech_duration_seconds(script)

        assert est_duration <= DISRUPTIONS_MAX_SECONDS, (
            f"Disruptions script exceeded 45s limit: {est_duration}s ({script})"
        )
        assert "[" in script and "]" in script


def test_generate_voice_script_with_mocked_gemma(sample_strike_event):
    mock_ollama_client = MagicMock()
    mock_ollama_client.chat.return_value = {
        "message": {
            "content": (
                "<thought>I will write an interactive update</thought>\n"
                "[announcer] Let op! [urgent] Er is een landelijke staking. "
                "[sigh] Treinen rijden niet tussen Utrecht en Amsterdam. [calm] Reis met ander vervoer!"
            )
        }
    }

    with patch("ollama.Client", return_value=mock_ollama_client):
        script = generate_voice_script([sample_strike_event], language="nl")
        assert "<thought>" not in script
        assert "[announcer]" in script
        assert "[urgent]" in script
        assert "[sigh]" in script
        assert estimate_speech_duration_seconds(script) <= DISRUPTIONS_MAX_SECONDS


def test_generate_voice_script_gemma_fallback_on_error(sample_work_event):
    with patch("ollama.Client.chat", side_effect=Exception("Connection refused")):
        script = generate_voice_script([sample_work_event], language="en")
        assert estimate_speech_duration_seconds(script) <= DISRUPTIONS_MAX_SECONDS
        assert "[" in script and "]" in script


def test_elevenlabs_service_unconfigured():
    service = ElevenLabsVoiceService(api_key="")
    with pytest.raises(ValueError, match="ELEVEN_API_KEY"):
        service.synthesize_speech("Test script")


def test_elevenlabs_service_synthesize_success():
    service = ElevenLabsVoiceService(api_key="valid-test-key")
    fake_audio = b"FAKE_OGG_OPUS_AUDIO"

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = fake_audio

    with patch("httpx.Client.post", return_value=mock_resp) as mock_post:
        result = service.synthesize_speech("[calm] Testing 11labs audio")
        assert result == fake_audio
        assert mock_post.called
        call_url = mock_post.call_args[0][0]
        call_kwargs = mock_post.call_args[1]
        assert service.voice_id in call_url
        assert call_kwargs["headers"]["xi-api-key"] == "valid-test-key"
        assert call_kwargs["json"]["text"] == "Testing 11labs audio"
        assert call_kwargs["params"] == {"output_format": "opus_48000_64"}


def test_elevenlabs_service_fallback_format():
    service = ElevenLabsVoiceService(api_key="valid-test-key")
    fake_audio = b"FAKE_MP3_AUDIO"

    resp_fail = MagicMock()
    resp_fail.status_code = 400
    resp_fail.text = "Invalid output_format requested"

    resp_ok = MagicMock()
    resp_ok.status_code = 200
    resp_ok.content = fake_audio

    with patch("httpx.Client.post", side_effect=[resp_fail, resp_ok]):
        result = service.synthesize_speech("[calm] Testing fallback")
        assert result == fake_audio


def test_model_supports_audio_tags():
    assert model_supports_audio_tags("eleven_v3")
    assert model_supports_audio_tags("eleven_v4_turbo")
    assert not model_supports_audio_tags("eleven_multilingual_v2")


def test_prepare_script_for_model_removes_tags_for_multilingual_v2():
    script = "[cheerfully] Hello travelers! [sighs] Delays expected."
    result = prepare_script_for_model(script, "eleven_multilingual_v2")
    assert result == "Hello travelers! Delays expected."


def test_prepare_script_for_model_keeps_tags_for_eleven_v3():
    script = "[cheerfully] Hello travelers!"
    result = prepare_script_for_model(script, "eleven_v3")
    assert result == script


def test_telegram_send_voice_success():
    notifier = TelegramNotifier(token="12345:dummytoken", chat_id="99999")
    mock_resp = MagicMock()
    mock_resp.status_code = 200

    with patch("httpx.Client.post", return_value=mock_resp) as mock_post:
        success = notifier.send_voice(b"AUDIO_BYTES", caption="🎙️ Test voice")
        assert success is True
        assert mock_post.called
        call_args = mock_post.call_args
        assert "sendVoice" in call_args[0][0]
        assert "voice" in call_args[1]["files"]


def test_telegram_send_voice_fallback_to_send_audio():
    notifier = TelegramNotifier(token="12345:dummytoken", chat_id="99999")
    resp_fail = MagicMock()
    resp_fail.status_code = 400
    resp_fail.text = "Wrong file identifier/HTTP URL specified"

    resp_ok = MagicMock()
    resp_ok.status_code = 200

    with patch("httpx.Client.post", side_effect=[resp_fail, resp_ok]) as mock_post:
        success = notifier.send_voice(b"AUDIO_BYTES", caption="🎙️ Test fallback")
        assert success is True
        assert mock_post.call_count == 2
        second_call_url = mock_post.call_args_list[1][0][0]
        assert "sendAudio" in second_call_url


def test_send_voice_summary_dry_run(sample_strike_event):
    result = send_voice_summary(events=[sample_strike_event], dry_run=True, language="nl")
    assert result is True


def test_summarizer_has_generate_voice_script():
    from src.summarizer import DisruptionSummarizer
    summarizer = DisruptionSummarizer()
    script = summarizer.generate_voice_script([])
    assert estimate_speech_duration_seconds(script) <= CALM_MAX_SECONDS
