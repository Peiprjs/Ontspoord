from src.config import Settings


def test_normalized_language_and_regions_label_english(monkeypatch):
    monkeypatch.setenv("LANGUAGE", "English")
    monkeypatch.setenv("TARGET_REGIONS", "Amsterdam, Utrecht")
    settings = Settings()

    assert settings.normalized_language == "en"
    assert settings.regions_label == "Amsterdam & Utrecht"


def test_parsed_target_keywords_include_regions_and_deduplicate(monkeypatch):
    monkeypatch.setenv("TARGET_REGIONS", "Utrecht, Rotterdam")
    monkeypatch.setenv("TARGET_KEYWORDS", "utrecht,staking")
    settings = Settings()

    assert settings.parsed_target_keywords == ["utrecht", "staking", "rotterdam"]


def test_resolved_eleven_values_with_fallbacks(monkeypatch):
    monkeypatch.setenv("ELEVEN_API_KEY", "")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "labs-key")
    monkeypatch.setenv("ELEVEN_VOICE_ID", "")
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "voice-123")
    monkeypatch.setenv("ELEVEN_MODEL_ID", "")
    monkeypatch.setenv("ELEVENLABS_MODEL_ID", "eleven_v3")
    settings = Settings()

    assert settings.resolved_eleven_api_key == "labs-key"
    assert settings.resolved_eleven_voice_id == "voice-123"
    assert settings.resolved_eleven_model_id == "eleven_v3"


def test_parsed_schedule_times_with_invalid_values_falls_back(monkeypatch):
    monkeypatch.setenv("SCHEDULE_TIMES", "07:30,invalid,19:xx")
    settings = Settings()
    assert settings.parsed_schedule_times == [(7, 30)]

    monkeypatch.setenv("SCHEDULE_TIMES", "invalid")
    settings = Settings()
    assert settings.parsed_schedule_times == [(7, 30), (19, 30)]


def test_parsed_target_station_codes_uppercase(monkeypatch):
    monkeypatch.setenv("TARGET_STATION_CODES", " ut , asd ,ut ")
    settings = Settings()
    assert settings.parsed_target_station_codes == {"UT", "ASD"}
