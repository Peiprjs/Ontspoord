import logging
import re
from datetime import datetime
from typing import List, Optional
from bs4 import BeautifulSoup

from src.config import settings
from src.models import DisruptionEvent
from src.time_utils import AMSTERDAM_TZ, is_in_7day_window, parse_dutch_datetime

logger = logging.getLogger(__name__)

STAKING_URL = "https://www.arriva.nl/plan-je-reis/staking/"
STORINGEN_URL = "https://www.arriva.nl/plan-je-reis/storingen-en-werkzaamheden/"


class ArrivaCollector:

    def __init__(
        self,
        chromium_path: str = "/usr/sbin/chromium",
        target_regions: Optional[List[str]] = None,
        keywords: Optional[List[str]] = None,
    ):
        self.chromium_path = chromium_path
        self.target_regions = target_regions if target_regions is not None else settings.parsed_target_regions
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

    def fetch_all(
        self,
        staking_html: Optional[str] = None,
        storingen_html: Optional[str] = None
    ) -> List[DisruptionEvent]:
        events: List[DisruptionEvent] = []

        if staking_html is None:
            staking_html = self._fetch_html_playwright(STAKING_URL)
        if staking_html:
            events.extend(self.parse_staking_html(staking_html))

        if storingen_html is None:
            storingen_html = self._fetch_html_playwright(STORINGEN_URL)
        if storingen_html:
            events.extend(self.parse_storingen_html(storingen_html))

        return events

    def parse_staking_html(self, html: str) -> List[DisruptionEvent]:
        soup = BeautifulSoup(html, "html.parser")
        events: List[DisruptionEvent] = []

        main = soup.find("main") or soup
        section0 = main.find("section")

        timestamp_str = ""
        time_heading = soup.find(lambda t: t.name in ["h3", "h4", "p"] and "bijgewerkt op" in t.get_text().lower())
        if time_heading:
            timestamp_str = time_heading.get_text(strip=True)

        subtitle = ""
        subtitle_tag = soup.select_one("span.subtitle, .subtitle, h4.h3")
        if subtitle_tag:
            subtitle = subtitle_tag.get_text(strip=True)

        strike_date_dt: Optional[datetime] = None
        strike_date_match = re.search(r"staking\s+(\d{1,2}\s+[a-zA-Z]+)", html, re.IGNORECASE)
        if strike_date_match:
            strike_date_dt = parse_dutch_datetime(strike_date_match.group(1))

        current_mode = "general"
        for elem in (section0.find_all(["h3", "h4", "p"]) if section0 else soup.find_all(["h3", "h4", "p"])):
            elem_text = elem.get_text(strip=True)
            if not elem_text:
                continue

            elem_lower = elem_text.lower()
            if elem.name in ["h3", "h4"] and "trein" in elem_lower:
                current_mode = "train"
                continue
            elif elem.name in ["h3", "h4"] and "bus" in elem_lower:
                current_mode = "bus"
                continue

            strong_tag = elem.find("strong")
            strong_text = strong_tag.get_text(strip=True) if strong_tag else ""
            if not self.target_regions:
                matched_region = strong_text or "Landelijk"
            else:
                matched_region = next((r for r in self.target_regions if r.lower() in strong_text.lower()), None)

            if matched_region:
                full_text = elem.get_text(separator=" ", strip=True)
                desc = full_text.replace(strong_text, "").strip() if strong_tag else full_text
                title = f"Arriva {current_mode.capitalize()} {matched_region} Stakingsactie"
                if subtitle:
                    title += f" - {subtitle}"

                events.append(
                    DisruptionEvent(
                        provider="Arriva",
                        mode=current_mode,
                        title=title,
                        region_or_route=f"{matched_region} ({current_mode.capitalize()})",
                        start_time=strike_date_dt,
                        end_time=None,
                        impact=desc or "Houd rekening met rituitval. Zorg voor alternatief vervoer.",
                        advice="Plan kort voor vertrek je reis in de reisplanner.",
                        source_url=STAKING_URL,
                        is_strike=True,
                    )
                )

        if not events and (subtitle or "staking" in html.lower()):
            if any(k in html.lower() for k in ["cnv", "fnv", "cao", "streekvervoer"]):
                regions_label = " & ".join(self.target_regions)
                events.append(
                    DisruptionEvent(
                        provider="Arriva",
                        mode="general",
                        title=f"Landelijke Arriva Staking: {subtitle or 'Stakingsactie aangekondigd'}",
                        region_or_route=f"{regions_label} en Landelijk",
                        start_time=strike_date_dt,
                        end_time=None,
                        impact=subtitle or "Landelijke stakingsacties in het streekvervoer aangekondigd.",
                        advice="Plan kort voor vertrek je reis in de reisplanner.",
                        source_url=STAKING_URL,
                        is_strike=True,
                    )
                )

        return events

    def parse_storingen_html(self, html: str) -> List[DisruptionEvent]:
        soup = BeautifulSoup(html, "html.parser")
        events: List[DisruptionEvent] = []

        items = soup.select("div.js-stremmingenList div[data-component=\"collapsable\"]")
        for item in items:
            event = self._parse_single_collapsable(item)
            if event:
                events.append(event)

        return events

    def _parse_single_collapsable(self, item: BeautifulSoup) -> Optional[DisruptionEvent]:
        svg_use = item.select_one("button svg use")
        href = svg_use.get("href", "") if svg_use else ""
        mode = "train" if "train" in href else "bus"

        title_tag = item.select_one("button h3")
        title = title_tag.get_text(strip=True) if title_tag else "Arriva Storing"

        subtitle_tag = item.select_one("button p.text-black")
        subtitle = subtitle_tag.get_text(strip=True) if subtitle_tag else ""
        route = f"{title} ({subtitle})" if subtitle else title

        full_card_text = item.get_text().lower()
        if self.keywords and not any(bool(re.search(rf"\b{re.escape(k)}\b", full_card_text)) for k in self.keywords):
            return None

        start_time: Optional[datetime] = None
        end_time: Optional[datetime] = None
        date_container = item.select_one("button div.leading-normal.text-mxs")
        if date_container:
            date_text = date_container.get_text(separator=" ", strip=True)
            m = re.search(r"van\s+(.+?)\s+t/m\s+(.+)", date_text, re.IGNORECASE)
            if m:
                start_time = parse_dutch_datetime(m.group(1))
                end_time = parse_dutch_datetime(m.group(2))
            else:
                start_time = parse_dutch_datetime(date_text)

        if not is_in_7day_window(start_time, end_time):
            return None

        oorzaak = ""
        gevolg = ""
        advies = ""

        wrapper = item.select_one("div.js-collapseWrapper")
        if wrapper:
            for h4 in wrapper.find_all("h4"):
                h4_name = h4.get_text(strip=True).lower()
                next_p = h4.find_next_sibling("p")
                val = next_p.get_text(separator=" ", strip=True) if next_p else ""
                if "oorzaak" in h4_name:
                    oorzaak = val
                elif "gevolg" in h4_name:
                    gevolg = val
                elif "advies" in h4_name:
                    advies = val

        impact = gevolg or oorzaak or "Dienstregeling aangepast"
        advice = advies or "Raadpleeg de reisplanner voor de actuele situatie."

        is_strike = any(w in full_card_text for w in ["staking", "vakbond", "cao", "actie"])

        return DisruptionEvent(
            provider="Arriva",
            mode=mode,
            title=title,
            region_or_route=route,
            start_time=start_time,
            end_time=end_time,
            impact=impact,
            advice=advice,
            source_url=STORINGEN_URL,
            is_strike=is_strike,
        )
