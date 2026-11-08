"""Parsing real ESPN responses (Liga Legendi, 8.11.2026)."""
import copy
import datetime as dt

from radar.espn.client import sanitize
from radar.espn.parse import parse_league
from radar.schedule import week_bounds


def _league(client, mutate=None):
    raw, pool = client.league(0, False, "legendi"), client.player_pool(0, False, "legendi")
    if mutate:
        raw, pool = copy.deepcopy(raw), copy.deepcopy(pool)
        mutate(raw, pool)
    return parse_league("legendi", raw, pool, client.transactions(0, False, "legendi"), 1, 2027)


def test_schedule_dates(schedule):
    assert schedule.first_day == dt.date(2026, 10, 20)
    assert schedule.date(15) == dt.date(2026, 11, 3)
    assert schedule.date(20) == dt.date(2026, 11, 8)
    assert schedule.games_on(15) == []                      # 3.11.2026: no NBA games
    assert len(schedule.games_on(20)) == 7
    assert week_bounds(schedule, 0, None, fallback_sp=20) == (14, 20)   # Mon 2.11. - Sun 8.11.
    assert week_bounds(schedule, 0, None, fallback_sp=3) == (1, 6)      # week 1 starts Tuesday


def test_my_roster_score_and_opponent(client):
    lg = _league(client)
    assert lg.sp == 20 and lg.matchup_period == 3
    assert lg.me.name == "Druzina gospodara Flagga"
    names = {r.player.name for r in lg.me.roster}
    assert {"Cooper Flagg", "Kyrie Irving", "Aday Mara", "Paolo Banchero"} <= names
    assert len(lg.me.roster) == 14
    ir = [r.player.name for r in lg.me.roster if r.slot == 13]
    assert ir == ["Paolo Banchero"]
    assert lg.opp.name == "Bajka Basna"
    assert (lg.matchup.my_points, lg.matchup.opp_points) == (1129.0, 1201.0)
    assert lg.matchup.my_by_sp[14] == 291.0
    assert lg.me.adds_by_period["3"] == 5


def test_settings(client):
    s = _league(client).settings
    assert s.slot_counts == {0: 1, 1: 1, 2: 1, 3: 1, 4: 1, 5: 1, 6: 1, 11: 3}
    assert (s.bench, s.ir, s.max_centers) == (3, 1, 4)


def test_players_and_game_logs(client):
    lg = _league(client)
    flagg = next(r.player for r in lg.me.roster if r.player.name == "Cooper Flagg")
    assert flagg.team == "DAL" and flagg.positions == "SF/PF"
    assert flagg.season.gp >= 8 and flagg.season.avg > 30
    assert flagg.proj.avg is not None and flagg.games and flagg.games[-1].sp <= 20
    assert all(g.min > 0 for g in flagg.games)


def test_free_agents_exclude_rostered_and_carry_waiver_date(client):
    lg = _league(client)
    rostered = {pid for t in lg.teams.values() for pid in t.player_ids()}
    assert lg.free_agents and not any(f.player.id in rostered for f in lg.free_agents)
    w = [f for f in lg.free_agents if f.status == "WAIVERS"]
    assert w and all(f.waiver_ms for f in w)


def test_roster_cache_lag_is_corrected(client):
    """mRoster still shows a dropped player and misses a new one; kona's onTeamId wins."""
    def mutate(raw, pool):
        flagg_id = next(e["playerId"] for t in raw["teams"] if t["id"] == 1 for e in t["roster"]["entries"]
                        if e["playerPoolEntry"]["player"]["fullName"] == "Cooper Flagg")
        fa = next(x for x in pool["players"] if x["status"] == "FREEAGENT")
        for x in pool["players"]:
            if x["id"] == flagg_id:
                x.update(status="FREEAGENT", onTeamId=0)
        fa.update(status="ONTEAM", onTeamId=1)
        mutate.fa = fa["player"]["fullName"]

    lg = _league(client, mutate)
    names = {r.player.name for r in lg.me.roster}
    assert "Cooper Flagg" not in names and mutate.fa in names
    assert any("cache" in f for f in lg.roster_fixes)


def test_sanitize_strips_members():
    d = sanitize({"members": [{"firstName": "A"}], "teams": [{"owners": ["{X}"], "name": "T"}], "memberId": "{Y}"})
    assert "members" not in d and d["teams"][0]["owners"] == [] and d["memberId"] == ""
