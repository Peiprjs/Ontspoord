---
layout: default
title: Ontspoord - Netherlands Public Transport Monitor
description: Automated monitoring, local AI summarization, and Telegram alerts for Dutch public transport strikes, service disruptions, and planned maintenance.
theme: jekyll-theme-cayman
---

# Ontspoord (Netherlands Public Transport Monitor)

**Ontspoord** (Dutch for *Derailed*) is an automated public transport monitoring service and command-line tool. It continuously tracks upcoming and active rail and bus disruptions, planned engineering works, and nationwide or regional strikes across the Netherlands over a rolling 7-day window.

Disruption data gathered from multiple authoritative transit sources is filtered, deduplicated, and synthesized locally using **Gemma 4 E4B** via **Ollama**, and delivered directly to your Telegram direct messages as clean, structured digests.

---

## Why This Project Was Created



---

## Key Features & Architecture

- **Multi-Source Data Acquisition**:
  - **NS Reisinformatie API v3**: Live integration querying active and upcoming calamitous events (`CALAMITY`), disruptions (`DISRUPTION`), and scheduled maintenance (`MAINTENANCE`).
  - **NS Web Fallback**: Real-time scraper targeting NS network-wide calamity announcements.
  - **Arriva Staking Scraper**: Headless browser scraper monitoring train and bus strike notices across regional and national concessions.
  - **Arriva Storingen & Werkzaamheden**: Scrapes active and scheduled route diversions, maintenance, and track disruptions.
  - **9292 Staking-OV**: Scrapes union announcements (FNV/CNV) and nationwide or regional public transport strike schedules.
- **7-Day Rolling Horizon**:
  - Filters all transit events strictly within `[now, now + 7 days]` in the `Europe/Amsterdam` timezone.
- **Granular Geographic Filtering**:
  - Monitors the entire Netherlands by default (`TARGET_REGIONS=Nederland`).
  - Supports targeted monitoring by province (e.g. `Utrecht,Noord-Holland` or `Limburg`), specific NS station codes (e.g. `UT`, `ASD`), or custom line/corridor keywords.
- **Local AI Synthesis with Gemma 4 E4B**:
  - Summarizes complex, overlapping disruptions into concise commuter advice without sending private data to external cloud LLM providers.
  - Fully bilingual: generates reports in Dutch (`nl`, default) or English (`en`).
- **Structured 3-Message Digest Delivery**:
  1. **Strikes** *(Conditional)*: Sent only when strikes are announced; details unions, dates, affected train series, bus concessions, and alternatives.
  2. **Works & Disruptions** *(Conditional)*: Sent only when significant maintenance or disruptions occur; outlines impact areas, affected routes, and duration.
  3. **General Status** *(Always sent)*: High-level overview of overall transit network health and commuter advice.
- **Flexible Execution Modes**:
  - Immediate one-off scan (`--once`).
  - Terminal dry-run mode (`--dry-run`) to preview LLM outputs without sending Telegram alerts.
  - Autonomous background daemon (`--daemon`) using APScheduler (configured by default for `07:30` and `19:30` Amsterdam time).

---

## How to Run It

### 1. Prerequisites

Before running Ontspoord, ensure you have the following installed:

- **Python 3.11+**
- **Ollama**: Running locally with the `gemma4:e4b` model pulled:
  ```bash
  ollama run gemma4:e4b
  ```
- **Playwright Chromium**: Required for scraping dynamic web notices:
  ```bash
  playwright install chromium
  ```
- **Telegram Bot**: A bot token from [@BotFather](https://t.me/botfather) and your numeric chat ID.
- **NS API Key**: Free API subscription key from the [NS API Developer Portal](https://apiportal.ns.nl/) (recommended for comprehensive NS disruption and maintenance data).

### 2. Installation & Setup

Clone the repository and set up a virtual environment:

```bash
# Clone the repository
git clone https://github.com/your-username/Ontspoord.git
cd Ontspoord

# Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
pip install -e .

# Install Playwright browser dependencies
playwright install chromium
```

*(Alternatively, if using `uv`:)*
```bash
uv venv .venv --system-site-packages
.venv/bin/python -m pip install -e .
playwright install chromium
```

### 3. Configuration

Copy the example environment file and configure your credentials:

```bash
cp .env.example .env
```

Edit `.env` with your settings:

```dotenv
# NS API & Telegram credentials
NS_API_KEY=your_ns_api_key_here
TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here
TELEGRAM_CHAT_ID=your_telegram_chat_id_here

# Local Ollama LLM Configuration
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=gemma4:e4b

# Scheduling & Localization
SCHEDULE_TIMES=07:30,19:30
TIMEZONE=Europe/Amsterdam
LANGUAGE=nl

# Monitored Regions (default: Nederland for nationwide)
TARGET_REGIONS=Nederland
TARGET_STATION_CODES=
TARGET_KEYWORDS=
```

### 4. Running the Monitor

Ontspoord supports three primary execution modes:

#### A. Single Immediate Check (`--once`)
Executes a single check, generates the AI disruption digest, and delivers messages to your Telegram:
```bash
python main.py --once
```
*To generate messages in English:*
```bash
python main.py --once --lang en
```

#### B. Dry-Run Mode (`--dry-run`)
Fetches all live transit data, runs the local Gemma 4 summarization, and prints the generated 3-part digest directly to your terminal without calling the Telegram API:
```bash
python main.py --dry-run
```
*Dry-run in English:*
```bash
python main.py --dry-run --lang en
```

#### C. Continuous Background Daemon (`--daemon`)
Starts an APScheduler daemon that performs an initial check immediately upon startup, followed by recurring scheduled checks at the specified times (`07:30` and `19:30` Europe/Amsterdam by default):
```bash
python main.py --daemon
```

### 5. Running Tests

To run the automated test suite with pytest:
```bash
pytest
```
