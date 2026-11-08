"""Value model and the 'new starter' signal (Brayden Burries, MIL, starter from 2.11.2026)."""
import datetime as dt
import gzip
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from radar.espn.parse import parse_player
from radar.models import Player, StatLine
from radar.news import NewsItem, out_info, parse_feed, starter_hit
from radar.value import as_of, evaluate, value_long, value_now

BUR = Path(__file__).parent / "fixtures" / "burries"
Z = ZoneInfo("Europe/Zagreb")


def _load(name):
    with gzip.open(BUR / name, "rt", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def burries():
    p = parse_player(_load("burries_pool.json.gz")["players"][0]["player"], 2027)
    return p, parse_feed(_load("news_5082206.json.gz"), p.id)


def test_value_formulas():
    p = Player(id=1, name="A B", season=StatLine(gp=10, avg=30), last7=StatLine(gp=3, avg=40),
               proj=StatLine(gp=70, avg=20))
    assert value_now(p) == pytest.approx(0.45 * 30 + 0.35 * 40 + 0.20 * 20)
    assert value_long(p) == pytest.approx(0.35 * 30 + 0.30 * 40 + 0.35 * 20)
    rookie = Player(id=2, name="C D", season=StatLine(gp=1, avg=50), proj=StatLine(gp=70, avg=25))
    assert value_now(rookie) == pytest.approx(20.0)        # fewer than 2 games: projection x 0.8
    idle = Player(id=3, name="E F", season=StatLine(gp=10, avg=30), proj=StatLine(gp=70, avg=20))
    assert value_now(idle) == pytest.approx((0.45 * 30 + 0.2 * 20) / 0.65, abs=0.01)   # no games in last 7 days


@pytest.mark.parametrize("day", [3, 4, 5, 6, 7])
def test_burries_flagged_3_to_7_november(burries, schedule, day):
    p, news = burries
    now = dt.datetime(2026, 11, day, 8, 30, tzinfo=Z)
    sp = schedule.sp_of(now.astimezone(ZoneInfo("America/New_York")).date())
    v = evaluate(as_of(p, sp), [n for n in news if n.published < now], now, schedule)
    assert v.hot, f"{day}.11. nije označen"
    assert any(s.kind == "start" and s.text == "starter od 2.11." for s in v.signals)


@pytest.mark.parametrize("day", [1, 2])
def test_burries_not_flagged_before_he_started(burries, schedule, day):
    p, news = burries
    now = dt.datetime(2026, 11, day, 8, 30, tzinfo=Z)
    sp = schedule.sp_of(now.astimezone(ZoneInfo("America/New_York")).date())
    assert not evaluate(as_of(p, sp), [n for n in news if n.published < now], now, schedule).hot


def test_burries_role_value_rises_after_38_minute_game(burries, schedule):
    p, news = burries
    now = dt.datetime(2026, 11, 7, 8, 30, tzinfo=Z)
    v = evaluate(as_of(p, 19), [n for n in news if n.published < now], now, schedule)
    assert v.now > v.base_now + 2
    assert "zadnja: 21/11/6 u 38 min" in v.reason()


def _item(headline, story="", pid=1):
    return NewsItem(id="x", player_id=pid, published=dt.datetime(2026, 11, 3, tzinfo=dt.timezone.utc),
                    headline=headline, story=story)


def test_starter_keywords_and_negation():
    assert starter_hit(_item("Burries will start Monday's game against the Timberwolves."), "Burries")
    assert starter_hit(_item("Smith to enter the starting lineup", "With Haliburton out, Smith will get more."),
                       "Smith")
    assert not starter_hit(_item("Rollins will come off the bench Monday.", "He won't start."), "Rollins")
    assert not starter_hit(_item("Fantasy basketball rookie watch", "Burries will start..."), "Burries")
    # Claude's verdict wins over keywords
    i = _item("Burries will start Monday."); i.starter = False
    assert not starter_hit(i, "Burries")


def test_out_until_parsing():
    pub = dt.date(2026, 11, 3)
    i = out_info(_item("Holmgren will miss at least 8-10 weeks, Nick Crain of Slam reports."))
    assert (i.min_date - pub).days == 56 and (i.max_date - pub).days == 70
    i = out_info(_item("He will be re-evaluated in two weeks, the Jazz announced Friday."))
    assert (i.min_date - pub).days == 14 and i.kind == "weeks"
    assert out_info(_item("Simmons suffered a season-ending right Achilles tear.")).kind == "season"
    assert out_info(_item("Jackson will be sidelined for 6-to-8 weeks.")).label().startswith("do ~")
    assert out_info(_item("Burries scored 21 points in 38 minutes.")) is None
