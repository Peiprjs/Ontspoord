import logging
from typing import List

from src.models import DisruptionEvent
from src.collectors.ns_api import NSAPICollector
from src.collectors.arriva import ArrivaCollector
from src.collectors.ns_web import NSWebCollector
from src.collectors.nine_two_nine_two import NineTwoNineTwoCollector

logger = logging.getLogger(__name__)


def collect_all_disruptions() -> List[DisruptionEvent]:
    events: List[DisruptionEvent] = []

    try:
        ns_api = NSAPICollector()
        api_events = ns_api.fetch_disruptions()
        logger.info(f"Collected {len(api_events)} events from NS API v3")
        events.extend(api_events)
    except Exception as e:
        logger.error(f"Error collecting from NS API: {e}")

    try:
        arriva = ArrivaCollector()
        arriva_events = arriva.fetch_all()
        logger.info(f"Collected {len(arriva_events)} events from Arriva")
        events.extend(arriva_events)
    except Exception as e:
        logger.error(f"Error collecting from Arriva: {e}")

    try:
        ns_web = NSWebCollector()
        web_events = ns_web.fetch_disruptions()
        logger.info(f"Collected {len(web_events)} events from NS Web Fallback")
        events.extend(web_events)
    except Exception as e:
        logger.error(f"Error collecting from NS Web Fallback: {e}")

    try:
        nine_two = NineTwoNineTwoCollector()
        nine_two_events = nine_two.fetch_disruptions()
        logger.info(f"Collected {len(nine_two_events)} events from 9292")
        events.extend(nine_two_events)
    except Exception as e:
        logger.error(f"Error collecting from 9292: {e}")

    deduped: List[DisruptionEvent] = []
    seen = set()
    for ev in events:
        key = (ev.provider.lower(), ev.title.lower().strip(), ev.region_or_route.lower().strip())
        if key not in seen:
            seen.add(key)
            deduped.append(ev)

    deduped.sort(
        key=lambda x: (
            not x.is_strike,
            x.mode,
            x.start_time.isoformat() if x.start_time else "9999-12-31",
            x.title,
        )
    )

    return deduped
