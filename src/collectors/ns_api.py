import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional
import httpx

from src.config import settings
from src.models import DisruptionEvent
from src.time_utils import AMSTERDAM_TZ, is_in_7day_window, parse_dutch_datetime

logger = logging.getLogger(__name__)

NATIONAL_IMPACT_KEYWORDS = [
    "landelijk", "staking", "cao", "vakbond", "personeelsactie", "werkonderbreking"
]


class NSAPICollector:

    BASE_URL = "https://gateway.apiportal.ns.nl/reisinformatie-api/api/v3/disruptions"

    def __init__(
        self,
        api_key: Optional[str] = None,
        station_codes: Optional[set[str]] = None,
        keywords: Optional[List[str]] = None,
    ):
        self.api_key = api_key or settings.ns_api_key
        self.station_codes = station_codes if station_codes is not None else settings.parsed_target_station_codes
        self.keywords = keywords if keywords is not None else settings.parsed_target_keywords

    def fetch_disruptions(self) -> List[DisruptionEvent]:
        if not self.api_key or self.api_key == "your_ns_api_key_here":
            logger.warning("No valid NS_API_KEY configured. Skipping NS API call.")
            return []

        headers = {
            "Ocp-Apim-Subscription-Key": self.api_key,
            "Accept": "application/json",
            "User-Agent": "nl-ov-monitor/1.0",
        }

        try:
            with httpx.Client(timeout=15.0) as client:
                response = client.get(self.BASE_URL, headers=headers)
                response.raise_for_status()
                data = response.json()
        except Exception as e:
            logger.error(f"Error requesting NS API v3 disruptions: {e}")
            return []

        return self.parse_payload(data)

    def parse_payload(self, data: Any) -> List[DisruptionEvent]:
        items: List[Dict[str, Any]] = []
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            items = data.get("payload", [])
            if not isinstance(items, list):
                items = [items]

        events: List[DisruptionEvent] = []
        for item in items:
            event = self._parse_single_disruption(item)
            if event:
                events.append(event)
        return events

    def _parse_single_disruption(self, item: Dict[str, Any]) -> Optional[DisruptionEvent]:
        raw_type = str(item.get("type", "")).upper()
        if raw_type not in ["CALAMITY", "DISRUPTION", "MAINTENANCE", "STORING", "WERKZAAMHEID"]:
            pass

        title = item.get("title") or item.get("header") or "NS Verstoring"
        topic = item.get("topic") or ""

        start_time: Optional[datetime] = None
        end_time: Optional[datetime] = None

        if "timespans" in item and isinstance(item["timespans"], list) and item["timespans"]:
            ts0 = item["timespans"][0]
            if "start" in ts0 and ts0["start"]:
                start_time = parse_dutch_datetime(ts0["start"])
            if "end" in ts0 and ts0["end"]:
                end_time = parse_dutch_datetime(ts0["end"])
        else:
            if "start" in item and item["start"]:
                start_time = parse_dutch_datetime(item["start"])
            if "end" in item and item["end"]:
                end_time = parse_dutch_datetime(item["end"])

        if not is_in_7day_window(start_time, end_time):
            return None

        is_relevant, matched_stations = self._is_disruption_relevant(item, title, topic)
        if not is_relevant:
            return None

        route_desc = topic or title
        if matched_stations and self.station_codes:
            route_desc = f"{route_desc} ({', '.join(sorted(matched_stations))})"

        consequence = item.get("consequence", {})
        consequence_desc = consequence.get("description") if isinstance(consequence, dict) else str(consequence)
        impact = consequence_desc or item.get("description") or item.get("message") or title

        advice = item.get("advise") or ""
        alt_transport = item.get("alternativeTransport", {})
        if isinstance(alt_transport, dict) and alt_transport.get("shortDescription"):
            advice = f"{advice} {alt_transport['shortDescription']}".strip()
        if not advice:
            advice = "Raadpleeg de NS Reisplanner voor actueel reisadvies."

        all_text = f"{title} {topic} {impact} {advice}".lower()
        is_strike = any(w in all_text for w in ["staking", "vakbond", "cao", "actie"])

        return DisruptionEvent(
            provider="NS",
            mode="train",
            title=title,
            region_or_route=route_desc,
            start_time=start_time,
            end_time=end_time,
            impact=impact,
            advice=advice,
            source_url="https://www.ns.nl/reisinformatie/actuele-situatie-op-het-spoor/",
            is_strike=is_strike,
        )

    def _is_disruption_relevant(
        self, item: Dict[str, Any], title: str, topic: str
    ) -> tuple[bool, set[str]]:
        search_blob = f"{title} {topic}".lower()
        matched_stations: set[str] = set()
        stations_in_item: set[str] = set()

        section = item.get("section")
        if isinstance(section, dict):
            for st in section.get("stations", []):
                code = st.get("code") or st.get("stationCode")
                if code:
                    stations_in_item.add(code.upper())
                name = st.get("name") or st.get("mediumName")
                if name:
                    search_blob += f" {name.lower()}"

        for pub_sec in item.get("publicationSections", []):
            sec = pub_sec.get("section", {})
            for endpt in [sec.get("from"), sec.get("to")]:
                if isinstance(endpt, dict):
                    code = endpt.get("stationCode") or endpt.get("code")
                    if code:
                        stations_in_item.add(code.upper())
                    name = endpt.get("name")
                    if name:
                        search_blob += f" {name.lower()}"

        if not self.station_codes and not self.keywords:
            return True, stations_in_item

        matched_stations = stations_in_item.intersection(self.station_codes)
        is_target_region = bool(matched_stations) or any(
            bool(re.search(rf"\b{re.escape(k)}\b", search_blob)) for k in self.keywords
        )
        is_national = any(
            bool(re.search(rf"\b{re.escape(k)}\b", search_blob)) for k in NATIONAL_IMPACT_KEYWORDS
        )

        return (is_target_region or is_national), matched_stations
