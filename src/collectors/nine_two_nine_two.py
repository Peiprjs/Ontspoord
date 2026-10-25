import logging
import re
from datetime import datetime, time
from typing import List, Optional
import httpx
from bs4 import BeautifulSoup

from src.models import DisruptionEvent
from src.time_utils import AMSTERDAM_TZ, is_in_7day_window, parse_dutch_datetime

logger = logging.getLogger(__name__)

STAKING_9292_URL = "https://9292.nl/actueel/staking-ov/"


class NineTwoNineTwoCollector:

    def __init__(self, url: str = STAKING_9292_URL):
        self.url = url

    def fetch_disruptions(self, html: Optional[str] = None) -> List[DisruptionEvent]:
        if html is None:
            html = self._fetch_html()
        if not html:
            return []

        return self.parse_html(html)

    def _fetch_html(self) -> Optional[str]:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "nl-NL,nl;q=0.9,en-US;q=0.8,en;q=0.7",
        }

        try:
            with httpx.Client(timeout=10.0, follow_redirects=True) as client:
                resp = client.get(self.url, headers=headers)
                if resp.status_code == 200:
                    return resp.text
        except Exception as e:
            logger.warning(f"httpx fetch failed for {self.url}: {e}. Trying Playwright...")

        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page()
                page.goto(self.url, wait_until="networkidle", timeout=20000)
                content = page.content()
                browser.close()
                return content
        except Exception as e:
            logger.error(f"Playwright fallback also failed for {self.url}: {e}")
            return None

    def parse_html(self, html: str) -> List[DisruptionEvent]:
        soup = BeautifulSoup(html, "html.parser")
        events: List[DisruptionEvent] = []

        article = soup.find("article") or soup.find("main") or soup
        h1 = article.find("h1")
        h1_text = h1.get_text(strip=True) if h1 else ""

        if not h1_text or "staking" not in h1_text.lower():
            if "staking" not in article.get_text().lower():
                return []

        date_pattern = r"(?:op\s+)?(?:[a-zA-Z]+\s+)?(\d{1,2}\s+[a-zA-Z]+(?:\s+\d{4})?)"
        match = re.search(date_pattern, h1_text, re.IGNORECASE)

        start_time: Optional[datetime] = None
        end_time: Optional[datetime] = None

        if match:
            parsed_date = parse_dutch_datetime(match.group(1))
            if parsed_date:
                start_time = parsed_date.replace(hour=0, minute=0, second=0)
                end_time = parsed_date.replace(hour=23, minute=59, second=59)

        if not start_time:
            intro_p = article.find("p")
            if intro_p:
                match_p = re.search(date_pattern, intro_p.get_text(), re.IGNORECASE)
                if match_p:
                    parsed_date = parse_dutch_datetime(match_p.group(1))
                    if parsed_date:
                        start_time = parsed_date.replace(hour=0, minute=0, second=0)
                        end_time = parsed_date.replace(hour=23, minute=59, second=59)

        if not is_in_7day_window(start_time, end_time):
            return []

        paragraphs = [p.get_text(strip=True) for p in article.find_all("p") if p.get_text(strip=True)]
        impact_lead = paragraphs[0] if paragraphs else "Landelijke stakingsactie in het streekvervoer."
        if len(paragraphs) > 1 and "streekvervoer" in paragraphs[1].lower():
            impact_lead += " " + paragraphs[1]

        operator_text = ""
        operator_heading = article.find(lambda t: t.name in ["h2", "h3"] and any(op in t.get_text().lower() for op in ["arriva", "keolis", "qbuzz", "ebs", "connexxion", "transdev"]))
        if operator_heading:
            next_p = operator_heading.find_next_sibling("p")
            if next_p:
                operator_text = next_p.get_text(strip=True)

        impact_desc = impact_lead
        if operator_text:
            impact_desc += f" {operator_heading.get_text(strip=True)}: {operator_text}"

        advice = (
            "Plan kort voor vertrek in de reisplanner op 9292.nl of in de 9292 app. "
            "Controleer de live locaties van bussen en treinen."
        )

        title = h1_text or "9292: Staking in het streekvervoer"

        events.append(
            DisruptionEvent(
                provider="9292",
                mode="general",
                title=title,
                region_or_route="Landelijk (Streekvervoer)",
                start_time=start_time,
                end_time=end_time,
                impact=impact_desc,
                advice=advice,
                source_url=self.url,
                is_strike=True,
            )
        )

        return events
