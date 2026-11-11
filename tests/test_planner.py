"""Planner timing rules that proved right when done by hand. Week = SP 21 (Mon 9.11.) .. 27 (Sun 15.11.)."""
import datetime as dt

from helpers import ET, engine, league_state, morning, player, raw_schedule, team

from radar.config import load_config
from radar.planner import FA, IR_BACK, WAIVER, Cand, Planner
from radar.report import league_report, move_line, when_label
from radar.schedule import ProSchedule
from radar.week import analyze

WEEK = list(range(21, 28))
ALL, MWFS = WEEK, [21, 23, 25, 27]


def _planner(games, roster, free, ir=(), **kw):
    now = kw.pop("now", morning(21))
    eng = engine(games, roster + list(free) + list(ir), now, [d for d in WEEK if d >= 21])
    cands = [Cand(p.id, kind, avail) for p, kind, avail in kw.pop("cands")]
    return Planner(eng=eng, base={p.id for p in roster}, ir={p.id for p in ir}, cands=cands,
                   adds_left=kw.pop("adds_left", 7), **kw)


def _core(n=9, team_id=1, avg=30):
    return [player(100 + i, avg, team_id, "ANY") for i in range(n)]


def test_drop_one_game_player_after_his_game():
    """X plays only Tuesday, Y plays Thu/Sat/Sun: drop X on Wednesday morning, not today."""
    core = _core()
    stars = [player(200 + i, 40, 3, "ANY") for i in range(3)]          # no games this week, protected
    x, y = player(1, 20, 2, "ANY"), player(2, 25, 4, "ANY")
    games = {1: MWFS, 2: [22], 3: [], 4: [24, 26, 27]}
    pl = _planner(games, core + stars + [x], [y], cands=[(y, FA, 21)])
    plan = pl.plan()
    assert len(plan) == 1
    m = plan[0]
    assert (m.drop, m.add, m.t) == (x.id, y.id, 23)
    assert m.gain == 75.0                                              # Y's three games, X keeps his Tuesday


def test_waiver_claim_lets_dropped_player_play_until_processing():
    core = _core()
    stars = [player(200 + i, 40, 3, "ANY") for i in range(3)]
    x, y = player(1, 20, 2, "ANY"), player(2, 25, 4, "ANY")
    games = {1: MWFS, 2: [22, 24], 3: [], 4: [24, 26, 27]}
    # claim placed Monday, processed Thursday (SP 24): X still plays Tuesday, loses Thursday
    pl = _planner(games, core + stars + [x], [y], cands=[(y, WAIVER, 24)])
    m = pl.plan()[0]
    assert (m.kind, m.t, m.drop) == (WAIVER, 24, x.id)
    assert m.gain == 75.0 - 20.0


def test_center_limit_respected():
    centers = [player(300 + i, 15, 3, "C") for i in range(4)]
    stars = [player(200 + i, 40, 5, "ANY") for i in range(8)]          # no games, protected
    w = player(1, 12, 3, "SF")
    yc, yn = player(2, 30, 4, "C"), player(3, 20, 4, "SF")
    games = {3: [21], 4: [22, 23, 24, 25, 26, 27], 5: []}
    pl = _planner(games, stars + centers + [w], [yc, yn], cands=[(yc, FA, 21), (yn, FA, 21)])
    plan = pl.plan()
    adds = {m.add: m for m in plan}
    assert yc.id in adds and adds[yc.id].drop in {c.id for c in centers}
    for d in WEEK:
        r = pl.roster_at(pl.base, plan, d)
        assert sum(1 for pid in r if pl.eng.values[pid].player.is_center) <= 4


def test_better_player_is_not_dropped_for_a_stream():
    core = _core()
    good = [player(200 + i, 40, 3, "ANY") for i in range(4)]           # no games this week
    y = player(2, 22, 4, "ANY")
    pl = _planner({1: MWFS, 3: [], 4: [22, 24, 26]}, core + good, [y], cands=[(y, FA, 21)])
    assert pl.plan() == []


