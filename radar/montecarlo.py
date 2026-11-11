"""Chance to win the H2H week: Monte Carlo over the remaining games of both teams.

Every remaining (not yet started) game of a player: he plays with probability p_play; if he does, his points are
normal(value, sd), with sd from his recent games (else `sd_frac` x value). Per day only 10 count: the 10 with the
highest expected value among those who actually play (the manager knows who plays, not how many points they'll
score). Positions are ignored here; the deterministic projection elsewhere uses the real lineup optimizer."""
from __future__ import annotations

import random
import statistics
from typing import Iterable

from .engine import Engine

Day = list[tuple[float, float, float]]     # (mean, p_play, sd), best first


def player_sd(fpts: list[float], mean: float, sd_frac: float = 0.3) -> float:
    if len(fpts) >= 4:
        sd = statistics.pstdev(fpts)
        return max(sd, 0.12 * mean, 2.0)
    return max(sd_frac * mean, 2.0)


def day_pool(eng: Engine, roster: Iterable[int], sp: int, sd_frac: float = 0.3) -> Day:
    out = []
    for pid in roster:
        pp = eng.p(pid, sp)
        if pp <= 0:
            continue
        v = eng.values[pid]
        mean = v.now
        out.append((mean, pp, player_sd([g.fpts for g in v.player.games[-10:]], mean, sd_frac)))
    out.sort(key=lambda x: -x[0])
    return out


def _day(players: Day, rnd: random.Random, slots: int) -> float:
    tot, k = 0.0, 0
    for mean, pp, sd in players:
        if pp >= 1.0 or rnd.random() < pp:
            tot += rnd.gauss(mean, sd)
            k += 1
            if k == slots:
                break
    return tot


def simulate(my_days: list[Day], opp_days: list[Day], my_live: float = 0.0, opp_live: float = 0.0,
             slots: int = 10, n: int = 5000, seed: int | None = None) -> float:
    """P(my final total > theirs); ties count half."""
    rnd = random.Random(seed)
    wins = 0.0
    for _ in range(n):
        a, b = my_live, opp_live
        for d in my_days:
            a += _day(d, rnd, slots)
        for d in opp_days:
            b += _day(d, rnd, slots)
        if a > b:
            wins += 1
        elif a == b:
            wins += 0.5
    return round(wins / n, 4)


def win_prob(eng: Engine, my_by_day: dict[int, set[int]], opp_by_day: dict[int, set[int]], my_live: float,
             opp_live: float, slots: int = 10, n: int = 5000, seed: int | None = None, sd_frac: float = 0.3) -> float:
    days = sorted(set(my_by_day) | set(opp_by_day))
    my_days = [day_pool(eng, my_by_day.get(d, ()), d, sd_frac) for d in days]
    opp_days = [day_pool(eng, opp_by_day.get(d, ()), d, sd_frac) for d in days]
    return simulate(my_days, opp_days, my_live, opp_live, slots, n, seed)


def pct(p: float | None) -> str:
    return "?" if p is None else f"{round(p * 100):.0f} %"
