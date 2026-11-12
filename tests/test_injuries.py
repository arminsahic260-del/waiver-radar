"""Hurt teammates (12.11.2026).
Drops: Ziaire Williams was offered as a drop while Reaves, Dončić, Grimes and Sexton were all DTD.
Pickups: a one-day streamer gains when a teammate who matters misses that game (measured on this season: rotation
players under 28 min get ~12 % of the missing player's FP, 28+ min starters ~nothing)."""
import copy

from helpers import engine, morning, player, team

from radar.alarms import diff, snapshot
from radar.config import ModelCfg, PPlayCfg
from radar.injuries import hurt_by_team, opening
from radar.models import GameLog
from radar.planner import FA, Cand, Planner
from radar.report import detail_report, move_line
from test_planner import ALL, _analyze

WEEK = list(range(21, 28))


def _played(p, *sps, minutes=30):
    p.games = [GameLog(sp=sp, fpts=p.season.avg, min=minutes) for sp in sps]
    return p


# ---- who counts --------------------------------------------------------------------------------------------------
def test_hurt_by_team_and_opening():
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
    hurt = hurt_by_team(pool, today_sp=24, min_value=18)
    assert [q.id for q in hurt[13]] == [2, 3] and [q.id for q in hurt[7]] == [6]
    o = opening(me, hurt, {})
    assert o is not None and o.fp == 4.5                                 # drop shield: OUT 3 + DTD 1.5
    assert o.text == f"{me.team}: Doncic DTD, Reaves OUT" and set(o.mates) == {2, 3}
    assert opening(pool[2], hurt, {}).text == f"{me.team}: Reaves OUT"   # never himself
    assert opening(pool[6], {13: hurt[13]}, {}) is None


# ---- pickups: per-game boost -------------------------------------------------------------------------------------
def _eng(players, games, hurt_ids, now_sp=21):
    eng = engine(games, players, morning(now_sp), [d for d in WEEK if d >= now_sp])
    eng.pcfg = PPlayCfg()                                                # real p_play: DTD misses sometimes
    by_id = {p.id: p for p in players}
    eng.hurt = hurt_by_team({i: by_id[i] for i in hurt_ids}, now_sp, ModelCfg().suigrac_min_fp)
    return eng


def test_boost_for_a_bench_streamer_when_a_teammate_misses():
    star = _played(player(1, 40, 3, "PG", injury="OUT"), 20)
    dtd = _played(player(2, 40, 4, "PG", injury="DAY_TO_DAY"), 20)
    bench = _played(player(10, 20, 3, "SG"), 18, 19, minutes=20)         # 20 min: gets 12 % of the star's 40
    starter = _played(player(11, 30, 3, "SF"), 18, 19, minutes=32)      # 28+ min: 2 %
    bench4 = _played(player(12, 20, 4, "SG"), 18, 19, minutes=20)
    eng = _eng([star, dtd, bench, starter, bench4], {3: [21, 23], 4: [21, 24]}, [1, 2])
    assert eng.boost(10, 21) == 4.8 and eng.val(10, 21) == round((20 + 4.8) * 0.97, 3)
    assert eng.boost(11, 21) == 0.8
    assert eng.boost(1, 21) == 0                                         # not for himself
    assert eng.boost(12, 21) == round(0.4 * 0.12 * 40, 2)                # DTD today: misses 40 %
    assert eng.boost(12, 24) == round(0.15 * 0.12 * 40, 2)               # ... later in the week 15 %


def test_boost_is_capped_and_fades_once_his_numbers_show_it():
    stars = [_played(player(i, 70, 3, "PG", injury="OUT"), 20) for i in (1, 2)]
    bench = _played(player(10, 30, 3, "SG"), 18, 19, minutes=20)
    eng = _eng(stars + [bench], {3: ALL}, [1, 2])
    assert eng.boost(10, 21) == 10                                       # 2 x 8.4 -> max 10 FP
    late = _played(player(11, 20, 3, "SG"), 21, 22, minutes=20)         # 2 games already without them
    eng = _eng(stars + [late], {3: ALL}, [1, 2], now_sp=23)
    assert eng.boost(11, 23) == round(min(2 * 0.12 * 70 * 0.5, 10, 0.5 * 20), 2)


def test_streamer_with_a_missing_teammate_wins_the_pick():
    """Two free agents worth the same, same games: the one whose star teammate is OUT is the pickup."""
    stars = [player(200 + i, 40, 5, "ANY") for i in range(3)]          # no games, protected
    core = [player(100 + i, 30, 1, "ANY") for i in range(9)]
    x = player(1, 10, 2, "ANY", name="Xavier Drop")
    star = _played(player(60, 45, 3, "PG", injury="OUT", name="Star Hurt"), 20)
    with_gap = _played(player(61, 22, 3, "SG", name="Gap Taker"), 18, 19, minutes=20)
    plain = _played(player(62, 22, 4, "SG", name="Plain Guy"), 18, 19, minutes=20)
    me = team(core + stars + [x], slots={**{p.id: s for p, s in zip(core, [0, 1, 2, 3, 4, 5, 6, 11, 11])}, 1: 11})
    opp = team([star] + [player(900 + i, 20, 1, "ANY") for i in range(9)], tid=2)
    a = _analyze(me, opp, [(with_gap, "FREEAGENT", None), (plain, "FREEAGENT", None)],
                 games={1: ALL, 2: [], 3: [21, 23], 4: [21, 23], 5: []})
    gains = {f.pid: f.best_gain for f in a.free}
    assert gains[61] > gains[62]
    assert a.plan and a.plan[0].add == 61
    assert a.mate_boost(61) == 5.4 and a.mate_boost(62) == 0
    assert "🩹 " + a.hurt_note(61) in move_line(a, a.plan[0])
    assert a.hurt_note(61) == f"{star.team}: Hurt OUT → +5 FP/utak."


def test_alarm_when_a_teammate_goes_out_today():
    stars = [player(200 + i, 40, 5, "ANY") for i in range(3)]
    core = [player(100 + i, 30, 1, "ANY") for i in range(9)]
    x = player(1, 10, 2, "ANY", name="Xavier Drop")
    star = _played(player(60, 45, 3, "PG", injury="OUT", name="Star Hurt"), 20)
    mate = _played(player(61, 22, 3, "SG", name="Gap Taker"), 18, 19, minutes=20)
    me = team(core + stars + [x], slots={**{p.id: s for p, s in zip(core, [0, 1, 2, 3, 4, 5, 6, 11, 11])}, 1: 11})
    opp = team([star] + [player(900 + i, 20, 1, "ANY") for i in range(9)], tid=2)
    a = _analyze(me, opp, [(mate, "FREEAGENT", None)], games={1: ALL, 2: [], 3: [21, 23], 5: []})
    cur = snapshot(a.lg, a)
    prev = copy.deepcopy(cur)
    prev["players"]["60"][2] = "DAY_TO_DAY"
    ev = [e for e in diff(prev, cur, a, a.lg) if e.kind == "suigrac"]
    assert len(ev) == 1 and ev[0].key == f"mateout:{a.label}:60:21"
    t = ev[0].text
    assert "Star Hurt" in t and "DTD → OUT" in t and "Taker (FA) dobiva ~+5 FP" in t and "Uzmi ga za Drop" in t
    assert not [e for e in diff(cur, cur, a, a.lg) if e.kind == "suigrac"]     # already OUT: no news


# ---- drops ---------------------------------------------------------------------------------------------------------
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
