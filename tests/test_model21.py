"""Model 2.1: value without a projection, minutes trend, p_play and back-to-backs, Monte Carlo."""
import datetime as dt

import pytest
from helpers import ET, morning, player, raw_schedule

from radar.availability import back_to_back, p_play
from radar.config import PPlayCfg
from radar.models import GameLog, Player, StatLine
from radar.montecarlo import player_sd, simulate
from radar.news import NewsItem, availability_flag
from radar.schedule import ProSchedule
from radar.value import has_proj, minutes_trend, value_long, value_now

STIRTZ = 5241364


# ---- value without projection -----------------------------------------------------------------------------------
def test_stirtz_without_projection_ends_high(leg11, res11):
    """Rookie Bennett Stirtz (OKC) has no ESPN projection: 0.5·7 days + 0.3·15 days + 0.2·season + minutes bonus."""
    lg = res11.states[0]
    p = lg.players[STIRTZ]
    assert not has_proj(p)
    v = leg11.values[STIRTZ]
    old = (0.45 * p.season.avg + 0.35 * p.last7.avg) / 0.8          # v2: season-heavy blend, projection dropped
    assert v.now >= 23 and v.now > old + 3
    assert v.trend.arrow == "↑"
    # he would be among the 6 most valuable free agents of the league
    better = [f for f in lg.free_agents if leg11.values[f.player.id].long > v.long]
    assert len(better) <= 5


def test_zero_projection_never_sinks_value():
    rookie = Player(id=1, name="Ro Okie", season=StatLine(gp=1, avg=28, mpg=30), last7=StatLine(gp=1, avg=28),
                    proj=StatLine(gp=0, avg=0.0))
    assert value_now(rookie) == pytest.approx(28.0)          # v2 gave 0.8 x 0 = 0
    vet = Player(id=2, name="Ve Teran", season=StatLine(gp=8, avg=20, mpg=25), last7=StatLine(gp=3, avg=26),
                 last15=StatLine(gp=6, avg=22), proj=StatLine(gp=70, avg=0.0))
    assert value_long(vet) == pytest.approx(0.5 * 26 + 0.3 * 22 + 0.2 * 20)


def _games(mins, start_sp=10, fp=1.0):
    return [GameLog(sp=start_sp + i, fpts=m * fp, min=m) for i, m in enumerate(mins)]


def test_minutes_trend_arrows_and_bonus():
    up = Player(id=3, name="Up Ward", season=StatLine(gp=10, avg=20, mpg=20), last7=StatLine(gp=3, avg=25),
                last15=StatLine(gp=6, avg=22), games=_games([16, 17, 18, 26, 27, 28]))
    t = minutes_trend(up)
    assert t.arrow == "↑" and t.delta >= 4
    base = 0.5 * 25 + 0.3 * 22 + 0.2 * 20
    assert value_now(up) > base                                # bonus only without projection
    down = up.model_copy(update={"games": _games([24, 23, 15, 14, 13, 12])})
    assert minutes_trend(down).arrow == "↓"
    flat = up.model_copy(update={"games": _games([20, 21, 19, 20, 22, 20])})
    assert minutes_trend(flat).arrow == "" and value_now(flat) == pytest.approx(base)


# ---- p_play and back-to-back --------------------------------------------------------------------------------------
@pytest.fixture()
def sched():
    # team 1 plays Mon (21) and Tue (22) -> Tuesday is a back-to-back; team 2 plays Mon, Wed
    return ProSchedule(raw_schedule({1: [21, 22, 25], 2: [21, 23]}))


