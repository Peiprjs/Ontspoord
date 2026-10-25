import logging
import re
from datetime import datetime
from typing import List, Optional
from bs4 import BeautifulSoup

from src.config import settings
from src.models import DisruptionEvent
from src.time_utils import AMSTERDAM_TZ, is_in_7day_window, parse_dutch_datetime

logger = logging.getLogger(__name__)

NS_SITUATIE_URL = "https://www.ns.nl/reisinformatie/actuele-situatie-op-het-spoor/"

NATIONAL_KEYWORDS = [
    "landelijk", "staking", "cao", "vakbond", "personeelsactie", "werkonderbreking"
]


class NSWebCollector:

    def __init__(
        self,
        chromium_path: str = "/usr/sbin/chromium",
        keywords: Optional[List[str]] = None,
    ):
        self.chromium_path = chromium_path
        self.keywords = keywords if keywords is not None else settings.parsed_target_keywords

    def _fetch_html_playwright(self, url: str) -> Optional[str]:
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                try:
                    browser = p.chromium.launch(headless=True, executable_path=self.chromium_path)
                except Exception:
                    browser = p.chromium.launch(headless=True)
                page = browser.new_page()
                page.goto(url, wait_until="networkidle", timeout=30000)
                if "challenge" in page.title().lower():
                    try:
                        page.wait_for_function('() => !document.title.toLowerCase().includes("challenge")', timeout=20000)
                        page.wait_for_load_state("networkidle", timeout=15000)
                    except Exception as e:
                        logger.warning(f"Timeout waiting for challenge resolution on {url}: {e}")
                html = page.content()
                browser.close()
                return html
        except Exception as e:
            logger.error(f"Playwright error fetching {url}: {e}")
            return None

    def fetch_disruptions(self, html: Optional[str] = None) -> List[DisruptionEvent]:
        if html is None:
            html = self._fetch_html_playwright(NS_SITUATIE_URL)
        if not html:
            return []

        return self.parse_html(html)

    def parse_html(self, html: str) -> List[DisruptionEvent]:
        soup = BeautifulSoup(html, "html.parser")
        events: List[DisruptionEvent] = []

        calamities = soup.find_all("ns-web-ui-calamity-item")
        for cal in calamities:
            text = cal.get_text(separator=" ", strip=True)
            if not text:
                continue

            lower_text = text.lower()
            matches_national = any(bool(re.search(rf"\b{re.escape(k)}\b", lower_text)) for k in NATIONAL_KEYWORDS)
            matches_target = not self.keywords or any(bool(re.search(rf"\b{re.escape(k)}\b", lower_text)) for k in self.keywords)
            if not (matches_national or matches_target):
                continue

            update_label = ""
            time_tag = cal.select_one(".nes-text-alpha, span")
            if time_tag:
                update_label = time_tag.get_text(strip=True)

            is_strike = any(w in lower_text for w in ["staking", "cao", "vakbond", "actie"])

            start_time: Optional[datetime] = None
            end_time: Optional[datetime] = None
            date_match = re.search(r"van\s+([a-zA-Z]+\s+\d{1,2}\s+[a-zA-Z]+|\d{1,2}\s+[a-zA-Z]+)\s+t/m\s+([a-zA-Z]+\s+\d{1,2}\s+[a-zA-Z]+|\d{1,2}\s+[a-zA-Z]+)", text, re.IGNORECASE)
            if date_match:
                start_time = parse_dutch_datetime(date_match.group(1))
                end_time = parse_dutch_datetime(date_match.group(2))

            if not is_in_7day_window(start_time, end_time):
                continue

            title = "NS Landelijke Melding / Calamiteit"
            if is_strike:
                title = "NS Stakingswaarschuwing"
            if update_label:
                title += f" ({update_label})"

            events.append(
                DisruptionEvent(
                    provider="NS",
                    mode="train",
                    title=title,
                    region_or_route="Landelijk / Spoornetwerk",
                    start_time=start_time,
                    end_time=end_time,
                    impact=text,
                    advice="Houd rekening met forse hinder. Raadpleeg de NS Reisplanner.",
                    source_url=NS_SITUATIE_URL,
                    is_strike=is_strike,
                )
            )

        return events
