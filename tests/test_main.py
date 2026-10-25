import sys
from unittest.mock import MagicMock

import main
from src.models import DigestReport


def test_run_job_dry_run_calls_voice_when_enabled(monkeypatch, capsys):
    report = DigestReport(strikes_message=None, works_message=None, general_status_message="status")
    summarizer = MagicMock()
    summarizer.generate_report.return_value = report

    monkeypatch.setattr("main.collect_all_disruptions", lambda: [])
    monkeypatch.setattr("main.DisruptionSummarizer", lambda language=None: summarizer)
    monkeypatch.setattr("main.settings.voice_enabled", True)
    voice_calls = []
    monkeypatch.setattr("main.send_voice_summary", lambda **kwargs: voice_calls.append(kwargs))

    result = main.run_job(dry_run=True, language="nl", voice=False)

    assert result == report
    assert len(voice_calls) == 1
    assert voice_calls[0]["dry_run"] is True
    assert "GENERAL STATUS" in capsys.readouterr().out


def test_run_job_non_dry_run_sends_report(monkeypatch):
    report = DigestReport(strikes_message="s", works_message=None, general_status_message="g")
    summarizer = MagicMock()
    summarizer.generate_report.return_value = report
    notifier = MagicMock()
    notifier.send_report.return_value = True

    monkeypatch.setattr("main.collect_all_disruptions", lambda: [])
    monkeypatch.setattr("main.DisruptionSummarizer", lambda language=None: summarizer)
    monkeypatch.setattr("main.TelegramNotifier", lambda: notifier)
    monkeypatch.setattr("main.settings.voice_enabled", False)

    result = main.run_job(dry_run=False, language="en", voice=False)
    assert result == report
    notifier.send_report.assert_called_once_with(report)


def test_start_daemon_schedules_jobs_and_runs_initial(monkeypatch):
    scheduler = MagicMock()
    monkeypatch.setattr("main.BlockingScheduler", lambda: scheduler)
    monkeypatch.setattr("main.CronTrigger", lambda **kwargs: ("trigger", kwargs))
    monkeypatch.setattr("main.settings.timezone", "Europe/Amsterdam")
    monkeypatch.setattr("main.settings.parsed_schedule_times", [(7, 30), (19, 30)])
    run_calls = []
    monkeypatch.setattr("main.run_job", lambda **kwargs: run_calls.append(kwargs))

    main.start_daemon(language="nl", voice=True)

    assert scheduler.add_job.call_count == 2
    assert run_calls == [{"dry_run": False, "language": "nl", "voice": True}]
    scheduler.start.assert_called_once()


def test_main_dispatches_modes(monkeypatch):
    run_calls = []
    daemon_calls = []
    monkeypatch.setattr("main.run_job", lambda **kwargs: run_calls.append(kwargs))
    monkeypatch.setattr("main.start_daemon", lambda **kwargs: daemon_calls.append(kwargs))

    monkeypatch.setattr(sys, "argv", ["prog", "--dry-run", "--lang", "en"])
    main.main()
    assert run_calls[-1] == {"dry_run": True, "language": "en", "voice": False}

    monkeypatch.setattr(sys, "argv", ["prog", "--daemon", "--voice"])
    main.main()
    assert daemon_calls[-1] == {"language": None, "voice": True}

    monkeypatch.setattr(sys, "argv", ["prog"])
    main.main()
    assert run_calls[-1] == {"dry_run": False, "language": None, "voice": False}
