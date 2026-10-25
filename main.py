import argparse
import logging
import sys
from typing import Optional
from zoneinfo import ZoneInfo
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from src.config import settings
from src.collectors import collect_all_disruptions
from src.models import DigestReport
from src.summarizer import DisruptionSummarizer
from src.notifier import TelegramNotifier
from src.voice import send_voice_summary

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("nl-ov-monitor")


def run_job(
    dry_run: bool = False,
    language: Optional[str] = None,
    voice: bool = False,
) -> DigestReport:
    logger.info("Starting OV disruption check...")
    events = collect_all_disruptions()
    logger.info(f"Total deduplicated events for next 7 days: {len(events)}")

    summarizer = DisruptionSummarizer(language=language)
    report = summarizer.generate_report(events)

    should_send_voice = voice or settings.voice_enabled

    if dry_run:
        print("\n" + "=" * 60)
        print("DRY RUN: MESSAGE 1 - STRIKES (Conditional)")
        print("=" * 60)
        if report.strikes_message:
            print(report.strikes_message)
        else:
            print("(Geen stakingen gemeld - bericht wordt niet verzonden)")

        print("\n" + "=" * 60)
        print("DRY RUN: MESSAGE 2 - WORKS & DISRUPTIONS (Conditional)")
        print("=" * 60)
        if report.works_message:
            print(report.works_message)
        else:
            print("(Geen werkzaamheden of storingen gemeld - bericht wordt niet verzonden)")

        print("\n" + "=" * 60)
        print("DRY RUN: MESSAGE 3 - GENERAL STATUS OF THE LINES (Always sent)")
        print("=" * 60)
        print(report.general_status_message)
        print("=" * 60 + "\n")
        logger.info(f"Dry run complete. Generated {len(report.get_messages_to_send())} message(s). No Telegram notification sent.")

        if should_send_voice:
            send_voice_summary(events=events, language=language, dry_run=True)
    else:
        notifier = TelegramNotifier()
        success = notifier.send_report(report)
        if success:
            logger.info(f"Telegram report ({len(report.get_messages_to_send())} message(s)) sent successfully.")
        else:
            logger.warning("Telegram report was not delivered (check credentials or logs).")

        if should_send_voice:
            send_voice_summary(events=events, language=language, dry_run=False, notifier=notifier)

    return report


def start_daemon(language: Optional[str] = None, voice: bool = False):
    scheduler = BlockingScheduler()
    tz = ZoneInfo(settings.timezone)
    schedule_times = settings.parsed_schedule_times

    logger.info(f"Starting daemon scheduler in timezone {settings.timezone}")
    for hour, minute in schedule_times:
        trigger = CronTrigger(hour=hour, minute=minute, timezone=tz)
        scheduler.add_job(
            run_job,
            trigger=trigger,
            args=[False, language, voice],
            name=f"OV-Check-{hour:02d}:{minute:02d}",
        )
        logger.info(f"Scheduled check at {hour:02d}:{minute:02d} ({settings.timezone})")

    logger.info("Executing initial check on daemon startup...")
    run_job(dry_run=False, language=language, voice=voice)

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Daemon scheduler stopped.")


def main():
    parser = argparse.ArgumentParser(
        description="NL OV Monitor: Monitor strikes & disruptions across the Netherlands with Gemma 4 E4B."
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single check immediately and exit.",
    )
    parser.add_argument(
        "--daemon",
        action="store_true",
        help="Run continuously as a daemon with APScheduler (07:30 and 19:30 Europe/Amsterdam).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch data, run local Gemma 4 summarization, and print to stdout without calling Telegram.",
    )
    parser.add_argument(
        "--voice",
        action="store_true",
        help="Generate and send an optional interactive voice message via Gemma & ElevenLabs (<=15s if calm, <=45s if disruptions).",
    )
    parser.add_argument(
        "--lang",
        "--language",
        dest="language",
        choices=["nl", "en"],
        default=None,
        help="Language for generated messages ('nl' for Dutch, 'en' for English; defaults to LANGUAGE in .env).",
    )

    args = parser.parse_args()

    if args.dry_run:
        run_job(dry_run=True, language=args.language, voice=args.voice)
    elif args.daemon:
        start_daemon(language=args.language, voice=args.voice)
    elif args.once or len(sys.argv) == 1:
        run_job(dry_run=False, language=args.language, voice=args.voice)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
