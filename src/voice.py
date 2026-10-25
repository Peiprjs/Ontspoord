import logging
import re
from typing import List, Optional
import httpx
import ollama

from src.config import settings
from src.models import DisruptionEvent
from src.notifier import TelegramNotifier

logger = logging.getLogger(__name__)

CALM_MAX_SECONDS = 15.0
DISRUPTIONS_MAX_SECONDS = 45.0


def count_spoken_words(script: str) -> int:
    """Counts words intended for spoken delivery, stripping out bracketed 11Labs tags."""
    clean_text = re.sub(r"\[.*?\]", "", script)
    words = clean_text.split()
    return len(words)


def estimate_speech_duration_seconds(script: str, words_per_minute: float = 140.0) -> float:
    """
    Estimates audio duration in seconds based on spoken word count and natural speaking pace.
    Includes a slight buffer for emotional inflections and pauses from 11Labs tags.
    """
    word_count = count_spoken_words(script)
    tag_count = len(re.findall(r"\[.*?\]", script))
    duration = (word_count / words_per_minute) * 60.0 + (tag_count * 0.4)
    return round(duration, 1)


def clean_voice_script(text: str) -> str:
    """
    Cleans raw LLM text for speech synthesis:
    - Strips thought blocks (<thought>...</thought>, <|thought|>...</|thought|>)
    - Strips markdown formatting (**, *, #, `)
    - Strips HTML tags
    - Preserves bracketed 11Labs audio tags ([announcer], [urgent], [sigh], etc.)
    """
    # Strip thought channels/tags
    text = re.sub(r"<\|channel\|>thought.*?<\|channel\|>", "", text, flags=re.DOTALL)
    text = re.sub(r"<\|channel\>thought.*?(<\|channel\>|$)", "", text, flags=re.DOTALL)
    text = re.sub(r"<\|thought\|>.*?<\|/thought\|>", "", text, flags=re.DOTALL)
    text = re.sub(r"<thought>.*?</thought>", "", text, flags=re.DOTALL)
    text = re.sub(r"<\|channel\|>[a-zA-Z0-9_]*", "", text)

    # Strip code fences
    fence_match = re.search(r"^```(?:[a-zA-Z]+)?\s*(.*?)\s*```$", text.strip(), flags=re.DOTALL)
    if fence_match:
        text = fence_match.group(1)

    # Strip HTML tags
    text = re.sub(r"<[^>]+>", " ", text)

    # Strip markdown bold, italics, headers, code
    text = re.sub(r"[*_#`]", "", text)

    # Clean quotes and redundant whitespace
    text = re.sub(r'["\']', '', text)
    text = re.sub(r"\s+", " ", text)

    return text.strip()


def enforce_time_limit(script: str, max_seconds: float) -> str:
    """
    Enforces that the script strictly does not exceed max_seconds estimated speech duration.
    Trims by complete sentences first, or by words if necessary, ensuring proper ending punctuation.
    """
    current_est = estimate_speech_duration_seconds(script)
    if current_est <= max_seconds:
        return script

    logger.warning(
        f"Generated voice script duration ({current_est}s) exceeded strict limit ({max_seconds}s). Enforcing limit..."
    )

    sentences = re.split(r"(?<=[.!?])\s+", script)
    fitted: List[str] = []

    for sentence in sentences:
        candidate = " ".join(fitted + [sentence]).strip()
        if estimate_speech_duration_seconds(candidate) <= max_seconds:
            fitted.append(sentence)
        else:
            break

    if fitted:
        result = " ".join(fitted).strip()
        if not result.endswith((".", "!", "?")):
            result += "!"
        return result

    # If even the first sentence is too long, trim word by word
    words = script.split()
    target_words = []
    for w in words:
        candidate = " ".join(target_words + [w])
        if estimate_speech_duration_seconds(candidate) <= max_seconds:
            target_words.append(w)
        else:
            break

    result = " ".join(target_words).strip()
    if not result.endswith((".", "!", "?")):
        result += "."
    return result


