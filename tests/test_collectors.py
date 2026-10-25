from datetime import datetime

from src.collectors import collect_all_disruptions
from src.collectors.arriva import ArrivaCollector
from src.collectors.nine_two_nine_two import NineTwoNineTwoCollector
from src.collectors.ns_api import NSAPICollector
from src.collectors.ns_web import NSWebCollector
from src.models import DisruptionEvent


def test_arriva_parse_staking_html():
    html = """
    <main>
      <section>
        <h3>Bus</h3>
        <p><strong>Friesland</strong> Door stakingen rijden minder ritten.</p>
      </section>
    </main>
    <span class="subtitle">Actie aangekondigd</span>
    """
    collector = ArrivaCollector(target_regions=["Friesland"], keywords=[])

    events = collector.parse_staking_html(html)

    assert len(events) == 1
    event = events[0]
    assert event.provider == "Arriva"
    assert event.mode == "bus"
    assert event.is_strike is True


def test_arriva_parse_storingen_html(monkeypatch):
    monkeypatch.setattr("src.collectors.arriva.is_in_7day_window", lambda *_args, **_kwargs: True)
    html = """
    <div class="js-stremmingenList">
      <div data-component="collapsable">
        <button>
          <svg><use href="#icon-train"></use></svg>
          <h3>Zwolle - Leeuwarden</h3>
          <p class="text-black">Werkzaamheden</p>
          <div class="leading-normal text-mxs">van 25 oktober t/m 26 oktober</div>
        </button>
        <div class="js-collapseWrapper">
          <h4>Gevolg</h4><p>Minder treinen</p>
          <h4>Advies</h4><p>Check reisplanner</p>
        </div>
      </div>
    </div>
    """
    collector = ArrivaCollector(target_regions=[], keywords=["zwolle"])
    events = collector.parse_storingen_html(html)

    assert len(events) == 1
    assert events[0].mode == "train"
    assert events[0].impact == "Minder treinen"


def test_ns_api_parse_payload_filters_and_maps(monkeypatch):
    monkeypatch.setattr("src.collectors.ns_api.is_in_7day_window", lambda *_args, **_kwargs: True)
    collector = NSAPICollector(api_key="key", station_codes={"UT"}, keywords=["utrecht"])
    payload = {
        "payload": [
            {
                "title": "Staking Utrecht",
                "topic": "Utrecht Centraal",
                "start": "2026-10-25T09:00:00",
                "end": "2026-10-25T17:00:00",
                "section": {"stations": [{"code": "UT", "name": "Utrecht Centraal"}]},
                "consequence": {"description": "Geen treinen"},
                "advise": "Reis niet",
                "alternativeTransport": {"shortDescription": "Businzet"},
            },
            {"title": "Onrelevant", "topic": "Maastricht"},
        ]
    }

    events = collector.parse_payload(payload)

    assert len(events) == 1
    event = events[0]
    assert event.provider == "NS"
    assert "(UT)" in event.region_or_route
    assert "Businzet" in event.advice
    assert event.is_strike is True


def test_ns_web_parse_html(monkeypatch):
    monkeypatch.setattr("src.collectors.ns_web.is_in_7day_window", lambda *_args, **_kwargs: True)
    html = """
    <ns-web-ui-calamity-item>
      <span>Update 10:00</span>
      Landelijk staking van 25 oktober t/m 26 oktober
    </ns-web-ui-calamity-item>
    """
    collector = NSWebCollector(keywords=["amsterdam"])
    events = collector.parse_html(html)

    assert len(events) == 1
    assert events[0].title.startswith("NS Stakingswaarschuwing")
    assert events[0].is_strike is True


def test_nine_two_nine_two_parse_html(monkeypatch):
    monkeypatch.setattr("src.collectors.nine_two_nine_two.is_in_7day_window", lambda *_args, **_kwargs: True)
    html = """
    <article>
      <h1>Staking streekvervoer op 25 oktober 2026</h1>
      <p>Landelijke actie in het streekvervoer.</p>
      <h2>Arriva</h2>
      <p>In veel regio's vervallen ritten.</p>
    </article>
    """
    collector = NineTwoNineTwoCollector()
    events = collector.parse_html(html)

    assert len(events) == 1
    event = events[0]
    assert event.provider == "9292"
    assert event.is_strike is True
    assert "Arriva" in event.impact


def test_collect_all_disruptions_deduplicates_and_sorts(monkeypatch):
    strike = DisruptionEvent(
        provider="NS",
        mode="train",
        title="A",
        region_or_route="R",
        start_time=datetime(2026, 10, 25, 10, 0),
        impact="i",
        advice="a",
        source_url="u",
        is_strike=True,
    )
    duplicate = DisruptionEvent(
        provider="ns",
        mode="train",
        title=" A ",
        region_or_route="r ",
        start_time=datetime(2026, 10, 25, 11, 0),
        impact="i2",
        advice="a2",
        source_url="u2",
        is_strike=True,
    )
    work = DisruptionEvent(
        provider="Arriva",
        mode="bus",
        title="B",
        region_or_route="X",
        impact="i",
        advice="a",
        source_url="u",
        is_strike=False,
    )

    class _NSAPI:
        def fetch_disruptions(self):
            return [strike, duplicate]

    class _Arriva:
        def fetch_all(self):
            return [work]

    class _NSWeb:
        def fetch_disruptions(self):
            raise RuntimeError("boom")

    class _9292:
        def fetch_disruptions(self):
            return []

    monkeypatch.setattr("src.collectors.NSAPICollector", lambda: _NSAPI())
    monkeypatch.setattr("src.collectors.ArrivaCollector", lambda: _Arriva())
    monkeypatch.setattr("src.collectors.NSWebCollector", lambda: _NSWeb())
    monkeypatch.setattr("src.collectors.NineTwoNineTwoCollector", lambda: _9292())

    events = collect_all_disruptions()
    assert len(events) == 2
    assert events[0].is_strike is True