def test_returning_ir_player_takes_the_spot_of_whoever_is_done():
    """R comes back Thursday; A (Mon-Wed games) makes room, B (Thu-Sun streamer) keeps his games."""
    core = _core(11, avg=30)
    a, b, r = player(1, 20, 2, "ANY"), player(2, 18, 3, "ANY"), player(3, 35, 4, "ANY", injury="OUT")
    games = {1: MWFS, 2: [21, 22, 23], 3: [24, 25, 26, 27], 4: [24, 25, 26, 27]}
    pl = _planner(games, core + [a, b], [], ir=[r], cands=[])
    pl.eng.values[r.id].player.injury = "ACTIVE"        # expected back from Thursday
    pl.eng._val.clear()
    plan = pl.plan(ir_returns=[(r.id, 24, False)])
    m = next(m for m in plan if m.kind == IR_BACK)
    assert (m.add, m.t, m.drop) == (r.id, 24, a.id)


def test_reserve_keeps_one_add_until_saturday():
    core = _core(5)
    stars = [player(200 + i, 40, 3, "ANY") for i in range(3)]
    fillers = [player(300 + i, 5, 3, "ANY") for i in range(5)]
    fas = [player(400 + i, 25, 4, "ANY") for i in range(8)]
    games = {1: MWFS, 3: [], 4: [22, 24]}
    pl = _planner(games, core + stars + fillers, fas, cands=[(p, FA, 21) for p in fas], adds_left=5,
                  reserve=1, reserve_from=26)
    early = [m for m in pl.plan() if m.t < 26]
    assert len(early) <= 4


# ---- full analysis on synthetic leagues -------------------------------------------------------------------------
def _analyze(me, opp, free=(), now=None, games=None, **kw):
    cfg = load_config()
    sched = ProSchedule(raw_schedule(games))
    lc = cfg.lige[0]
    lg = league_state(me, opp, free, **kw)
    return analyze(lg, lc, cfg, sched, now or morning(21), {}, {})


def test_lineup_alerts_out_starter_and_idle_starter():
    pg_out = player(1, 30, 1, "PG", injury="OUT", name="Mark Out")
    idle = player(2, 30, 2, "SG", name="Sam Idle")
    bench_pg = player(3, 20, 1, "PG", name="Pat Bench")
    bench_sg = player(4, 20, 1, "SG", name="Sid Bench")
    rest = [player(10 + i, 25, 3, "ANY") for i in range(8)]
    me = team([pg_out, idle, bench_pg, bench_sg] + rest,
              slots={1: 0, 2: 1, **{p.id: s for p, s in zip(rest, [2, 3, 4, 5, 6, 11, 11, 11])}})
    opp = team([player(900 + i, 25, 1, "ANY") for i in range(10)], tid=2)
    a = _analyze(me, opp, games={1: ALL, 2: [22], 3: []})
    assert "⚠️ Out OUT u postavi (PG) → zamijeni ga s Bench (klupa, igra danas)" in a.alerts
    assert any(s.startswith("⚠️ Idle nema utakmicu danas (SG)") for s in a.alerts)


def test_lost_week_switches_to_long_term():
    me = team([player(i, 15, 1, "ANY") for i in range(10)])
    opp = team([player(900 + i, 45, 1, "ANY") for i in range(10)], tid=2)
    a = _analyze(me, opp, games={1: ALL}, my_points=100, opp_points=400)
    assert a.mode == "dugorocno" and "izgubljen" in a.mode_reason
    assert "📉 Tjedan je izgubljen" in league_report(a)


def test_report_shape():
    stars = [player(70 + i, 40, 4, "ANY") for i in range(3)]          # no games this week, protected
    me = team([player(i, 30, 1, "ANY") for i in range(9)] + [player(50, 10, 2, "ANY", name="Xavier Drop")] + stars,
              slots={**{i: s for i, s in zip(range(9), [0, 1, 2, 3, 4, 5, 6, 11, 11])}, 50: 11})
    opp = team([player(900 + i, 20, 1, "ANY") for i in range(10)], tid=2)
    fa = player(60, 26, 3, "ANY", name="Yuri Add")
    a = _analyze(me, opp, [(fa, "FREEAGENT", None)], games={1: ALL, 2: [21], 3: [22, 23, 24, 25], 4: []},
                 my_points=0, opp_points=0, adds_used=2)
    text = league_report(a)
    lines = text.splitlines()
    assert lines[0].startswith("🏀 LEGENDI — Moj tim vs Moj tim: 0 : 0 (proj. ")
    assert lines[1] == "Danas: ti 10 | oni 10   Sutra: ti 9 | oni 10"
    assert lines[-1] == "Preuzimanja: 2/7 (protivnik 0/7)"
    assert any(l.startswith("SUTRA UJUTRO: Drop → Add (+") and "Drop gubi" not in l for l in lines)
