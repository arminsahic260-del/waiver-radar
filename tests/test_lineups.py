"""RotoWire lineups as a second injury source (12.11.2026): tonight's % to play, scratches before ESPN, starters."""
import copy

from helpers import ET, FIRST, morning, player, team

import datetime as dt

from radar.alarms import diff, snapshot
from radar.lineups import Spot, match, p_from, parse
from radar.models import GameLog
from radar.schedule import ProSchedule
from test_planner import ALL, _analyze

LAL, BOS, TOR = 13, 2, 28


def _li(name, pct, pos="G", inj=None):
    tag = f'<span class="lineup__inj">{inj}</span>' if inj else ""
    return (f'<li class="lineup__player is-pct-play-{pct}{" has-injury-status" if inj else ""}" title="x">'
            f'<div class="lineup__pos">{pos}</div> <a title="{name}" href="/p">{name[0]}. x</a> {tag}</li>')


def _team_list(side, confirmed, starters, out):
    status = "is-confirmed" if confirmed else "is-expected"
    return (f'<ul class="lineup__list is-{side}"><li class="lineup__status {status}">Lineup</li>'
            + "".join(_li(*s) for s in starters)
            + '<li><button>Projected Minutes</button></li><li class="lineup__title is-middle">MAY NOT PLAY</li>'
            + "".join(_li(*s) for s in out) + "</ul>")


PAGE = ('<html><div class="lineup is-nba" data-lnum="1"><div class="lineup__time">7:00 PM ET</div>'
        '<div class="lineup__abbr">BOS</div><div class="lineup__abbr">LAL</div>'
        + _team_list("visit", False, [("Jayson Tatum", 100), ("Baylor Scheierman", 100)], [("Paul George", 50, "F", "Ques")])
        + _team_list("home", True, [("Luka Doncic", 100), ("Ziaire Williams", 100), ("Austin Reaves", 0)],
                     [("Austin Reaves", 0, "G", "Out"), ("Collin Sexton", 0, "G", "Out")])
        + '</div><div class="lineup is-nba" data-lnum="2"><div class="lineup__abbr">GS</div>'
          '<div class="lineup__abbr">TOR</div>'
        + _team_list("visit", False, [("Stephen Curry", 100)], []) + _team_list("home", False, [], [])
        + "</div></html>")


def _schedule(games):
    """{sp: [(away, home)]} -> ProSchedule with real away teams (match() checks both)."""
    teams = {}
    for sp, pairs in games.items():
        d = FIRST + dt.timedelta(days=sp - 1)
        start = dt.datetime(d.year, d.month, d.day, 19, tzinfo=ET)
        for away, home in pairs:
            g = {"id": sp * 1000 + home, "date": int(start.timestamp() * 1000), "homeProTeamId": home,
                 "awayProTeamId": away, "scoringPeriodId": sp}
            for tid in (away, home):
                teams.setdefault(tid, {}).setdefault(str(sp), []).append(g)
    return ProSchedule({"settings": {"proTeams": [{"id": t, "abbrev": f"T{t}", "proGamesByScoringPeriod": by}
                                                  for t, by in teams.items()]}})


def test_parse_reads_starters_status_and_pct():
    games = parse(PAGE)
    assert [(a, h) for a, h, _ in games] == [("BOS", "LAL"), ("GSW", "TOR")]    # 'GS' -> ESPN's 'GSW'
    spots = {s.name: s for s in games[0][2]}
    assert spots["Jayson Tatum"].starter and not spots["Jayson Tatum"].confirmed and spots["Jayson Tatum"].pct == 100
    assert spots["Paul George"].status == "Ques" and spots["Paul George"].pct == 50 and not spots["Paul George"].starter
    assert spots["Luka Doncic"].confirmed and spots["Luka Doncic"].team == "LAL"
    assert p_from(spots["Paul George"]) == 0.5 and p_from(spots["Luka Doncic"]) == 0.97


