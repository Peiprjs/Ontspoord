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
    cleaned = re.sub(r"<b>\s*(?:Key\s*Takeaways?|Belangrijkste\s*aandachtspunten)\s*:?\s*</b>.*?(?=(?:\n\s*<b>|$))", "", cleaned, flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def is_no_message(text: Optional[str]) -> bool:
    """Checks if Gemma decided that a message should not be sent."""
    if not text:
        return True
    plain = BeautifulSoup(text, "html.parser").get_text().strip()
    upper = plain.upper()
    if upper in {
        "NO_MESSAGE", "NO MESSAGE", "GEEN_BERICHT", "GEEN BERICHT",
        "SKIP", "NONE", "NULL", "NO-MESSAGE", "GEEN-BERICHT"
    }:
        return True
    if upper.startswith("NO_MESSAGE") or upper.startswith("GEEN_BERICHT") or upper.startswith("SKIP"):
        return True
    # Short replies (<140 chars) stating that there are no strikes or disruptions
    lower = plain.lower()
    if len(lower) < 140 and any(
        phrase in lower
        for phrase in [
            "geen staking",
            "wordt er niet gestaakt",
            "niet gestaakt",
            "no strike",
            "no active strike",
            "no planned strike",
            "geen stakingsacties",
            "no disruptions",
            "geen werkzaamheden",
        ]
    ):
        return True
    return False


def has_substantive_strikes(strikes: List[DisruptionEvent]) -> bool:
    """Evaluates whether strike events report real active/upcoming strikes vs informational notices saying no strikes."""
    negative_phrases = [
        "niet gestaakt",
        "geen staking",
        "geen stakingsacties",
        "no strike",
        "no active strike",
        "called off",
        "opgeschort",
        "afgelast",
        "won't be strike",
        "will not be strike",
    ]
    for s in strikes:
        full_text = f"{s.title} {s.impact} {s.advice}".lower()
        if not any(neg in full_text for neg in negative_phrases):
            return True
    return False

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
            has_actual_strikes = False
            if strikes:
                candidate = self._generate_strikes_message(strikes, period_str)
                if candidate and not is_no_message(candidate):
                    strikes_msg = candidate
                    has_actual_strikes = True
                else:
                    logger.info("Gemma evaluated strike data and decided NO strikes message is needed.")

            works_msg: Optional[str] = None
            has_actual_works = False
            if works:
                candidate = self._generate_works_message(works, period_str)
                if candidate and not is_no_message(candidate):
                    works_msg = candidate
                    has_actual_works = True
                else:
                    logger.info("Gemma evaluated works data and decided NO works message is needed.")

            status_msg = self._generate_status_message(
                events,
                period_str,
                has_strikes=has_actual_strikes,
                has_works=has_actual_works,
            )

            return DigestReport(
                strikes_message=strikes_msg,
                works_message=works_msg,
                general_status_message=status_msg,
            )
        except Exception as e:
            logger.error(f"Error generating multi-message report via Ollama: {e}")
            return self._fallback_report(events, period_str)

    def generate_voice_script(self, events: List[DisruptionEvent]) -> str:
        """Generates a spoken voice script via Gemma honoring strict 15s (calm) and 45s (disruptions) limits."""
        from src.voice import generate_voice_script as _gen_voice
        return _gen_voice(events, language=self.language, host=self.host, model=self.model)

    def _generate_strikes_message(self, strikes: List[DisruptionEvent], period_str: str) -> str:
        reg = self.regions_label
        strike_texts = "\n\n".join(s.format_for_prompt(include_bus_details=False) for s in strikes)

        if self.is_english:
            system_prompt = (
                f"You are the {reg} Public Transport Monitor assistant. Evaluate strike information and generate Message 1: STRIKES.\n"
                f"Write the entire message in English.\n"
                f"Use ONLY valid Telegram HTML (<b>, <i>, <code>). NO Markdown.\n\n"
                f"CRITICAL DECISION RULE:\n"
                f"Carefully evaluate whether there are actual active, upcoming, or announced strike actions during the monitoring period.\n"
                f"- If the information states that there are NO strikes (e.g. 'no strike actions until November 4', negotiations resumed without strike, or actions have been called off/suspended): you MUST output strictly the single word 'NO_MESSAGE' and nothing else!\n"
                f"- If no actual strikes are scheduled within the next 7 days: output strictly 'NO_MESSAGE'.\n"
                f"- ONLY if real strike actions are taking place or scheduled: generate the message following this uniform structure:\n\n"
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
                f"- Do NOT add Key Takeaways.\n"
                f"- Keep the message concise, professional, and easily scannable."
            )
            user_prompt = (
                f"Here is the strike information for {reg}:\n\n"
                f"{strike_texts}\n\n"
                f"Evaluate if real strikes are happening. If so, generate the Telegram HTML message; otherwise output strictly 'NO_MESSAGE'."
            )
        else:
            system_prompt = (
                f"Je bent de {reg} Openbaar Vervoer Monitor assistent. Beoordeel de stakingsinformatie en genereer Bericht 1: STAKINGEN.\n"
                f"Gebruik UITSLUITEND geldige Telegram HTML (<b>, <i>, <code>). GEEN Markdown.\n\n"
                f"CRUCIALE BESLISSINGSREGEL:\n"
                f"Beoordeel eerst zorgvuldig of er daadwerkelijk sprake is van actieve, geplande of aangekondigde stakingsacties binnen de peilperiode.\n"
                f"- Indien de informatie meldt dat er GEEN stakingen zijn (bijvoorbeeld: 'tot en met 4 november wordt er niet gestaakt', onderhandelingen hervat zonder staking, of acties afgelast/opgeschort): geef dan UITSLUITEND het enkele woord 'NO_MESSAGE' en verder niets!\n"
                f"- Indien er binnen de komende 7 dagen geen daadwerkelijke stakingen plaatsvinden: geef UITSLUITEND 'NO_MESSAGE'.\n"
                f"- ALLEEN wanneer er daadwerkelijk stakingsacties plaatsvinden: genereer het volledige bericht conform deze uniforme structuur:\n\n"
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
                f"- Voeg GEEN belangrijkste aandachtspunten / Key Takeaways toe.\n"
                f"- Houd het bericht beknopt, professioneel en direct scanbaar."
            )
            user_prompt = (
                f"Hier zijn de geregistreerde stakingsgegevens voor {reg}:\n\n"
                f"{strike_texts}\n\n"
                f"Beoordeel of er daadwerkelijk gestaakt wordt. Zo ja, genereer het bericht. Zo nee, antwoord uitsluitend met 'NO_MESSAGE'."
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
                f"You are the {reg} Public Transport Monitor assistant. Evaluate works information and generate Message 2: ENGINEERING WORKS & DISRUPTIONS.\n"
                f"Write the entire message in English.\n"
                f"Use ONLY valid Telegram HTML (<b>, <i>, <code>). NO Markdown.\n\n"
                f"CRITICAL DECISION RULE:\n"
                f"Evaluate whether there are actual substantial active or upcoming works or rail disruptions.\n"
                f"- If there are no substantial disruptions or works (e.g. only normal operations): output strictly 'NO_MESSAGE'.\n"
                f"- Otherwise, follow EXACTLY this uniform structure:\n\n"
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
                f"- Do NOT add Key Takeaways.\n"
                f"- Keep the message concise, professional, and easily scannable."
            )
            user_prompt = (
                f"Here are the active works and disruptions for {reg}:\n\n"
                f"TRAINS:\n{train_texts}\n\n"
                f"BUS OVERVIEW:\n{bus_summary}\n\n"
                f"Evaluate if a disruptions message is needed. If so, generate it; otherwise output strictly 'NO_MESSAGE'."
            )
        else:
            system_prompt = (
                f"Je bent de {reg} Openbaar Vervoer Monitor assistent. Beoordeel werkzaamheden en genereer Bericht 2: WERKZAAMHEDEN & STORINGEN.\n"
                f"Gebruik UITSLUITEND geldige Telegram HTML (<b>, <i>, <code>). GEEN Markdown.\n\n"
                f"CRUCIALE BESLISSINGSREGEL:\n"
                f"Beoordeel of er sprake is van substantiële actieve of geplande werkzaamheden of treinstoringen.\n"
                f"- Indien er geen noemenswaardige verstoringen of werkzaamheden zijn: geef dan UITSLUITEND 'NO_MESSAGE'.\n"
                f"- Zo wel, volg EXACT deze uniforme structuur:\n\n"
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
                f"- Voeg GEEN belangrijkste aandachtspunten / Key Takeaways toe.\n"
                f"- Houd het bericht beknopt, professioneel en direct scanbaar."
            )
            user_prompt = (
                f"Hier zijn de actuele werkzaamheden en storingen voor {reg}:\n\n"
                f"TREINEN:\n{train_texts}\n\n"
                f"BUSSEN OVERZICHT:\n{bus_summary}\n\n"
                f"Beoordeel of een werkzaamhedenbericht nodig is. Zo ja, genereer het; zo nee, antwoord uitsluitend met 'NO_MESSAGE'."
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

        if has_strikes:
            status_level = "DISRUPTED / STRIKE ACTION" if self.is_english else "VERSTOORD / STAKINGSACTIE"
        elif has_works:
            status_level = "MODERATE DELAYS / ENGINEERING WORKS" if self.is_english else "BEPERKTE HINDER / WERKZAAMHEDEN"
        else:
            status_level = "REGULAR SCHEDULE" if self.is_english else "REGULIERE DIENSTREGELING"

        if self.is_english:
            system_prompt = (
                f"You are the {reg} Public Transport Monitor assistant. Generate Message 3: GENERAL LINE STATUS.\n"
                f"Write the entire message in English.\n"
                f"Use ONLY valid Telegram HTML (<b>, <i>, <code>). NO Markdown.\n\n"
                f"CRITICAL STATUS EVALUATION:\n"
                f"Choose the overall status banner based on the real situation:\n"
                f"- 🟢 PT MONITOR {reg.upper()}: GENERAL STATUS (GREEN):\n"
                f"  Set status to GREEN (🟢) if transport is running on regular schedule, OR if the only strike notice states that there will NOT be strike actions (e.g. no strikes until November 4) and no major disruptions exist!\n"
                f"- 🟡 PT MONITOR {reg.upper()}: GENERAL STATUS (YELLOW):\n"
                f"  Set status to YELLOW (🟡) if there is moderate delay, track maintenance, or local works without strike action.\n"
                f"- 🔴 PT MONITOR {reg.upper()}: GENERAL STATUS (RED):\n"
                f"  Set status to RED (🔴) ONLY if there are active, confirmed strike actions or severe network-wide calamities.\n\n"
                f"Follow EXACTLY this uniform structure:\n"
                f"<b>[🟢 or 🟡 or 🔴] PT MONITOR {reg.upper()}: GENERAL STATUS</b>\n"
                f"<i>Monitoring period: {period_str}</i>\n\n"
                f"<b>Network Status:</b>\n"
                f"[Executive summary of overall public transport condition in {reg}: {status_level}]\n\n"
                f"<b>Overview by Modality:</b>\n"
                f"• <b>Train Connections:</b> [Status of main corridors with train series like NS Intercity, Sprinter, regional lines]\n"
                f"• <b>Bus Network:</b> [General service status and punctuality of regional and city bus fleets]\n\n"
                f"CRITICAL RULES:\n"
                f"- Do NOT add a 'Key Takeaways' section! Stop immediately after Overview by Modality.\n"
                f"- Do NOT add travel advice (no Travel Advice section).\n"
                f"- DO show specific train series and line numbers (such as NS Intercity, Sprinter, regional rail lines).\n"
                f"- Do NOT show bus line numbers and do NOT show specific bus stop names.\n"
                f"- Keep the message concise, professional, and easily scannable."
            )
            user_prompt = (
                f"Summary data for {reg}:\n"
                f"- Strike actions active/planned: {'Yes' if has_strikes else 'No (or informational notices state no strikes until later date)'}\n"
                f"- Works/disruptions active: {'Yes' if has_works else 'No'}\n"
                f"- Total recorded events: {len(events)}\n\n"
                f"Now generate the uniform Telegram HTML message for the general network status in English conforming to the formatting rules."
            )
        else:
            system_prompt = (
                f"Je bent de {reg} Openbaar Vervoer Monitor assistent. Genereer Bericht 3: ALGEMENE STATUS VAN DE LIJNEN.\n"
                f"Gebruik UITSLUITEND geldige Telegram HTML (<b>, <i>, <code>). GEEN Markdown.\n\n"
                f"CRUCIALE STATUS BEOORDELING:\n"
                f"Kies de algemene statusbanner op basis van de werkelijke situatie:\n"
                f"- 🟢 OV MONITOR {reg.upper()}: ALGEMENE STATUS (GROEN):\n"
                f"  Zet de status op GROEN (🟢) als alles volgens dienstregeling rijdt, OF als de stakingsinformatie aangeeft dat er NIET gestaakt wordt (bijvoorbeeld: tot en met 4 november wordt er niet gestaakt) en er geen grote storingen zijn!\n"
                f"- 🟡 OV MONITOR {reg.upper()}: ALGEMENE STATUS (GEEL):\n"
                f"  Zet de status op GEEL (🟡) bij beperkte hinder, spoorwerkzaamheden of lokale vertragingen zonder staking.\n"
                f"- 🔴 OV MONITOR {reg.upper()}: ALGEMENE STATUS (ROOD):\n"
                f"  Zet de status op ROOD (🔴) UITSLUITEND als er daadwerkelijk actieve of aangekondigde stakingen of grote calamiteiten zijn.\n\n"
                f"Volg EXACT deze uniforme structuur:\n"
                f"<b>[🟢 of 🟡 of 🔴] OV MONITOR {reg.upper()}: ALGEMENE STATUS</b>\n"
                f"<i>Peilperiode: {period_str}</i>\n\n"
                f"<b>Netwerkstatus:</b>\n"
                f"[Executive samenvatting van de algehele situatie in het openbaar vervoer in {reg}: {status_level}]\n\n"
                f"<b>Overzicht per modaliteit:</b>\n"
                f"• <b>Treinverbindingen:</b> [Status van de hoofdcorridors met treinseries zoals NS Intercity, Sprinter en regionale spoorlijnen]\n"
                f"• <b>Busnetwerk:</b> [Algemene inzetbaarheid en punctualiteit van het regionale en stadsbusnetwerk]\n\n"
                f"CRUCIALE OPMAAKREGELS:\n"
                f"- Voeg GEEN 'Belangrijkste aandachtspunten' of 'Key Takeaways' toe! Stop direct na Overzicht per modaliteit.\n"
                f"- Voeg GEEN reisadvies toe (geen Reisadvies sectie).\n"
                f"- Toon WEL specifieke treinnummers en treinseries (zoals NS Intercity, Sprinter, regionale spoorlijnen).\n"
                f"- Toon GEEN buslijnnummers en GEEN specifieke bushaltenamen.\n"
                f"- Houd het bericht beknopt, professioneel en direct scanbaar."
            )
            user_prompt = (
                f"Samenvatting gegevens voor {reg}:\n"
                f"- Stakingsacties actief/gepland: {'Ja' if has_strikes else 'Nee (of berichten melden dat er niet gestaakt wordt tot latere datum)'}\n"
                f"- Werkzaamheden/verstoringen actief: {'Ja' if has_works else 'Nee'}\n"
                f"- Totaal geregistreerde gebeurtenissen: {len(events)}\n\n"
                f"Genereer nu het uniforme Telegram HTML-bericht voor de algemene netwerkstatus conform de opmaakregels."
            )

        raw_msg = self._call_ollama(system_prompt, user_prompt)

        # Enforce green status if neither strikes nor works are active
        if not has_strikes and not has_works:
            raw_msg = re.sub(r"<b>\s*[🔴🟡]\s*", "<b>🟢 ", raw_msg)
            raw_msg = re.sub(
                r"(?:VERSTOORD\s*/\s*STAKINGSACTIE|DISRUPTED\s*/\s*STRIKE\s*ACTION|BEPERKTE\s*HINDER\s*/\s*WERKZAAMHEDEN|MODERATE\s*DELAYS\s*/\s*ENGINEERING\s*WORKS)",
                "REGULIERE DIENSTREGELING" if not self.is_english else "REGULAR SCHEDULE",
                raw_msg,
                flags=re.IGNORECASE,
            )

        # Strictly strip any Key Takeaways / Belangrijkste aandachtspunten block
        raw_msg = re.sub(
            r"<b>\s*(?:Key\s*Takeaways?|Belangrijkste\s*aandachtspunten)\s*:?\s*</b>.*",
            "",
            raw_msg,
            flags=re.IGNORECASE | re.DOTALL,
        )

        return raw_msg.strip()

    def _fallback_report(self, events: List[DisruptionEvent], period_str: str) -> DigestReport:
        reg = self.regions_label
        strikes = [e for e in events if e.is_strike]
        works = [e for e in events if not e.is_strike]

        has_substantive = has_substantive_strikes(strikes)
        has_substantive_works = bool(works)

        if self.is_english:
            strikes_msg: Optional[str] = None
            if strikes and has_substantive:
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
            if works and has_substantive_works:
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

            status_icon = "🔴" if has_substantive else ("🟡" if has_substantive_works else "🟢")
            status_text = (
                "Disrupted due to strike actions" if has_substantive else (
                    "Minor disruptions due to engineering works" if has_substantive_works else "All lines running on regular schedule"
                )
            )
            status_lines = [
                f"<b>{status_icon} PT MONITOR {reg.upper()}: GENERAL STATUS</b>",
                f"<i>Monitoring period: {period_str}</i>\n",
                "<b>Network Status:</b>",
                f"{status_text} ({reg}).\n",
                "<b>Overview by Modality:</b>",
                "• <b>Train Connections:</b> Rail corridors operational; expect delays on lines with engineering works.",
                "• <b>Bus Network:</b> Regional and city buses running with local detours.",
            ]
            status_msg = "\n".join(status_lines)
        else:
            strikes_msg = None
            if strikes and has_substantive:
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
            if works and has_substantive_works:
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

            status_icon = "🔴" if has_substantive else ("🟡" if has_substantive_works else "🟢")
            status_text = (
                "Verstoord wegens stakingsacties" if has_substantive else (
                    "Beperkte hinder door werkzaamheden" if has_substantive_works else "Alle lijnen rijden volgens dienstregeling"
                )
            )
            status_lines = [
                f"<b>{status_icon} OV MONITOR {reg.upper()}: ALGEMENE STATUS</b>",
                f"<i>Peilperiode: {period_str}</i>\n",
                "<b>Netwerkstatus:</b>",
                f"{status_text} ({reg}).\n",
                "<b>Overzicht per modaliteit:</b>",
                "• <b>Treinverbindingen:</b> Spoorcorridors operationeel, houd rekening met eventuele werkzaamheden.",
                "• <b>Busnetwerk:</b> Regionale en stadsbussen rijden met lokale omleidingen.",
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