def _fallback_voice_script(
    events: List[DisruptionEvent],
    language: Optional[str] = None,
) -> str:
    """
    Deterministic fallback script that guarantees strict adherence to:
    - 15s limit if calm
    - 45s limit if disruptions
    incorporating interactive 11Labs tags.
    """
    raw_lang = language or settings.normalized_language
    is_english = str(raw_lang).strip().lower() in {"en", "english", "eng"}

    strikes = [e for e in events if e.is_strike]
    works = [e for e in events if not e.is_strike]

    has_strikes = bool(strikes)
    has_works = bool(works)
    region_name = settings.regions_label

    if is_english:
        if has_strikes and has_works:
            script = (
                f"[announcer] Urgent travel bulletin for {region_name}! "
                f"[urgent] Strike actions and major rail works are taking place this week. "
                f"[serious] Expect heavy cancellations across train and regional bus lines. "
                f"[sigh] Please verify your route in the travel app before heading out. "
                f"[calm] Safe travels!"
            )
        elif has_strikes:
            script = (
                f"[announcer] Attention commuters in {region_name}! "
                f"[urgent] Public transport strike actions are scheduled this week. "
                f"[serious] Significant disruptions are expected across rail and bus routes. "
                f"[sigh] Please consult your live travel planner before departure. "
                f"[calm] Safe travels!"
            )
        elif has_works:
            script = (
                f"[announcer] Good day commuters! "
                f"[serious] Major track maintenance is scheduled on several rail corridors this week. "
                f"[sigh] Replacement buses and minor delays are in effect on affected routes. "
                f"[relieved] All other services are running normally. "
                f"[cheerfully] Have a great journey!"
            )
        else:
            # Calm: strictly <= 15 seconds
            script = (
                f"[cheerfully] Great news for commuters in {region_name}! "
                f"[laughs] The entire transport network is running completely smoothly. "
                f"[relieved] No strikes and no major maintenance works. "
                f"[calm] Enjoy your trip!"
            )
    else:
        if has_strikes and has_works:
            script = (
                f"[announcer] Belangrijk reisbericht voor {region_name}! "
                f"[urgent] Er zijn deze week stakingsacties én grote werkzaamheden op het spoor. "
                f"[serious] Reken op flinke uitval bij zowel trein als bus. "
                f"[sigh] Raadpleeg vooraf altijd je reisplanner. "
                f"[calm] Reis voorzichtig!"
            )
        elif has_strikes:
            script = (
                f"[announcer] Let op reizigers in {region_name}! "
                f"[urgent] Er zijn deze week stakingsacties aangekondigd in het openbaar vervoer. "
                f"[serious] Verwacht aanzienlijke uitval bij treinen en bussen. "
                f"[sigh] Controleer voor vertrek altijd de reisplanner. "
                f"[calm] Een veilige reis gewenst!"
            )
        elif has_works:
            script = (
                f"[announcer] Goedendag forenzen! "
                f"[serious] Deze week zijn er spoorwerkzaamheden op diverse trajecten. "
                f"[sigh] Op sommige lijnen rijden bussen, dus reken op extra reistijd. "
                f"[relieved] Het overige netwerk rijdt volgens dienstregeling. "
                f"[cheerfully] Fijne reis!"
            )
        else:
            # Calm: strictly <= 15 seconds
            script = (
                f"[cheerfully] Goed nieuws voor reizigers in {region_name}! "
                f"[laughs] Het openbaar vervoer rijdt soepel en volgens dienstregeling. "
                f"[relieved] Geen stakingen of grote spoorstoringen. "
                f"[calm] Fijne reis!"
            )

    return script.strip()