def test_p_play_status_and_back_to_back(sched):
    c = PPlayCfg()
    ok = player(1, 30, 1)
    assert p_play(ok, 21, 21, sched, c) == 0.97
    assert back_to_back(sched, 1, 22) and not back_to_back(sched, 2, 23)
    assert p_play(ok, 22, 21, sched, c) == pytest.approx(0.87)              # b2b: -0.10
    old = player(2, 30, 1)
    assert p_play(old, 22, 21, sched, c, age=34) == pytest.approx(0.72)     # 33+: -0.25
    dtd = player(3, 30, 1, injury="DAY_TO_DAY")
    assert p_play(dtd, 21, 21, sched, c) == 0.6
    assert p_play(dtd, 22, 21, sched, c) == pytest.approx(0.35)             # DTD on a b2b: 0.6 - 0.25
    assert p_play(dtd, 25, 21, sched, c) == 0.85                            # DTD, game 4 days away
    out = player(4, 30, 1, injury="OUT")
    assert p_play(out, 21, 21, sched, c) == 0.0
    assert p_play(out, 25, 21, sched, c, back=dt.date(2026, 11, 10)) == 0.7  # back before Friday
    assert p_play(ok, 21, 21, sched, c, flag="upitan") == 0.5
    assert p_play(ok, 21, 21, sched, c, flag="out") == 0.0


def test_news_flags():
    now = dt.datetime(2026, 11, 11, 18, 0, tzinfo=dt.timezone.utc)

    def item(h, hours=2):
        return NewsItem(id=h[:8], player_id=1, published=now - dt.timedelta(hours=hours), headline=h)

    assert availability_flag([item("Johnson (ankle) is questionable for Wednesday's game.")], "Johnson", now)[0] \
        == "upitan"
    assert availability_flag([item("Johnson (ankle) has been ruled out for Wednesday.")], "Johnson", now)[0] == "out"
    assert availability_flag([item("Johnson (ankle) will play Wednesday."),
                              item("Johnson (ankle) is questionable for Wednesday.", 6)], "Johnson", now) is None
    assert availability_flag([item("Johnson is questionable.", hours=30)], "Johnson", now) is None   # older than 24 h


def test_engine_expected_values_use_p_play(sched):
    from radar.engine import Engine
    from radar.value import evaluate
    now = morning(21)
    ps = [player(1, 30, 1), player(2, 20, 1, injury="DAY_TO_DAY")]
    vals = {p.id: evaluate(p, [], now, sched) for p in ps}
    eng = Engine(schedule=sched, now=now, today_sp=21, days=[21, 22, 23], slot_counts={11: 3}, values=vals)
    assert eng.val(1, 21) == pytest.approx(30 * 0.97)
    assert eng.val(1, 22) == pytest.approx(30 * 0.87) and eng.b2b(1, 22)
    assert eng.val(2, 21) == pytest.approx(20 * 0.6)


# ---- Monte Carlo ---------------------------------------------------------------------------------------------------
def test_monte_carlo_is_deterministic_and_sane():
    strong = [[(40.0, 0.97, 8.0)] * 10 for _ in range(4)]
    weak = [[(25.0, 0.97, 8.0)] * 10 for _ in range(4)]
    assert simulate(strong, weak, n=2000, seed=7) > 0.99
    assert simulate(weak, strong, n=2000, seed=7) < 0.01
    even = simulate(strong, strong, n=4000, seed=7)
    assert 0.45 < even < 0.55
    assert simulate(strong, weak, 0, 0, n=1000, seed=3) == simulate(strong, weak, 0, 0, n=1000, seed=3)
    # a 200-point lead with two days left and equal teams is safe; the 10-player cap counts
    assert simulate(weak[:2], weak[:2], 1200, 1000, n=2000, seed=1) > 0.99
    twelve = [[(30.0, 1.0, 0.1)] * 12]
    ten = [[(30.0, 1.0, 0.1)] * 10]
    assert simulate(twelve, ten, n=500, seed=1) == pytest.approx(0.5, abs=0.06)


def test_player_sd():
    assert player_sd([10, 30, 20, 40], 25) == pytest.approx(11.18, abs=0.01)
    assert player_sd([20], 30, 0.3) == pytest.approx(9.0)


def test_win_probability_in_analysis(leg11):
    """Recorded Wednesday: 465 : 388 after Mon/Tue, me ahead in projection -> clear favourite."""
    assert 0.85 < leg11.win_prob < 0.99
    assert leg11.win_prob_plan >= leg11.win_prob
    assert "siguran" in leg11.prob_hint
