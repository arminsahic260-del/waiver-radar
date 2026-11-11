"""ESPN daily lock (user's league rule, 12.11.2026): from a day's first tip-off an add counts from the next day, and
the player dropped then still counts that day's game. Week = SP 21 (Mon 9.11.) .. 27; games at 19:00 ET = 01:00 Zagreb."""
import datetime as dt
from zoneinfo import ZoneInfo

from helpers import player, team
from test_planner import ALL, _analyze

from radar.report import when_label

ZG = ZoneInfo("Europe/Zagreb")


def _a(now):
    me = team([player(i, 30, 1, "ANY") for i in range(10)])
    opp = team([player(900 + i, 20, 2, "ANY") for i in range(10)], tid=2)
    fa = player(60, 26, 3, "ANY", name="Yuri Add")
    return _analyze(me, opp, [(fa, "FREEAGENT", None)], now=now, games={1: ALL, 2: ALL, 3: ALL})


def test_before_first_tip_today_until_then_tomorrow_from_tonights_tip():
    a = _a(dt.datetime(2026, 11, 9, 8, 30, tzinfo=ZG))                  # Monday morning, SP 21
    assert a.today_sp == 21
    assert when_label(a, 21) == "DANAS DO 01:00"
    assert when_label(a, 22) == "NOĆAS OD 01:00"                        # Monday's games tip off Tue 01:00
    assert when_label(a, 24) == "NOĆ SRI/ČET OD 01:00"                  # for Thursday: from Wednesday's games
    assert next(f for f in a.free if f.pid == 60).avail == 21           # an add now still plays today


def test_after_first_tip_adds_count_from_tomorrow():
    a = _a(dt.datetime(2026, 11, 10, 1, 30, tzinfo=ZG))                 # Monday's games are on (still SP 21 in ET)
    assert a.today_sp == 21
    assert when_label(a, 22) == "ODMAH" and when_label(a, 21) == "DANAS"
    assert when_label(a, 23) == "NOĆ UTO/SRI OD 01:00"
    assert next(f for f in a.free if f.pid == 60).avail == 22           # ESPN puts him on the roster for Tuesday
    assert all(m.t >= 22 for m in a.plan)
