import logging
import re
from typing import List, Optional
from bs4 import BeautifulSoup
import ollama

from src.config import settings
from src.models import DigestReport, DisruptionEvent
from src.time_utils import get_7day_rolling_window

logger = logging.getLogger(__name__)

ALLOWED_TELEGRAM_TAGS = {
    "b", "strong", "i", "em", "u", "ins", "s", "strike", "del",
    "span", "tg-spoiler", "a", "code", "pre", "blockquote"
}


def sanitize_telegram_html(text: str) -> str:
    text = re.sub(r"<\|channel\|>thought.*?<\|channel\|>", "", text, flags=re.DOTALL)
    text = re.sub(r"<\|channel\>thought.*?(<\|channel\>|$)", "", text, flags=re.DOTALL)
    text = re.sub(r"<\|thought\|>.*?<\|/thought\|>", "", text, flags=re.DOTALL)
    text = re.sub(r"<thought>.*?</thought>", "", text, flags=re.DOTALL)
    text = re.sub(r"<\|channel\|>[a-zA-Z0-9_]*", "", text)

    fence_match = re.search(r"^```(?:html)?\s*(.*?)\s*```$", text.strip(), flags=re.DOTALL | re.IGNORECASE)
    if fence_match:
        text = fence_match.group(1)

    text = re.sub(r"<h[1-6][^>]*>(.*?)</h[1-6]>", r"\n\n<b>\1</b>\n", text, flags=re.IGNORECASE | re.DOTALL)

    text = re.sub(r"<li[^>]*>(.*?)</li>", r"• \1\n", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"</?[uo]l[^>]*>", "\n", text, flags=re.IGNORECASE)

    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<hr\s*/?>", "\n———\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p>", "\n\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<p[^>]*>", "", text, flags=re.IGNORECASE)

    soup = BeautifulSoup(text, "html.parser")
    for tag in soup.find_all(True):
        tag_name = tag.name.lower()
        if tag_name in ALLOWED_TELEGRAM_TAGS:
            if tag_name == "a":
                href = tag.get("href")
                tag.attrs = {"href": href} if href else {}
            elif tag_name == "span":
                cls = tag.get("class", [])
                if cls == ["tg-spoiler"] or "tg-spoiler" in cls:
                    tag.attrs = {"class": "tg-spoiler"}
                else:
                    tag.unwrap()
            else:
                tag.attrs = {}
        else:
            tag.unwrap()

    cleaned = str(soup)
    cleaned = re.sub(r"<b>\s*(?:Reisadvies|Travel\s*Advice)\s*:?\s*</b>.*?(?=(?:\n\s*<b>|$))", "", cleaned, flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()

SYSTEM_PROMPT = """Je bent de Nederlandse Openbaar Vervoer Monitor assistent. Jouw taak is om een beknopt, overzichtelijk Telegram-bericht te genereren in zuivere HTML-opmaak (gebruik UITSLUITEND <b>, <i> en <code> tags, GEEN Markdown zoals **, *, # of backticks).

Richtlijnen voor de structuur:
1. Statusbanner bovenaan:
   - 🔴 STAKING / GROTE VERSTORING (indien staking of grote landelijke/regionale stremming)
   - 🟡 BEPERKTE HINDER / WERKZAAMHEDEN (indien regulier onderhoud of lokale vertragingen)
   - 🟢 REGULIERE DIENSTREGELING (indien geen verstoringen of stakingen)
   Inclusief vermelding van de 7-daagse peilperiode.

2. Stakingsradar (komende 7 dagen):
   - Vermeld expliciete data, getroffen vervoerders (NS, Arriva, Keolis, Qbuzz, Connexxion, etc.), vakbonden (FNV, CNV) en CAO-achtergrond indien van toepassing.
   - Indien er GEEN stakingen zijn aangekondigd: vermeld kort dat er geen stakingsacties gepland staan.

3. Trajectoverzicht:
   - Groepeer per vervoerswijze:
     * <b>Trein</b> (NS Intercity & Sprinter, Arriva, Keolis, Qbuzz)
     * <b>Bus</b> (stads- en streekbussen per regio)
   - Vermeld per verstoring: traject, geldige data/tijden, oorzaak en gevolg.

4. Indien er GEEN stakingen EN GEEN verstoringen zijn:
   - Geef een clean 2-regelig bericht:
     🟢 <b>Alles rijdt volgens dienstregeling</b>
     <i>Er zijn voor de komende 7 dagen geen stakingen of grote werkzaamheden gemeld.</i>

Houd de toon professioneel, feitelijk en direct scanbaar voor forenzen. Produceer GEEN interne denkprocessen, GEEN uitleg buiten het bericht, en ALLEEN valide HTML."""


class DisruptionSummarizer:

    def __init__(
        self,
        host: Optional[str] = None,
        model: Optional[str] = None,
        target_regions: Optional[List[str]] = None,
        language: Optional[str] = None,
    ):
        self.host = host or settings.ollama_host
        self.model = model or settings.ollama_model
        self.target_regions = target_regions if target_regions is not None else settings.parsed_target_regions
        raw_lang = language or settings.normalized_language
        self.language = "en" if str(raw_lang).strip().lower() in {"en", "english", "eng"} else "nl"
        self.client = ollama.Client(host=self.host)

    @property
    def is_english(self) -> bool:
        return self.language == "en"

    @property
    def regions_label(self) -> str:
        if self.target_regions:
            return " & ".join(self.target_regions)
        return "The Netherlands" if self.is_english else "Nederland"

    @staticmethod
    def strip_thought_blocks(text: str) -> str:
        return sanitize_telegram_html(text)

    def _call_ollama(self, system_prompt: str, user_prompt: str) -> str:
        response = self.client.chat(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            options={
                "temperature": 0.6,
                "top_p": 0.95,
                "top_k": 64,
                "num_ctx": 16384,
                "num_predict": 4096,
            },
        )
        if response.get("done_reason") == "length":
            logger.warning("Ollama response reached generation length limit (done_reason=length).")
        raw = response["message"]["content"]
        return self.strip_thought_blocks(raw)

    def _format_period_string(self) -> str:
        w_start, w_end = get_7day_rolling_window()
        sep = " to " if self.is_english else " t/m "
        return f"{w_start.strftime('%d-%m-%Y')}{sep}{w_end.strftime('%d-%m-%Y')}"

    def _summarize_buses_for_prompt(self, buses: List[DisruptionEvent]) -> str:
        city_counts: dict[str, int] = {}
        for b in buses:
            clean_route = re.sub(r",?\s*(?:Lijn\s*\d+|Halte\s+[^,()]+|\([^)]*\))", "", b.region_or_route, flags=re.IGNORECASE).strip()
            city = clean_route.split(",")[0].split("-")[0].strip() or ("Regional" if self.is_english else "Regionaal")
            city_counts[city] = city_counts.get(city, 0) + 1

        parts = []
        for city, count in sorted(city_counts.items(), key=lambda x: -x[1])[:8]:
            if self.is_english:
                parts.append(f"• Region {city}: {count} active road detours / cancelled stops")
            else:
                parts.append(f"• Regio {city}: {count} actieve wegomleidingen / vervallen haltes")

        if parts:
            return "\n".join(parts)
        return "Regular regional bus service with local detours." if self.is_english else "Regulier regionaal busvervoer met lokale omleidingen."

    def generate_report(self, events: List[DisruptionEvent]) -> DigestReport:
        period_str = self._format_period_string()
        strikes = [e for e in events if e.is_strike]
        works = [e for e in events if not e.is_strike]

        try:
            strikes_msg: Optional[str] = None
            if strikes:
                strikes_msg = self._generate_strikes_message(strikes, period_str)

            works_msg: Optional[str] = None
            if works:
                works_msg = self._generate_works_message(works, period_str)

            status_msg = self._generate_status_message(events, period_str, has_strikes=bool(strikes), has_works=bool(works))

            return DigestReport(
                strikes_message=strikes_msg,
                works_message=works_msg,
                general_status_message=status_msg,
            )
        except Exception as e:
            logger.error(f"Error generating multi-message report via Ollama: {e}")
            return self._fallback_report(events, period_str)

    def _generate_strikes_message(self, strikes: List[DisruptionEvent], period_str: str) -> str:
        reg = self.regions_label
        strike_texts = "\n\n".join(s.format_for_prompt(include_bus_details=False) for s in strikes)

        if self.is_english:
            system_prompt = (
                f"You are the {reg} Public Transport Monitor assistant. Generate Message 1: STRIKES.\n"
                f"Write the entire message in English.\n"
                f"Use ONLY valid Telegram HTML (<b>, <i>, <code>). NO Markdown.\n\n"
                f"Follow EXACTLY this uniform structure:\n"
                f"<b>🔴 PT MONITOR {reg.upper()}: STRIKES</b>\n"
                f"<i>Monitoring period: {period_str}</i>\n\n"
                f"<b>Reason & Background:</b>\n"
                f"[Explain the cause: e.g. collective labor agreement (CAO) negotiations, strike action called by trade unions CNV/FNV]\n\n"
                f"<b>Period & Timing:</b>\n"
                f"[Explicitly state the date(s) and times of the strike]\n\n"
                f"<b>Affected Lines & Operators:</b>\n"
                f"• <b>Train:</b> [Explicitly name specific train series, e.g. NS Intercity, Sprinter, Arriva, Keolis, Qbuzz and expected impact]\n"
                f"• <b>Bus:</b> [Describe general impact on regional city and regional bus networks WITHOUT bus line numbers or bus stop names]\n\n"
                f"IMPORTANT DETAIL RULES:\n"
                f"- DO show specific train series and line numbers (such as NS Intercity, Sprinter, regional rail lines).\n"
                f"- Do NOT show bus line numbers (NEVER write 'Line 350', 'Line 4' etc.) and NEVER write specific bus stop names.\n"
                f"- Do NOT add travel advice (no Travel Advice section).\n"
                f"- Keep the message concise, professional, and easily scannable."
            )
            user_prompt = (
                f"Here is the strike information for {reg}:\n\n"
                f"{strike_texts}\n\n"
                f"Now generate the uniform Telegram HTML message for the strikes in English conforming to the formatting rules."
            )
        else:
            system_prompt = (
                f"Je bent de {reg} Openbaar Vervoer Monitor assistent. Genereer Bericht 1: STAKINGEN.\n"
                f"Gebruik UITSLUITEND geldige Telegram HTML (<b>, <i>, <code>). GEEN Markdown.\n\n"
                f"Volg EXACT deze uniforme structuur:\n"
                f"<b>🔴 OV MONITOR {reg.upper()}: STAKINGEN</b>\n"
                f"<i>Peilperiode: {period_str}</i>\n\n"
                f"<b>Aanleiding & Achtergrond:</b>\n"
                f"[Leg de reden uit: bijv. CAO-onderhandelingen, stakingsoproep door FNV/CNV]\n\n"
                f"<b>Periode & Tijdstip:</b>\n"
                f"[Vermeld expliciet de datum(s) en tijdstippen van de staking]\n\n"
                f"<b>Getroffen Lijnen & Vervoerders:</b>\n"
                f"• <b>Trein:</b> [Noem expliciet de specifieke treinseries, bijv. NS Intercity, Sprinter, Arriva, Keolis, Qbuzz en het verwachte gevolg]\n"
                f"• <b>Bus:</b> [Beschrijf de algemene impact op het regionale stads- en streekbusnetwerk ZONDER buslijnnummers of haltes]\n\n"
                f"BELANGRIJKE DETAIL-REGELS:\n"
                f"- Toon WEL specifieke treinnummers en treinseries (zoals NS Intercity, Sprinter, regionale spoorlijnen).\n"
                f"- Toon GEEN buslijnnummers (schrijf NOOIT 'Lijn 350', 'Lijn 4' etc.) en NOOIT specifieke bushaltenamen.\n"
                f"- Voeg GEEN reisadvies toe (geen Reisadvies sectie).\n"
                f"- Houd het bericht beknopt, professioneel en direct scanbaar."
            )
            user_prompt = (
                f"Hier zijn de geregistreerde stakingsgegevens voor {reg}:\n\n"
                f"{strike_texts}\n\n"
                f"Genereer nu het uniforme Telegram HTML-bericht voor de stakingen conform de opmaakregels."
            )

        return self._call_ollama(system_prompt, user_prompt)

    def _generate_works_message(self, works: List[DisruptionEvent], period_str: str) -> str:
        reg = self.regions_label
        trains = [w for w in works if w.mode == "train"]
        buses = [w for w in works if w.mode == "bus"]

        empty_trains_msg = "No major rail disruptions reported." if self.is_english else "Geen grote treinstoringen gemeld."
        train_texts = "\n\n".join(t.format_for_prompt() for t in trains) if trains else empty_trains_msg
        bus_summary = self._summarize_buses_for_prompt(buses)

        if self.is_english:
            system_prompt = (
                f"You are the {reg} Public Transport Monitor assistant. Generate Message 2: ENGINEERING WORKS & DISRUPTIONS.\n"
                f"Write the entire message in English.\n"
                f"Use ONLY valid Telegram HTML (<b>, <i>, <code>). NO Markdown.\n\n"
                f"Follow EXACTLY this uniform structure:\n"
                f"<b>🟡 PT MONITOR {reg.upper()}: ENGINEERING WORKS & DISRUPTIONS</b>\n"
                f"<i>Monitoring period: {period_str}</i>\n\n"
                f"<b>Reason & Works:</b>\n"
                f"[Explain reasons: e.g. track renewal, overhead wire maintenance, signaling faults, infrastructure maintenance]\n\n"
                f"<b>Period & Timing:</b>\n"
                f"[State effective periods and dates within the next 7 days]\n\n"
                f"<b>Affected Lines & Operators:</b>\n"
                f"• <b>Train:</b> [Specify train series such as NS Intercity, Sprinter, regional trains (Arriva, Keolis, Qbuzz), corridors, and replacement buses if applicable]\n"
                f"• <b>Bus:</b> [General description of regional detours and works by region/city WITHOUT bus line numbers or bus stops]\n\n"
                f"IMPORTANT DETAIL RULES:\n"
                f"- DO show specific train series and line numbers (such as NS Intercity, Sprinter, regional rail lines).\n"
                f"- Do NOT show bus line numbers (NEVER write 'Line 350', 'Line 4' etc.) and NEVER write specific bus stop names.\n"
                f"- Do NOT add travel advice (no Travel Advice section).\n"
                f"- Keep the message concise, professional, and easily scannable."
            )
            user_prompt = (
                f"Here are the active works and disruptions for {reg}:\n\n"
                f"TRAINS:\n{train_texts}\n\n"
                f"BUS OVERVIEW:\n{bus_summary}\n\n"
                f"Now generate the uniform Telegram HTML message for the works and disruptions in English conforming to the formatting rules."
            )
        else:
            system_prompt = (
                f"Je bent de {reg} Openbaar Vervoer Monitor assistent. Genereer Bericht 2: WERKZAAMHEDEN & STORINGEN.\n"
                f"Gebruik UITSLUITEND geldige Telegram HTML (<b>, <i>, <code>). GEEN Markdown.\n\n"
                f"Volg EXACT deze uniforme structuur:\n"
                f"<b>🟡 OV MONITOR {reg.upper()}: WERKZAAMHEDEN & STORINGEN</b>\n"
                f"<i>Peilperiode: {period_str}</i>\n\n"
                f"<b>Aanleiding & Werkzaamheden:</b>\n"
                f"[Leg de redenen uit: bijv. spoorvernieuwing, bovenleidingsonderhoud, seinstoringen, infrastructurele werkzaamheden]\n\n"
                f"<b>Periode & Tijdstip:</b>\n"
                f"[Vermeld de geldende periodes en datums binnen de komende 7 dagen]\n\n"
                f"<b>Getroffen Lijnen & Vervoerders:</b>\n"
                f"• <b>Trein:</b> [Vermeld de specifieke treinseries zoals NS Intercity, Sprinter, regionale treinen (Arriva, Keolis, Qbuzz), trajecten en eventueel vervangend busvervoer]\n"
                f"• <b>Bus:</b> [Algemene beschrijving van regionale omleidingen en werkzaamheden per regio/stad ZONDER buslijnnummers of haltes]\n\n"
                f"BELANGRIJKE DETAIL-REGELS:\n"
                f"- Toon WEL specifieke treinnummers en treinseries (zoals NS Intercity, Sprinter, regionale spoorlijnen).\n"
                f"- Toon GEEN buslijnnummers (schrijf NOOIT 'Lijn 350', 'Lijn 4' etc.) en NOOIT specifieke bushaltenamen.\n"
                f"- Voeg GEEN reisadvies toe (geen Reisadvies sectie).\n"
                f"- Houd het bericht beknopt, professioneel en direct scanbaar."
            )
            user_prompt = (
                f"Hier zijn de actuele werkzaamheden en storingen voor {reg}:\n\n"
                f"TREINEN:\n{train_texts}\n\n"
                f"BUSSEN OVERZICHT:\n{bus_summary}\n\n"
                f"Genereer nu het uniforme Telegram HTML-bericht voor de werkzaamheden en storingen conform de opmaakregels."
            )

        return self._call_ollama(system_prompt, user_prompt)

    def _generate_status_message(
        self,
        events: List[DisruptionEvent],
        period_str: str,
        has_strikes: bool,
        has_works: bool,
    ) -> str:
        reg = self.regions_label

        if self.is_english:
            if has_strikes:
                status_icon = "🔴"
                status_level = "DISRUPTED / STRIKE ACTION"
            elif has_works:
                status_icon = "🟡"
                status_level = "MODERATE DELAYS / ENGINEERING WORKS"
            else:
                status_icon = "🟢"
                status_level = "REGULAR SCHEDULE"

            system_prompt = (
                f"You are the {reg} Public Transport Monitor assistant. Generate Message 3: GENERAL LINE STATUS.\n"
                f"Write the entire message in English.\n"
                f"Use ONLY valid Telegram HTML (<b>, <i>, <code>). NO Markdown.\n\n"
                f"Follow EXACTLY this uniform structure:\n"
                f"<b>{status_icon} PT MONITOR {reg.upper()}: GENERAL STATUS</b>\n"
                f"<i>Monitoring period: {period_str}</i>\n\n"
                f"<b>Network Status:</b>\n"
                f"[Executive summary of overall public transport condition in {reg}: {status_level}]\n\n"
                f"<b>Overview by Modality:</b>\n"
                f"• <b>Train Connections:</b> [Status of main corridors with train series like NS Intercity, Sprinter, regional lines]\n"
                f"• <b>Bus Network:</b> [General service status and punctuality of regional and city bus fleets]\n\n"
                f"<b>Key Takeaways:</b>\n"
                f"[1 or 2 key points commuters must take into account this week]\n\n"
                f"IMPORTANT DETAIL RULES:\n"
                f"- DO show specific train series and line numbers (such as NS Intercity, Sprinter, regional rail lines).\n"
                f"- Do NOT show bus line numbers and do NOT show specific bus stop names.\n"
                f"- Do NOT add travel advice (no Travel Advice section).\n"
                f"- Keep the message concise, professional, and easily scannable."
            )
            user_prompt = (
                f"Summary data for {reg}:\n"
                f"- Strike actions active/planned: {'Yes' if has_strikes else 'No'}\n"
                f"- Works/disruptions active: {'Yes' if has_works else 'No'}\n"
                f"- Total recorded events: {len(events)}\n\n"
                f"Now generate the uniform Telegram HTML message for the general network status in English conforming to the formatting rules."
            )
        else:
            if has_strikes:
                status_icon = "🔴"
                status_level = "VERSTOORD / STAKINGSACTIE"
            elif has_works:
                status_icon = "🟡"
                status_level = "BEPERKTE HINDER / WERKZAAMHEDEN"
            else:
                status_icon = "🟢"
                status_level = "REGULIERE DIENSTREGELING"

            system_prompt = (
                f"Je bent de {reg} Openbaar Vervoer Monitor assistent. Genereer Bericht 3: ALGEMENE STATUS VAN DE LIJNEN.\n"
                f"Gebruik UITSLUITEND geldige Telegram HTML (<b>, <i>, <code>). GEEN Markdown.\n\n"
                f"Volg EXACT deze uniforme structuur:\n"
                f"<b>{status_icon} OV MONITOR {reg.upper()}: ALGEMENE STATUS</b>\n"
                f"<i>Peilperiode: {period_str}</i>\n\n"
                f"<b>Netwerkstatus:</b>\n"
                f"[Executive samenvatting van de algehele situatie in het openbaar vervoer in {reg}: {status_level}]\n\n"
                f"<b>Overzicht per modaliteit:</b>\n"
                f"• <b>Treinverbindingen:</b> [Status van de hoofdcorridors met treinseries zoals NS Intercity, Sprinter en regionale spoorlijnen]\n"
                f"• <b>Busnetwerk:</b> [Algemene inzetbaarheid en punctualiteit van het regionale en stadsbusnetwerk]\n\n"
                f"<b>Belangrijkste aandachtspunten:</b>\n"
                f"[1 of 2 belangrijkste punten waar forenzen deze week rekening mee moeten houden]\n\n"
                f"BELANGRIJKE DETAIL-REGELS:\n"
                f"- Toon WEL specifieke treinnummers en treinseries (zoals NS Intercity, Sprinter, regionale spoorlijnen).\n"
                f"- Toon GEEN buslijnnummers en GEEN specifieke bushaltenamen.\n"
                f"- Voeg GEEN reisadvies toe (geen Reisadvies sectie).\n"
                f"- Houd het bericht beknopt, professioneel en direct scanbaar."
            )
            user_prompt = (
                f"Samenvatting gegevens voor {reg}:\n"
                f"- Stakingsacties actief/gepland: {'Ja' if has_strikes else 'Nee'}\n"
                f"- Werkzaamheden/verstoringen actief: {'Ja' if has_works else 'Nee'}\n"
                f"- Totaal geregistreerde gebeurtenissen: {len(events)}\n\n"
                f"Genereer nu het uniforme Telegram HTML-bericht voor de algemene netwerkstatus conform de opmaakregels."
            )

        return self._call_ollama(system_prompt, user_prompt)

    def _fallback_report(self, events: List[DisruptionEvent], period_str: str) -> DigestReport:
        reg = self.regions_label
        strikes = [e for e in events if e.is_strike]
        works = [e for e in events if not e.is_strike]

        if self.is_english:
            strikes_msg: Optional[str] = None
            if strikes:
                s_lines = [
                    f"<b>🔴 PT MONITOR {reg.upper()}: STRIKES</b>",
                    f"<i>Monitoring period: {period_str}</i>\n",
                    "<b>Reason & Background:</b>",
                    "National and regional public transport strike actions announced by trade unions (e.g. FNV and CNV).\n",
                    "<b>Period & Timing:</b>",
                    f"{strikes[0].start_time.strftime('%d-%m-%Y') if strikes[0].start_time else 'Next 7 days'}\n",
                    "<b>Affected Lines & Operators:</b>",
                    "• <b>Train (NS & regional):</b> Risk of cancellations and major delays on rail routes.",
                    "• <b>Bus (city and regional):</b> Service cancellations across affected areas.",
                ]
                strikes_msg = "\n".join(s_lines)

            works_msg: Optional[str] = None
            if works:
                w_lines = [
                    f"<b>🟡 PT MONITOR {reg.upper()}: ENGINEERING WORKS & DISRUPTIONS</b>",
                    f"<i>Monitoring period: {period_str}</i>\n",
                    "<b>Reason & Works:</b>",
                    "Scheduled track and infrastructure maintenance and active signaling faults.\n",
                    "<b>Period & Timing:</b>",
                    f"{period_str}\n",
                    "<b>Affected Lines & Operators:</b>",
                ]
                trains = [w for w in works if w.mode == "train"]
                if trains:
                    for t in trains[:4]:
                        w_lines.append(f"• <b>Train ({t.region_or_route}):</b> {t.impact}")
                w_lines.append("• <b>Bus:</b> Various local detours and modified routes across the region.")
                works_msg = "\n".join(w_lines)

            status_icon = "🔴" if strikes else ("🟡" if works else "🟢")
            status_text = "Disrupted due to strike actions" if strikes else ("Minor disruptions due to engineering works" if works else "All lines running on regular schedule")
            status_lines = [
                f"<b>{status_icon} PT MONITOR {reg.upper()}: GENERAL STATUS</b>",
                f"<i>Monitoring period: {period_str}</i>\n",
                "<b>Network Status:</b>",
                f"{status_text} ({reg}).\n",
                "<b>Overview by Modality:</b>",
                "• <b>Train Connections:</b> Rail corridors operational; expect delays on lines with engineering works.",
                "• <b>Bus Network:</b> Regional and city buses running with local detours.\n",
                "<b>Key Takeaways:</b>",
                "Check live departure times and plan ahead on strike and maintenance days.",
            ]
            status_msg = "\n".join(status_lines)
        else:
            strikes_msg = None
            if strikes:
                s_lines = [
                    f"<b>🔴 OV MONITOR {reg.upper()}: STAKINGEN</b>",
                    f"<i>Peilperiode: {period_str}</i>\n",
                    "<b>Aanleiding & Achtergrond:</b>",
                    "Landelijke en regionale stakingsacties in het openbaar vervoer aangekondigd door vakbonden (o.a. FNV en CNV).\n",
                    "<b>Periode & Tijdstip:</b>",
                    f"{strikes[0].start_time.strftime('%d-%m-%Y') if strikes[0].start_time else 'Komende 7 dagen'}\n",
                    "<b>Getroffen Lijnen & Vervoerders:</b>",
                    "• <b>Trein (NS & regionaal):</b> Kans op forse rituitval en vertragingen op spoortrajecten.",
                    "• <b>Bus (stads- en streekvervoer):</b> Uitval van bussen in de getroffen regio's.",
                ]
                strikes_msg = "\n".join(s_lines)

            works_msg = None
            if works:
                w_lines = [
                    f"<b>🟡 OV MONITOR {reg.upper()}: WERKZAAMHEDEN & STORINGEN</b>",
                    f"<i>Peilperiode: {period_str}</i>\n",
                    "<b>Aanleiding & Werkzaamheden:</b>",
                    "Gepland spoor- en wegenonderhoud en actuele seinstoringen.\n",
                    "<b>Periode & Tijdstip:</b>",
                    f"{period_str}\n",
                    "<b>Getroffen Lijnen & Vervoerders:</b>",
                ]
                trains = [w for w in works if w.mode == "train"]
                if trains:
                    for t in trains[:4]:
                        w_lines.append(f"• <b>Trein ({t.region_or_route}):</b> {t.impact}")
                w_lines.append("• <b>Bus:</b> Diverse lokale omleidingen en aangepaste routes in de regio.")
                works_msg = "\n".join(w_lines)

            status_icon = "🔴" if strikes else ("🟡" if works else "🟢")
            status_text = "Verstoord wegens stakingsacties" if strikes else ("Beperkte hinder door werkzaamheden" if works else "Alle lijnen rijden volgens dienstregeling")
            status_lines = [
                f"<b>{status_icon} OV MONITOR {reg.upper()}: ALGEMENE STATUS</b>",
                f"<i>Peilperiode: {period_str}</i>\n",
                "<b>Netwerkstatus:</b>",
                f"{status_text} ({reg}).\n",
                "<b>Overzicht per modaliteit:</b>",
                "• <b>Treinverbindingen:</b> Spoorcorridors operationeel, houd rekening met eventuele werkzaamheden.",
                "• <b>Busnetwerk:</b> Regionale en stadsbussen rijden met lokale omleidingen.\n",
                "<b>Belangrijkste aandachtspunten:</b>",
                "Let op de aangekondigde actiedagen en controleer actuele vertrektijden.",
            ]
            status_msg = "\n".join(status_lines)

        return DigestReport(
            strikes_message=strikes_msg,
            works_message=works_msg,
            general_status_message=status_msg,
        )

    def generate_digest(self, events: List[DisruptionEvent]) -> str:
        report = self.generate_report(events)
        return "\n\n———\n\n".join(report.get_messages_to_send())