def generate_voice_script(
    events: List[DisruptionEvent],
    language: Optional[str] = None,
    host: Optional[str] = None,
    model: Optional[str] = None,
) -> str:
    """
    Generates an interactive audio script for ElevenLabs TTS using Gemma via Ollama,
    based on the available disruption information.

    Strict time limits:
    - Calm (no disruptions): <= 15 seconds (target ~20-25 words)
    - Disruptions present: <= 45 seconds (target ~60-75 words)

    Uses 11Labs tags ([announcer], [urgent], [serious], [sigh], [relieved],
    [calm], [cheerfully], [laughs]) for expressive and interactive audio delivery.
    """
    raw_lang = language or settings.normalized_language
    is_english = str(raw_lang).strip().lower() in {"en", "english", "eng"}

    has_disruptions = bool(events)
    max_seconds = DISRUPTIONS_MAX_SECONDS if has_disruptions else CALM_MAX_SECONDS
    region_name = settings.regions_label

    ollama_host = host or settings.ollama_host
    ollama_model = model or settings.ollama_model

    # Format event details for Gemma
    if has_disruptions:
        strikes = [e for e in events if e.is_strike]
        works = [e for e in events if not e.is_strike]

        event_summaries = []
        if strikes:
            event_summaries.append(f"STRIKES ({len(strikes)}):")
            for s in strikes[:3]:
                event_summaries.append(f"- {s.provider} {s.title}: {s.region_or_route} ({s.impact})")
        if works:
            event_summaries.append(f"WORKS/DISRUPTIONS ({len(works)}):")
            for w in works[:4]:
                event_summaries.append(f"- {w.provider} {w.title}: {w.region_or_route} ({w.impact})")
        data_text = "\n".join(event_summaries)
    else:
        data_text = "All lines running on normal schedule. No strikes, no engineering works."

    if is_english:
        if has_disruptions:
            system_prompt = (
                f"You are the transit announcer for {region_name}. Generate an interactive audio voice script for ElevenLabs TTS.\n"
                f"Status: Disruptions active.\n"
                f"CRITICAL REQUIREMENT: The spoken message MUST BE 45 SECONDS OR LESS (MAXIMUM 60 TO 70 SPOKEN WORDS).\n"
                f"Use ElevenLabs audio tags inside square brackets ([announcer], [urgent], [serious], [sigh], [relieved], [calm], [cheerfully]) "
                f"to make the speech lively, emotional, and interactive.\n"
                f"Do NOT output markdown (no bold/headers), HTML, or thoughts. Output ONLY the raw spoken text with inline tags."
            )
            user_prompt = (
                f"Here is the disruption data for {region_name}:\n\n"
                f"{data_text}\n\n"
                f"Create a 45-second or less interactive spoken update with 11Labs tags."
            )
        else:
            system_prompt = (
                f"You are the transit announcer for {region_name}. Generate an interactive audio voice script for ElevenLabs TTS.\n"
                f"Status: Calm / Regular schedule.\n"
                f"CRITICAL REQUIREMENT: The spoken message MUST BE 15 SECONDS OR LESS (MAXIMUM 20 TO 25 SPOKEN WORDS).\n"
                f"Use ElevenLabs audio tags inside square brackets ([cheerfully], [laughs], [relieved], [calm]) to make the speech friendly and lively.\n"
                f"Do NOT output markdown, HTML, or thoughts. Output ONLY the raw spoken text with inline tags."
            )
            user_prompt = (
                f"All transit lines in {region_name} are running smoothly with no strikes or works.\n"
                f"Create a cheerful 15-second or less spoken announcement with 11Labs tags."
            )
    else:
        if has_disruptions:
            system_prompt = (
                f"Je bent de omroeper voor het openbaar vervoer in {region_name}. Genereer een interactief audioscript voor ElevenLabs TTS.\n"
                f"Status: Verstoringen / werkzaamheden / stakingen actief.\n"
                f"CRUCIALE EIS: Het gesproken bericht MOET STRIKT 45 SECONDEN OF KORTER ZIJN (MAXIMAAL 60 TOT 70 WOORDEN).\n"
                f"Gebruik ElevenLabs audio-tags tussen vierkante haken ([announcer], [urgent], [serious], [sigh], [relieved], [calm], [cheerfully]) "
                f"om de omroep interactief, expressief en levendig te maken.\n"
                f"Geef GEEN markdown (geen **, #), GEEN HTML en GEEN denkprocessen. Geef UITSLUITEND de gesproken tekst met tags."
            )
            user_prompt = (
                f"Hier zijn de verstoringen voor {region_name}:\n\n"
                f"{data_text}\n\n"
                f"Maak een interactief gesproken bericht van maximaal 45 seconden met 11Labs-tags."
            )
        else:
            system_prompt = (
                f"Je bent de omroeper voor het openbaar vervoer in {region_name}. Genereer een interactief audioscript voor ElevenLabs TTS.\n"
                f"Status: Rustig / Reguliere dienstregeling.\n"
                f"CRUCIALE EIS: Het gesproken bericht MOET STRIKT 15 SECONDEN OF KORTER ZIJN (MAXIMAAL 20 TOT 25 WOORDEN).\n"
                f"Gebruik ElevenLabs audio-tags tussen vierkante haken ([cheerfully], [laughs], [relieved], [calm]) voor een vrolijke, ontspannen omroep.\n"
                f"Geef GEEN markdown, GEEN HTML en GEEN denkprocessen. Geef UITSLUITEND de gesproken tekst met tags."
            )
            user_prompt = (
                f"Alles in {region_name} rijdt volgens dienstregeling, geen stakingen of werkzaamheden.\n"
                f"Maak een vrolijke gesproken omroep van maximaal 15 seconden met 11Labs-tags."
            )

    try:
        logger.info(f"Generating voice script via Gemma ({ollama_model}) at {ollama_host} (Limit: {max_seconds}s)...")
        client = ollama.Client(host=ollama_host)
        response = client.chat(
            model=ollama_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            options={
                "temperature": 0.5,
                "top_p": 0.9,
                "num_predict": 256,
            },
        )
        raw_content = response["message"]["content"]
        cleaned_script = clean_voice_script(raw_content)

        if cleaned_script:
            # Enforce the strict 15s or 45s limit
            final_script = enforce_time_limit(cleaned_script, max_seconds=max_seconds)
            est_dur = estimate_speech_duration_seconds(final_script)
            logger.info(
                f"Gemma generated voice script successfully ({count_spoken_words(final_script)} words, ~{est_dur}s, limit: {max_seconds}s)"
            )
            return final_script

    except Exception as e:
        logger.warning(f"Could not generate voice script via Gemma ({e}). Using rule-based fallback.")

    # Fallback guarantees the strict limit (15s if calm, 45s if disruptions)
    fallback = _fallback_voice_script(events, language=language)
    return enforce_time_limit(fallback, max_seconds=max_seconds)


