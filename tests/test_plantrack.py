"""Plan with conditions, tracking what I really did, pre-game checks, recommendation history.
Week = SP 21 (Mon 9.11.) .. 27 (Sun 15.11.)."""
import datetime as dt
from zoneinfo import ZoneInfo

from helpers import league_state, morning, player, raw_schedule, team

from radar import history, pregame
from radar.config import load_config
from radar.models import GameLog, Transaction
from radar.plantrack import plan_text, reconcile
from radar.report import league_report
from radar.schedule import ProSchedule
from radar.scoreboard import SbGame
from radar.store import Store
from radar.week import Extras, analyze

Z = ZoneInfo("Europe/Zagreb")
ET = ZoneInfo("America/New_York")
X, Y, CL = 50, 60, 70        # Nesmith (plan: drop), Stirtz (add), Clayton (what I really dropped)


def _league(roster, free, games, now, tx=(), adds_used=0, slots=None, opp_n=10):
    cfg = load_config()
    sched = ProSchedule(raw_schedule(games))
    me = team(roster, slots=slots or {})
    opp = team([player(900 + i, 25, 1, "ANY") for i in range(opp_n)], tid=2)
    lg = league_state(me, opp, free, adds_used=adds_used)
    return analyze(lg, cfg.lige[0], cfg, sched, now, {}, {}, Extras(week_tx=list(tx)))


def _tx(adds, drops, tid="tx1", sp=21):
    return Transaction(id=tid, team_id=1, type="FREEAGENT", status="EXECUTED", sp=sp, adds=adds, drops=drops)


GAMES = {1: [21, 23, 25, 27], 2: [24], 3: [26], 4: [22, 24, 26], 5: [22, 24, 26, 27]}
CORE = [player(100 + i, 30, 1, "ANY") for i in range(9)]
NES = player(X, 18, 2, "ANY", name="Aaron Nesmith")
STI = player(Y, 24, 4, "ANY", name="Bennett Stirtz")
CLA = player(CL, 20, 3, "ANY", name="Walter Clayton Jr.")


def _told(store, drop, add):
    store.set("plan:test", {"period": 4, "steps": [{"kind": "FA", "t": 21, "drop": drop, "add": add,
                                                    "text": "DANAS: Nesmith → Stirtz", "reserves": []}],
                            "done": [], "seen_tx": []})


def test_different_move_is_accepted_and_the_plan_recomputed():
    """Plan said: drop Nesmith for Stirtz. I dropped Clayton instead -> Saturday got weaker, new plan."""
    store = Store()
    _told(store, X, Y)
    a = _league(CORE + [NES, STI], [(CLA, "FREEAGENT", None)], GAMES, morning(21), tx=[_tx([Y], [CL])])
    a.key = "test"
    ev = reconcile(store, a, morning(21))
    assert len(ev) == 1 and ev[0].kind == "plan"
    t = ev[0].text
    assert t.startswith("🔄 LEGENDI: za Stirtz si bacio Clayton Jr. umjesto Nesmith → slabiji ti je dan: "
                        "subota (1 umjesto 2 igrača)")
    assert "do nedjelje −" in t.replace("-", "−") and "Novi plan:" in t
    saved = store.get("plan:test")
    assert saved["done"][0]["status"] == "drugacije" and saved["seen_tx"] == ["tx1"]
    assert reconcile(store, a, morning(21)) == []                    # the same move is news only once
    assert "↪️ 🔄 LEGENDI: za Stirtz si bacio Clayton Jr." in plan_text(store, a)


def test_executed_step_is_ticked():
    store = Store()
    _told(store, X, Y)
    a = _league(CORE + [CLA, STI], [(NES, "FREEAGENT", None)], GAMES, morning(21), tx=[_tx([Y], [X])])
    a.key = "test"
    assert reconcile(store, a, morning(21)) == []
    saved = store.get("plan:test")
    assert saved["done"][0]["status"] == "izvrsen" and saved["done"][0]["add"] == Y
    assert plan_text(store, a).splitlines()[1].startswith("✅")


