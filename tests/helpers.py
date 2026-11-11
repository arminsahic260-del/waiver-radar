"""Small builders for synthetic scenarios (planner, alerts, lineup)."""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from radar.config import PPlayCfg
from radar.engine import Engine
from radar.models import Player, RosterSpot, StatLine, Team
from radar.schedule import ProSchedule
from radar.value import evaluate

ET = ZoneInfo("America/New_York")
FIRST = dt.date(2026, 10, 20)
SLOTS = {0: 1, 1: 1, 2: 1, 3: 1, 4: 1, 5: 1, 6: 1, 11: 3}
ELIG = {  # eligibleSlots as ESPN sends them
    "PG": [0, 5, 11, 12, 13], "SG": [1, 5, 11, 12, 13], "SF": [2, 6, 11, 12, 13], "PF": [3, 6, 11, 12, 13],
    "C": [4, 11, 12, 13], "PG/SG": [0, 1, 5, 11, 12, 13], "SF/PF": [2, 3, 6, 11, 12, 13], "PF/C": [3, 4, 6, 11, 12, 13],
    "ANY": [0, 1, 2, 3, 4, 5, 6, 11, 12, 13],
}
POS_ID = {"PG": 1, "SG": 2, "SF": 3, "PF": 4, "C": 5, "ANY": 3}


def raw_schedule(games: dict[int, list[int]], hour_et: int = 19) -> dict:
    """{pro team id: [scoring periods]} -> proTeamSchedules_wl-shaped JSON."""
    teams = []
    for tid, sps in games.items():
        by = {}
        for sp in sps:
            d = FIRST + dt.timedelta(days=sp - 1)
            start = dt.datetime(d.year, d.month, d.day, hour_et, 0, tzinfo=ET)
            by[str(sp)] = [{"id": tid * 1000 + sp, "date": int(start.timestamp() * 1000), "homeProTeamId": tid,
                            "awayProTeamId": 0, "scoringPeriodId": sp}]
        teams.append({"id": tid, "abbrev": f"T{tid}", "proGamesByScoringPeriod": by})
    return {"settings": {"proTeams": teams}}


def player(pid: int, avg: float, team: int, pos: str = "SF", injury: str = "ACTIVE", gp: int = 10,
           mpg: float = 28, name: str | None = None, proj: float | None = None) -> Player:
    return Player(id=pid, name=name or f"Igrac {pid}", pro_team_id=team, pos_id=POS_ID[pos.split("/")[-1]],
                  eligible=ELIG[pos], injury=injury,
                  season=StatLine(gp=gp, avg=avg, mpg=mpg), last7=StatLine(gp=3, avg=avg, mpg=mpg),
                  proj=StatLine(gp=70, avg=proj if proj is not None else avg, mpg=mpg))


def team(players: list[Player], slots: dict[int, int] | None = None, tid: int = 1) -> Team:
    slots = slots or {}
    return Team(id=tid, name="Moj tim", roster=[RosterSpot(player=p, slot=slots.get(p.id, 12)) for p in players])


def engine(sched_games: dict[int, list[int]], players: list[Player], now: dt.datetime, days: list[int]) -> Engine:
    sched = ProSchedule(raw_schedule(sched_games))
    values = {p.id: evaluate(p, [], now, sched) for p in players}
    return Engine(schedule=sched, now=now, today_sp=sched.sp_of(now.astimezone(ET).date()), days=days,
                  slot_counts=SLOTS, values=values, pcfg=PPlayCfg.neutral())


def morning(sp: int) -> dt.datetime:
    """08:30 Zagreb on the day of scoring period `sp`."""
    d = FIRST + dt.timedelta(days=sp - 1)
    return dt.datetime(d.year, d.month, d.day, 8, 30, tzinfo=ZoneInfo("Europe/Zagreb"))


def league_state(me, opp, free=(), period=4, sp=21, my_points=0.0, opp_points=0.0, adds_used=0, ir_slots=1):
    """A LeagueState for analyze(): `me`/`opp` are Teams, `free` (Player, status, waiver_ms) tuples."""
    from radar.models import FreeAgent, LeagueSettings, LeagueState, Matchup
    m = Matchup(period=period, my_points=my_points, opp_points=opp_points, opp_team_id=opp.id)
    me.adds_by_period = {str(period): adds_used}
    players = {r.player.id: r.player for t in (me, opp) for r in t.roster}
    fas = []
    for p, status, ms in free:
        players[p.id] = p
        fas.append(FreeAgent(player=p, status=status, waiver_ms=ms))
    return LeagueState(key="test", league_id=1, name="Test", sp=sp, matchup_period=period,
                       settings=LeagueSettings(slot_counts=SLOTS, bench=3, ir=ir_slots, max_centers=4),
                       me=me, opp=opp, matchup=m, teams={me.id: me, opp.id: opp}, matchups={period: m},
                       free_agents=fas, players=players)