class ElevenLabsVoiceService:
    """Service to synthesize interactive speech using the ElevenLabs Text-to-Speech API."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        voice_id: Optional[str] = None,
        model_id: Optional[str] = None,
        timeout: float = 30.0,
    ):
        self.api_key = settings.resolved_eleven_api_key if api_key is None else api_key
        self.voice_id = voice_id or settings.resolved_eleven_voice_id
        self.model_id = model_id or settings.resolved_eleven_model_id
        self.timeout = timeout

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key and not self.api_key.startswith("your_"))

    def synthesize_speech(
        self,
        script: str,
        output_format: str = "opus_48000_64",
    ) -> bytes:
        """
        Synthesizes speech from script containing 11Labs tags.
        Defaults to Ogg Opus format ('opus_48000_64') which is natively supported
        by Telegram voice messages without conversion.
        """
        if not self.is_configured:
            raise ValueError(
                "ElevenLabs API key is not configured. Set ELEVEN_API_KEY in your .env file."
            )

        url = f"https://api.elevenlabs.io/v1/text-to-speech/{self.voice_id}"
        headers = {
            "xi-api-key": self.api_key,
            "Content-Type": "application/json",
        }
        payload = {
            "text": script,
            "model_id": self.model_id,
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.8,
                "style": 0.35,
                "use_speaker_boost": True,
            },
        }

        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(
                    url,
                    headers=headers,
                    json=payload,
                    params={"output_format": output_format},
                )
                if resp.status_code == 200:
                    return resp.content

                if resp.status_code == 400 and "output_format" in resp.text:
                    logger.warning(
                        f"ElevenLabs output_format {output_format} rejected. Falling back to default format..."
                    )
                    fallback_resp = client.post(url, headers=headers, json=payload)
                    if fallback_resp.status_code == 200:
                        return fallback_resp.content
                    raise RuntimeError(
                        f"ElevenLabs TTS failed: {fallback_resp.status_code} - {fallback_resp.text}"
                    )

                raise RuntimeError(
                    f"ElevenLabs TTS request failed: {resp.status_code} - {resp.text}"
                )

        except Exception as e:
            logger.error(f"Error communicating with ElevenLabs API: {e}")
            raise


def send_voice_summary(
    events: Optional[List[DisruptionEvent]] = None,
    language: Optional[str] = None,
    dry_run: bool = False,
    notifier: Optional[TelegramNotifier] = None,
) -> bool:
    """
    Optional function to generate and send an interactive voice summary via Gemma
    and ElevenLabs Text-to-Speech API with 11Labs audio tags.

    Enforces strict limits:
    - 15 seconds or less if calm
    - 45 seconds or less if disruptions

    Parameters:
        events: Optional list of DisruptionEvent objects. If None, collects live data.
        language: 'nl' (Dutch) or 'en' (English). Defaults to LANGUAGE setting.
        dry_run: If True, prints generated script and estimated duration to stdout without calling APIs.
        notifier: Optional TelegramNotifier instance.

    Returns:
        bool: True if voice message was successfully generated and sent (or simulated in dry_run).
    """
    if events is None:
        from src.collectors import collect_all_disruptions

        logger.info("Collecting live disruption events for voice summary...")
        events = collect_all_disruptions()

    has_disruptions = bool(events)
    max_seconds = DISRUPTIONS_MAX_SECONDS if has_disruptions else CALM_MAX_SECONDS

    script = generate_voice_script(events, language=language)
    spoken_words = count_spoken_words(script)
    estimated_seconds = estimate_speech_duration_seconds(script)

    status_desc = "disruptions active" if has_disruptions else "calm / regular schedule"
    logger.info(
        f"Voice script ready ({spoken_words} words, ~{estimated_seconds}s, limit: {max_seconds}s, status: {status_desc}):\n{script}"
    )

    if dry_run:
        print("\n" + "=" * 60)
        print(f"🎙️ DRY RUN: GEMMA + ELEVENLABS VOICE SUMMARY ({max_seconds:.0f}s LIMIT)")
        print("=" * 60)
        print(f"Network condition: {status_desc.upper()}")
        print(f"Strict time limit: <= {max_seconds:.0f} seconds")
        print(f"Spoken word count: {spoken_words} words")
        print(f"Estimated duration: ~{estimated_seconds} seconds")
        print("\nInteractive Voice Script with 11Labs tags (generated via Gemma):")
        print(script)
        print("=" * 60 + "\n")
        return True

    voice_service = ElevenLabsVoiceService()
    if not voice_service.is_configured:
        logger.warning(
            "ELEVEN_API_KEY is not configured in .env. Skipping voice summary generation."
        )
        return False

    try:
        logger.info(f"Synthesizing speech via ElevenLabs ({voice_service.model_id})...")
        audio_bytes = voice_service.synthesize_speech(script, output_format="opus_48000_64")
        logger.info(f"Received {len(audio_bytes)} bytes of audio from ElevenLabs.")

        active_notifier = notifier or TelegramNotifier()
        caption = f"🎙️ <i>Audio network summary (≤{max_seconds:.0f}s)</i>"
        success = active_notifier.send_voice(
            audio_bytes=audio_bytes,
            caption=caption,
            filename="network_summary.ogg",
            mime_type="audio/ogg",
        )
        if success:
            logger.info("ElevenLabs voice summary sent to Telegram successfully.")
        else:
            logger.error("Failed to deliver ElevenLabs voice summary to Telegram.")
        return success

    except Exception as e:
        logger.error(f"Failed to generate or send ElevenLabs voice summary: {e}")
        return False
