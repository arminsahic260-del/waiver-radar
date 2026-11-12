"""Hurt teammates of MY players (12.11.2026: Ziaire Williams was offered as a drop while Reaves, Dončić, Grimes
and Sexton were all DTD): shown next to the drop, and the planner needs more to drop him."""
from helpers import engine, morning, player, team

from radar.models import GameLog
from radar.injuries import opening
from radar.planner import FA, Cand, Planner
from radar.report import detail_report
from test_planner import ALL, _analyze

WEEK = list(range(21, 28))


def _played(p, sp, fpts=None):
    p.games = [GameLog(sp=sp, fpts=fpts if fpts is not None else p.season.avg, min=30)]
    return p


def test_opening_counts_fresh_injuries_that_matter():
    me = player(1, 25, 13, "SF", name="Ziaire Williams")
    pool = {p.id: p for p in [
        me,
        _played(player(2, 60, 13, "PG", injury="DAY_TO_DAY", name="Luka Doncic"), 23),
        _played(player(3, 40, 13, "SG", injury="OUT", name="Austin Reaves"), 22),
        _played(player(4, 45, 13, "SF", injury="OUT", name="Long Gone"), 5),       # out for weeks: already priced in
        _played(player(5, 10, 13, "PG", injury="DAY_TO_DAY", name="Bench Guy"), 23),   # doesn't matter
        _played(player(6, 50, 7, "PG", injury="OUT", name="Other Team"), 23),
        _played(player(7, 30, 13, "C", name="Healthy Mate"), 23),
    ]}
    o = opening(me, pool, {}, today_sp=24)
    assert o is not None and o.fp == 4.5                                 # OUT 3 + DTD 1.5
    assert o.text == f"{me.team}: Doncic DTD, Reaves OUT" and set(o.mates) == {2, 3}
    assert opening(pool[7], {k: v for k, v in pool.items() if k not in (2, 3)}, {}, today_sp=24) is None


def test_shield_keeps_him_from_being_streamed_away():
    """Without the shield X (26) goes for Y (27); with 4 hurt teammates (+6) he stays."""
    stars = [player(100 + i, 40, 3, "ANY") for i in range(12)]          # no games this week, protected
    x, y = player(1, 26, 2, "ANY"), player(2, 27, 4, "ANY")
    eng = engine({2: [], 3: [], 4: [22, 24, 26]}, stars + [x, y], morning(21), WEEK)

    def plan(shield):
        return Planner(eng=eng, base={p.id for p in stars + [x]}, ir=set(), cands=[Cand(y.id, FA, 21)],
                       adds_left=7, protect=4.0, shield=shield).plan()

    assert [(m.drop, m.add) for m in plan({})] == [(x.id, y.id)]
    assert plan({x.id: 6.0}) == []


def test_analysis_flags_hurt_teammates_next_to_the_drop():
    x = player(50, 12, 2, "ANY", name="Xavier Drop")
    star = _played(player(60, 45, 2, "PG", injury="DAY_TO_DAY", name="Star Mate"), 20)
    rest = [player(i, 30, 1, "ANY") for i in range(9)] + [player(70 + i, 40, 4, "ANY") for i in range(3)]
    me = team(rest + [x], slots={**{i: s for i, s in zip(range(9), [0, 1, 2, 3, 4, 5, 6, 11, 11])}, 50: 11})
    opp = team([star] + [player(900 + i, 20, 1, "ANY") for i in range(9)], tid=2)
    a = _analyze(me, opp, games={1: ALL, 2: [21, 23], 4: []})
    assert a.hurt_mates(x.id) == f"{x.team}: Mate DTD"
    assert a.openings[x.id].fp == 1.5
    assert any(s.kind == "teammate" for s in a.values[x.id].signals)
    assert a.values[x.id].long == 12                                     # shown, but his value is not inflated
    assert "⚠️ " + a.hurt_mates(x.id) in detail_report(a)
    assert a.hurt_mates(rest[0].id) == "" and a.hurt_mates(None) == ""
