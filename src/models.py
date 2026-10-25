from datetime import datetime
from typing import List, Optional
import re
from pydantic import BaseModel, Field


class DisruptionEvent(BaseModel):
    provider: str = Field(description="Operator/provider name, e.g. NS or Arriva")
    mode: str = Field(description="Transport mode: train, bus, or general")
    title: str = Field(description="Summary title of the disruption or strike")
    region_or_route: str = Field(description="Specific route or station corridor, e.g. Utrecht - Amsterdam, Zwolle - Groningen")
    start_time: Optional[datetime] = Field(default=None, description="Start timestamp (timezone-aware Europe/Amsterdam)")
    end_time: Optional[datetime] = Field(default=None, description="End timestamp (timezone-aware Europe/Amsterdam)")
    impact: str = Field(description="Operational impact, e.g. cancelled trips, delays, no trains")
    advice: str = Field(description="Travel advice, e.g. alternate routes or check reisplanner")
    source_url: str = Field(description="Source URL where the event was retrieved")
    is_strike: bool = Field(default=False, description="Flag indicating if the event relates to a strike or union action")

    def format_for_prompt(self, include_bus_details: bool = False) -> str:
        start_str = self.start_time.strftime("%d-%m-%Y %H:%M") if self.start_time else "Onbekend / Onbepaald"
        end_str = self.end_time.strftime("%d-%m-%Y %H:%M") if self.end_time else "Onbekend / Onbepaald"
        strike_flag = "[STAKING / STRIKE]" if self.is_strike else ""

        route = self.region_or_route
        impact = self.impact
        if not include_bus_details and self.mode == "bus":
            route = re.sub(r",?\s*(?:Lijn\s*\d+|Halte\s+[^,()]+|\([^)]*\))", "", route, flags=re.IGNORECASE).strip()
            if not route:
                route = f"{self.provider} regionaal busvervoer"

        return (
            f"- Provider: {self.provider} {strike_flag}\n"
            f"  Mode: {self.mode}\n"
            f"  Title: {self.title}\n"
            f"  Route/Region: {route}\n"
            f"  Time Window: {start_str} t/m {end_str}\n"
            f"  Impact: {impact}\n"
            f"  Advice: {self.advice}\n"
            f"  Source: {self.source_url}"
        )


class DigestReport(BaseModel):
    strikes_message: Optional[str] = Field(default=None, description="Strike report message, None if no strikes")
    works_message: Optional[str] = Field(default=None, description="Works & disruptions message, None if no works")
    general_status_message: str = Field(description="Executive general network status message, always present")

    def get_messages_to_send(self) -> List[str]:
        msgs: List[str] = []
        if self.strikes_message and self.strikes_message.strip():
            msgs.append(self.strikes_message.strip())
        if self.works_message and self.works_message.strip():
            msgs.append(self.works_message.strip())
        if self.general_status_message and self.general_status_message.strip():
            msgs.append(self.general_status_message.strip())
        return msgs
