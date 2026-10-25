# Netherlands Public Transport Monitor (`Ontspoord`)

A Python CLI and scheduled automation tool that monitors public transport strikes, service disruptions, and planned engineering works across the Netherlands over the next 7 days, summarizes them locally using Gemma 4 E4B (`gemma4:e4b`) via Ollama, and delivers structured multi-message digests to Telegram DM.

## Features
- **Multi-Source Data Acquisition**:
  - **Arriva Staking Scraper**: Scrapes `https://www.arriva.nl/plan-je-reis/staking/` for train and bus strike notices across regional and nationwide concessions.
  - **Arriva Storingen & Werkzaamheden**: Scrapes `https://www.arriva.nl/plan-je-reis/storingen-en-werkzaamheden/` for active and upcoming route detours, track maintenance, and disruptions.
  - **9292 Staking-OV**: Scrapes `https://9292.nl/actueel/staking-ov/` for national and regional strike dates, union announcements (FNV/CNV), and regional impact.
  - **NS Reisinformatie API v3**: Direct integration with NS API v3 querying `CALAMITY`, `DISRUPTION`, and `MAINTENANCE` without `isActive=true` to capture upcoming announced strikes and weekend track work.
  - **NS Web Fallback**: Scrapes `https://www.ns.nl/reisinformatie/actuele-situatie-op-het-spoor/` for major network-wide calamity alerts.
- **7-Day Rolling Window**: Filters all events in the `Europe/Amsterdam` timezone to `[now, now + 7 days]`.
- **Nationwide & Configurable Regions**:
  - Monitors the entire Netherlands by default (`TARGET_REGIONS=Nederland`).
  - Optional `.env` toggles to target any specific province(s) (e.g. `TARGET_REGIONS=Utrecht,Noord-Holland` or `Limburg`), station codes, or keywords.
- **3-Message Structure**:
  1. **Strikes** (conditional: evaluated by Gemma and skipped if no strike actions are active/planned).
  2. **Works & Disruptions** (conditional: sent only if active/planned works exist).
  3. **General Status** (always sent; concise status overview by modality, with green 🟢 status when regular service runs).
- **Local Gemma 4 E4B Summarization**
- **Telegram DM Delivery**
- **Flexible Execution Modes**:
  - `--once`: Single immediate check.
  - `--daemon`: Continuous monitoring with APScheduler at `07:30` and `19:30` (`Europe/Amsterdam`).
  - `--dry-run`: Fetches live data and runs Gemma 4 E4B summarization, printing to stdout without calling Telegram.

## Setup & Installation

### 1. Prerequisites
- Python 3.11+
- Playwright Chromium installed (`playwright install chromium` or system chromium)
- Ollama running locally with `gemma4:e4b` (`ollama run gemma4:e4b`)

### 2. Environment Setup
```bash
uv venv .venv --system-site-packages
.venv/bin/python -m pip install -e .
```

### 3. Configuration
Copy `.env.example` to `.env` and fill in your secrets:
```bash
cp .env.example .env
```

Configuration variables:
- `NS_API_KEY`: API Key from NS API Developer Portal.
- `TELEGRAM_BOT_TOKEN`: Telegram bot token from @BotFather.
- `TELEGRAM_CHAT_ID`: Your Telegram numeric chat ID.
- `ELEVEN_API_KEY`: ElevenLabs API key for optional interactive voice messages (<=15s if calm, <=45s if disruptions).
- `ELEVENLABS_VOICE_ID`: ElevenLabs voice ID (default: `EXAVITQu4vr4xnSDxMaL` / Bella).
- `ELEVENLABS_MODEL_ID`: ElevenLabs model (default: `eleven_multilingual_v2`).
- `VOICE_ENABLED`: Set to `true` to always send voice notes with scheduled reports (default: `false`).
- `OLLAMA_HOST`: URL of local Ollama server (default: `http://localhost:11434`).
- `OLLAMA_MODEL`: Target model (default: `gemma4:e4b`).
- `SCHEDULE_TIMES`: Comma-separated HH:MM execution times (default: `07:30,19:30`).
- `TIMEZONE`: Timezone for schedule and 7-day window (default: `Europe/Amsterdam`).
- `LANGUAGE`: Language for generated messages: `nl` (Dutch, default) or `en` (English).
- `TARGET_REGIONS`: Monitored regions toggle (default: `Nederland` for nationwide; or comma-separated regions e.g. `Utrecht,Noord-Holland` or `Limburg`).
- `TARGET_STATION_CODES`: Monitored NS station codes (comma-separated, e.g. `UT,ASD`; leave empty for all stations nationwide). For a complete list of station codes, visit [this Wikipedia page](https://en.wikipedia.org/wiki/Railway_stations_in_the_Netherlands#List_of_stations,_with_their_official_abbreviations).
- `TARGET_KEYWORDS`: Monitored route, city, and corridor keywords (comma-separated; leave empty for all).

## Usage

### Codebase Overview
- `main.py`: CLI entrypoint and scheduler wiring for one-off, dry-run, and daemon modes.
- `src/collectors/`: Source-specific collectors that normalize external data into shared models.
- `src/models.py`: Shared Pydantic models for disruption events and report payloads.
- `src/summarizer.py`: LLM prompt construction plus digest generation logic.
- `src/notifier.py`: Telegram delivery logic, including chunking, retries, and optional voice delivery.
- `src/time_utils.py`: Common timezone/date parsing helpers used by collectors.
- `tests/`: Unit tests for summarization and voice behavior.

### Single Immediate Run (`--once`)
```bash
.venv/bin/python main.py --once
.venv/bin/python main.py --once --lang en
# Optional interactive voice summary via Gemma + ElevenLabs:
.venv/bin/python main.py --once --voice
```

### Dry Run (`--dry-run`)
Fetches live data and outputs the generated summary directly to stdout without sending to Telegram:
```bash
.venv/bin/python main.py --dry-run
.venv/bin/python main.py --dry-run --lang en
# Preview generated 11Labs voice script (<=15s calm, <=45s disruptions):
.venv/bin/python main.py --dry-run --voice
```

### Scheduled Daemon (`--daemon`)
Runs in the background and executes checks at `07:30` and `19:30` Amsterdam time:
```bash
.venv/bin/python main.py --daemon
```
