"""Telegram delivery helpers for HTML digests and optional voice messages."""

import logging
import time
from typing import List, Optional

import httpx
from bs4 import BeautifulSoup

from src.config import settings
from src.models import DigestReport
from src.summarizer import sanitize_telegram_html

logger = logging.getLogger(__name__)

MAX_TELEGRAM_MESSAGE_LENGTH = 4096


def chunk_html_message(text: str, max_length: int = MAX_TELEGRAM_MESSAGE_LENGTH) -> List[str]:
    """Split long HTML text into Telegram-safe chunks while preserving paragraph boundaries."""
    if len(text) <= max_length:
        return [text]

    chunks: List[str] = []
    current_chunk: List[str] = []
    current_len = 0

    paragraphs = text.split("\n\n")

    for paragraph in paragraphs:
        if len(paragraph) + 2 > max_length:
            lines = paragraph.split("\n")
            for line in lines:
                if current_len + len(line) + 1 > max_length:
                    if current_chunk:
                        chunks.append("\n".join(current_chunk))
                        current_chunk = []
                        current_len = 0
                current_chunk.append(line)
                current_len += len(line) + 1
        else:
            if current_len + len(paragraph) + 2 > max_length:
                if current_chunk:
                    chunks.append("\n\n".join(current_chunk))
                    current_chunk = []
                    current_len = 0
            current_chunk.append(paragraph)
            current_len += len(paragraph) + 2

    if current_chunk:
        chunks.append("\n\n".join(current_chunk))

    return chunks


class TelegramNotifier:
    """Send multi-part digest and voice messages to a Telegram chat."""

    def __init__(
        self,
        token: Optional[str] = None,
        chat_id: Optional[str] = None,
        max_retries: int = 4,
        backoff_factor: float = 2.0,
    ):
        self.token = settings.telegram_bot_token if token is None else token
        self.chat_id = settings.telegram_chat_id if chat_id is None else chat_id
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor

    def send_digest(self, html_text: str) -> bool:
        """Send one digest message, splitting it into chunks if needed."""
        if not self.token or self.token.startswith("your_") or not self.chat_id:
            logger.warning("Telegram bot credentials not configured. Digest not sent.")
            return False

        sanitized_html = sanitize_telegram_html(html_text)
        chunks = chunk_html_message(sanitized_html)
        success = True

        for chunk_index, chunk in enumerate(chunks):
            sanitized_chunk = sanitize_telegram_html(chunk)
            chunk_success = self._send_chunk_with_retry(sanitized_chunk)
            if not chunk_success:
                logger.error(f"Failed to send chunk {chunk_index + 1}/{len(chunks)}")
                success = False

        return success

    def send_report(self, report: DigestReport) -> bool:
        """Send each generated report message in order with a short delay between messages."""
        messages = report.get_messages_to_send()
        if not messages:
            logger.info("No messages to send in report.")
            return True

        overall_success = True
        for message_index, message in enumerate(messages, 1):
            logger.info(f"Sending report message {message_index}/{len(messages)} to Telegram...")
            message_sent = self.send_digest(message)
            if not message_sent:
                logger.error(f"Failed to send report message {message_index}/{len(messages)}")
                overall_success = False
            if message_index < len(messages):
                time.sleep(0.5)

        return overall_success

    def send_voice(
        self,
        audio_bytes: bytes,
        caption: Optional[str] = None,
        filename: str = "network_summary.ogg",
        mime_type: str = "audio/ogg",
    ) -> bool:
        """Send a voice message and fall back to sendAudio when needed."""
        if not self.token or self.token.startswith("your_") or not self.chat_id:
            logger.warning("Telegram bot credentials not configured. Voice message not sent.")
            return False

        url = f"https://api.telegram.org/bot{self.token}/sendVoice"
        data = {
            "chat_id": self.chat_id,
        }
        if caption:
            data["caption"] = caption
            data["parse_mode"] = "HTML"

        delay = 1.0
        for attempt in range(1, self.max_retries + 1):
            try:
                files = {"voice": (filename, audio_bytes, mime_type)}
                with httpx.Client(timeout=30.0) as client:
                    resp = client.post(url, data=data, files=files)
                    if resp.status_code == 200:
                        logger.info("Telegram voice message delivered successfully.")
                        return True

                    if resp.status_code == 400:
                        logger.warning(
                            f"Telegram sendVoice returned 400 ({resp.text}). Retrying with sendAudio fallback..."
                        )
                        audio_url = f"https://api.telegram.org/bot{self.token}/sendAudio"
                        audio_files = {"audio": (filename, audio_bytes, mime_type)}
                        audio_resp = client.post(audio_url, data=data, files=audio_files)
                        if audio_resp.status_code == 200:
                            logger.info("Telegram audio message delivered successfully via sendAudio.")
                            return True
                        logger.warning(f"sendAudio fallback also failed: {audio_resp.status_code} - {audio_resp.text}")

                    if resp.status_code == 429:
                        retry_after = resp.json().get("parameters", {}).get("retry_after", delay)
                        logger.warning(f"Telegram rate limited (429). Retrying after {retry_after}s...")
                        time.sleep(float(retry_after))
                    else:
                        logger.warning(
                            f"Telegram voice error (attempt {attempt}/{self.max_retries}): "
                            f"{resp.status_code} - {resp.text}"
                        )
                        time.sleep(delay)

            except Exception as error:
                logger.warning(
                    f"Network error sending Telegram voice message (attempt {attempt}/{self.max_retries}): {error}"
                )
                time.sleep(delay)

            delay *= self.backoff_factor

        return False

    def _send_chunk_with_retry(self, text: str) -> bool:
        """Send one chunk with retry/backoff and HTML parse fallback to plain text."""
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }

        delay = 1.0
        for attempt in range(1, self.max_retries + 1):
            try:
                with httpx.Client(timeout=10.0) as client:
                    resp = client.post(url, json=payload)

                    if resp.status_code == 200:
                        return True

                    if resp.status_code == 400 and "can't parse entities" in resp.text:
                        logger.warning("Telegram HTML parse error on payload. Retrying with plain text fallback...")
                        plain_text = BeautifulSoup(text, "html.parser").get_text()
                        plain_payload = {
                            "chat_id": self.chat_id,
                            "text": plain_text,
                            "disable_web_page_preview": True,
                        }
                        plain_resp = client.post(url, json=plain_payload)
                        if plain_resp.status_code == 200:
                            return True
                        logger.warning(f"Plain text fallback error: {plain_resp.status_code} - {plain_resp.text}")

                    if resp.status_code == 429:
                        retry_after = resp.json().get("parameters", {}).get("retry_after", delay)
                        logger.warning(f"Telegram rate limited (429). Retrying after {retry_after}s...")
                        time.sleep(float(retry_after))
                    else:
                        logger.warning(
                            f"Telegram error (attempt {attempt}/{self.max_retries}): "
                            f"{resp.status_code} - {resp.text}"
                        )
                        time.sleep(delay)

            except Exception as error:
                logger.warning(
                    f"Network error sending Telegram message (attempt {attempt}/{self.max_retries}): {error}"
                )
                time.sleep(delay)

            delay *= self.backoff_factor

        return False