def test_conditional_step_and_pregame_check_say_what_and_until_when():
    """Johnson (DTD) in my lineup, his game 19:00 ET (01:00 Zagreb); no bench player plays tonight ->
    'Ako bude OUT: baci Johnson -> uzmi Clayton (FA, igra večeras ...)'. Free agents who don't play tonight
    are never offered."""
    now = dt.datetime(2026, 11, 10, 0, 5, tzinfo=Z)                 # Mon 9.11. 18:05 ET
    core = [player(100 + i, 30, 2, "ANY") for i in range(9)]
    jo = player(80, 20, 1, "ANY", injury="DAY_TO_DAY", name="Morez Johnson Jr.")
    cla = player(CL, 24, 3, "ANY", name="Walter Clayton Jr.")
    idle = player(90, 35, 6, "ANY", name="Not Tonight")              # better, but no game tonight
    games = {1: [21, 23], 2: [21], 3: [21, 24, 26, 27], 5: [21], 6: [22, 23]}
    slots = {p.id: s for p, s in zip(core + [jo], [0, 1, 2, 3, 4, 5, 6, 11, 11, 11])}
    a = _league(core + [jo], [(cla, "FREEAGENT", None), (idle, "FREEAGENT", None)], games, now, slots=slots)
    c = next(c for c in a.cond if c.pid == jo.id and c.sp == 21)
    assert (c.status, c.action, c.add) == ("DTD", "zamjena", CL)
    assert any(line.startswith("AKO Johnson Jr. OUT prije ?–ATL (01:00) → baci Johnson Jr., uzmi Clayton Jr.")
               for line in league_report(a).splitlines())

    start = dt.datetime(2026, 11, 9, 19, 0, tzinfo=ET)
    g = SbGame(id="g1", start=start.astimezone(dt.timezone.utc), home=1, away=5, state="pre")
    done = set()
    due = pregame.due([g], [60, 30], now, done.__contains__)
    assert [(x.id, off) for x, off in due] == [("g1", 60)]
    ev = pregame.checks(a, [g], now)
    msg = next(e.text for e in ev if e.kind == "utakmica")
    assert msg.startswith("LEGENDI: ⚠️ Johnson Jr. (ATL) DTD, CLE–ATL u 01:00. Ako bude OUT: baci Johnson Jr. → "
                          "uzmi Clayton Jr. (FA, igra večeras, čet, sub, ned🔁;")
    assert msg.endswith("Imaš do 01:00.") and "Not Tonight" not in msg
    assert pregame.next_check([g], [60, 30], now, done.__contains__) == \
        dt.datetime(2026, 11, 10, 0, 30, tzinfo=Z)


def test_bench_swap_preferred_over_add():
    now = dt.datetime(2026, 11, 10, 0, 5, tzinfo=Z)
    core = [player(100 + i, 30, 2, "ANY") for i in range(9)]
    jo = player(80, 20, 1, "ANY", injury="DAY_TO_DAY", name="Morez Johnson Jr.")
    bench = player(81, 18, 2, "ANY", name="Ben Ch")
    slots = {p.id: s for p, s in zip(core + [jo], [0, 1, 2, 3, 4, 5, 6, 11, 11, 11])}
    a = _league(core + [jo, bench], [], {1: [21], 2: [21]}, now, slots=slots)
    c = next(c for c in a.cond if c.pid == jo.id)
    assert (c.action, c.bench) == ("klupa", bench.id)


STARS = [player(200 + i, 40, 6, "ANY") for i in range(2)]          # no games this week, protected


def test_history_records_marks_and_measures():
    store = Store()
    a = _league(CORE + STARS + [NES, CLA], [(STI, "FREEAGENT", None)], GAMES, morning(21))
    a.key = "test"
    m = next(m for m in a.plan if m.add == Y)
    assert history.record(store, a) >= 1
    rec = store.recs("test")[0]
    assert (rec["add_id"], rec["executed"], rec["final"]) == (Y, 0, 0)
    # I did it on Monday; one week later Stirtz scored 3 x 25, the dropped player 1 x 10
    assert m.drop in (X, CL)
    a2 = _league(CORE + STARS + [STI, CLA if m.drop != CL else NES], [], GAMES, morning(21),
                 tx=[_tx([Y], [m.drop])])
    a2.key = "test"
    history.mark_executed(store, a2, morning(21))
    assert store.recs("test")[0]["executed"] == 1
    a3 = _league(CORE + STARS + [STI], [], GAMES, morning(28))
    a3.key = "test"
    sti = STI.model_copy(update={"games": [GameLog(sp=s, fpts=25, min=30) for s in (22, 24, 26)]})
    drp = (NES if m.drop == X else CLA).model_copy(update={"games": [GameLog(sp=24, fpts=10, min=20)]})
    history.evaluate(store, a3, {Y: sti, m.drop: drp})
    r = store.recs("test")[0]
    assert (r["actual_add"], r["actual_drop"], r["actual"], r["final"]) == (75.0, 10.0, 65.0, 1)
    assert "Najbolja preporuka: Bennett Stirtz za" in history.weekly_review(store, a3.__class__(**{
        **a3.__dict__, "period": 4}))


def test_add_before_the_advice_is_not_counted_as_done():
    """Monday I added Clayton myself, Tuesday dropped him; Wednesday's advice 'take Clayton' is not 'done'."""
    store = Store()
    a = _league(CORE + STARS + [NES, STI], [(CLA, "FREEAGENT", None)], GAMES, morning(23))
    a.key = "test"
    store.add_rec("test", a.period, "FA", X, "Aaron Nesmith", CL, "Walter Clayton Jr.", 23, 27, 10.0)
    old = _tx([CL], [X], tid="mon")
    old.date_ms = int((morning(21) - dt.timedelta(hours=1)).timestamp() * 1000)
    a2 = _league(CORE + STARS + [NES, STI], [(CLA, "FREEAGENT", None)], GAMES, morning(23), tx=[old])
    a2.key = "test"
    history.mark_executed(store, a2, morning(23))
    assert store.recs("test")[0]["executed"] == 0