def test_match_by_team_and_name_and_merges_double_listing():
    players = {p.id: p for p in [
        player(1, 60, LAL, "PG", name="Luka Dončić"), player(2, 40, LAL, "SG", name="Austin Reaves"),
        player(3, 40, BOS, "SF", name="Paul George"), player(4, 20, BOS, "SG", name="Baylor Scheierman"),
        player(5, 25, LAL, "SF", name="Ziaire Williams"), player(6, 50, TOR, "PG", name="Luka Doncic"),
        player(7, 18, LAL, "PG", name="Collin Sexton Jr."),
    ]}
    sched = _schedule({24: [(BOS, LAL)]})                               # GSW-TOR is not on the schedule
    m = match(parse(PAGE), players, sched, today_sp=24)
    assert set(m) == {1, 2, 3, 4, 5, 7}                                 # accents, Jr. ignored; TOR's Luka isn't him
    assert m[2].pct == 0 and m[2].status == "Out" and m[2].starter    # merged: in the five AND 'may not play'
    assert all(s.sp == 24 for s in m.values())
    assert match(parse(PAGE), players, _schedule({25: [(BOS, LAL)]}), today_sp=24)[1].sp == 25  # page moved on


def _league(rw_pct, star_injury="ACTIVE"):
    stars = [player(200 + i, 40, 5, "ANY") for i in range(3)]
    core = [player(100 + i, 30, 1, "ANY") for i in range(9)]
    x = player(1, 10, 2, "ANY", name="Xavier Drop")
    star = player(60, 45, 3, "PG", injury=star_injury, name="Star Rested")
    star.games = [GameLog(sp=20, fpts=45, min=34)]
    mate = player(61, 22, 3, "SG", name="Gap Taker")
    mate.games = [GameLog(sp=s, fpts=22, min=20) for s in (18, 19)]
    me = team(core + stars + [x], slots={**{p.id: s for p, s in zip(core, [0, 1, 2, 3, 4, 5, 6, 11, 11])}, 1: 11})
    opp = team([star] + [player(900 + i, 20, 1, "ANY") for i in range(9)], tid=2)
    lineups = {} if rw_pct is None else {60: Spot("Star Rested", "NOP", rw_pct, "Out" if rw_pct == 0 else "Ques",
                                                  False, False, sp=21)}
    return _analyze(me, opp, [(mate, "FREEAGENT", None)], games={1: ALL, 2: [], 3: [21, 23], 5: []},
                    lineups=lineups)


def test_rotowire_out_tonight_counts_before_espn_and_raises_the_streamer():
    a0, a = _league(None), _league(0)
    assert a0.eng.p(60, 21) == 0.97 and a.eng.p(60, 21) == 0.0         # ESPN still says healthy
    assert a.eng.p(60, 23) == 0.97                                       # only tonight
    assert a0.mate_boost(61) == 0 and a.eng.boost(61, 21) == 5.4 and a.eng.boost(61, 23) == 0.16
    assert a.hurt_mates(61) == f"{a.values[60].player.team}: Rested Out večeras"
    gains = {f.pid: f.best_gain for f in a.free}, {f.pid: f.best_gain for f in a0.free}
    assert gains[0][61] > gains[1][61]


def test_alarm_fires_when_rotowire_rules_him_out():
    a = _league(0)
    cur = snapshot(a.lg, a)
    assert cur["players"]["60"][2] == "ACTIVE" and cur["players"]["60"][5] == 0
    prev = copy.deepcopy(cur)
    prev["players"]["60"][5] = 50
    ev = [e for e in diff(prev, cur, a, a.lg) if e.kind == "suigrac"]
    assert len(ev) == 1 and "zdrav, RotoWire 50 % → OUT večeras (RotoWire)" in ev[0].text
    old = copy.deepcopy(prev)
    for v in old["players"].values():
        del v[5:]                                                        # a snapshot saved before this change
    assert [e.kind for e in diff(old, cur, a, a.lg)] == ["suigrac"]
