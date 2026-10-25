import os
from typing import List, Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables and .env."""
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    ns_api_key: Optional[str] = Field(default=None, alias="NS_API_KEY")
    telegram_bot_token: Optional[str] = Field(default=None, alias="TELEGRAM_BOT_TOKEN")
    telegram_chat_id: Optional[str] = Field(default=None, alias="TELEGRAM_CHAT_ID")
    eleven_api_key: Optional[str] = Field(default=None, alias="ELEVEN_API_KEY")
    elevenlabs_api_key: Optional[str] = Field(default=None, alias="ELEVENLABS_API_KEY")
    elevenlabs_voice_id: Optional[str] = Field(default=None, alias="ELEVENLABS_VOICE_ID")
    eleven_voice_id: Optional[str] = Field(default=None, alias="ELEVEN_VOICE_ID")
    elevenlabs_model_id: Optional[str] = Field(default=None, alias="ELEVENLABS_MODEL_ID")
    eleven_model_id: Optional[str] = Field(default=None, alias="ELEVEN_MODEL_ID")
    voice_enabled: bool = Field(default=False, alias="VOICE_ENABLED")
    ollama_host: str = Field(default="http://localhost:11434", alias="OLLAMA_HOST")
    ollama_model: str = Field(default="gemma4:e4b", alias="OLLAMA_MODEL")
    schedule_times: str = Field(default="07:30,19:30", alias="SCHEDULE_TIMES")
    timezone: str = Field(default="Europe/Amsterdam", alias="TIMEZONE")

    language: str = Field(default="nl", alias="LANGUAGE")

    @property
    def normalized_language(self) -> str:
        """Return the supported language code used by prompt generation."""
        lang = self.language.strip().lower()
        if lang in {"en", "english", "eng"}:
            return "en"
        return "nl"

    @property
    def resolved_eleven_api_key(self) -> Optional[str]:
        return self.eleven_api_key or self.elevenlabs_api_key

    @property
    def resolved_eleven_voice_id(self) -> str:
        return self.eleven_voice_id or self.elevenlabs_voice_id or "EXAVITQu4vr4xnSDxMaL"

    @property
    def resolved_eleven_model_id(self) -> str:
        return self.eleven_model_id or self.elevenlabs_model_id or "eleven_multilingual_v2"

    target_regions: str = Field(default="Nederland", alias="TARGET_REGIONS")
    target_station_codes: str = Field(default="", alias="TARGET_STATION_CODES")
    target_keywords: str = Field(default="", alias="TARGET_KEYWORDS")

    @property
    def is_nationwide(self) -> bool:
        """Check whether monitoring is configured for nationwide coverage."""
        normalized_region_setting = self.target_regions.strip().lower()
        return normalized_region_setting in {"", "nederland", "landelijk", "all", "*"}

    @property
    def parsed_target_regions(self) -> List[str]:
        """Parse comma-separated regions into a normalized list."""
        if self.is_nationwide:
            return []
        regions = [r.strip() for r in self.target_regions.split(",") if r.strip()]
        return regions

    @property
    def regions_label(self) -> str:
        if not self.parsed_target_regions:
            return "The Netherlands" if self.normalized_language == "en" else "Nederland"
        return " & ".join(self.parsed_target_regions)

    @property
    def parsed_target_station_codes(self) -> set[str]:
        """Parse and uppercase comma-separated NS station abbreviations."""
        codes = {c.strip().upper() for c in self.target_station_codes.split(",") if c.strip()}
        return codes

    @property
    def parsed_target_keywords(self) -> List[str]:
        """Parse keyword filters and include selected target regions as keywords."""
        keywords = [k.strip().lower() for k in self.target_keywords.split(",") if k.strip()]
        for r in self.parsed_target_regions:
            r_lower = r.lower()
            if r_lower not in keywords:
                keywords.append(r_lower)
        return keywords

    @property
    def parsed_schedule_times(self) -> List[tuple[int, int]]:
        """Parse schedule times from HH:MM entries, with sane defaults on invalid input."""
        results = []
        for item in self.schedule_times.split(","):
            item = item.strip()
            if not item:
                continue
            parts = item.split(":")
            if len(parts) == 2:
                try:
                    results.append((int(parts[0]), int(parts[1])))
                except ValueError:
                    pass
        return results or [(7, 30), (19, 30)]


settings = Settings()
