"""Player value (now / long-term) and the role-change signals v1 missed (Burries, 2.11.2026)."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from .models import GameLog, Player, StatLine
from .news import NewsItem, OutInfo, starter_hit
from .schedule import ET, ProSchedule, fmt_date

W_NOW = (0.45, 0.35, 0.20)    # season, last 7 days, projection
W_LONG = (0.35, 0.30, 0.35)


def _blend(p: Player, w: tuple[float, float, float]) -> float:
    if p.season.gp < 2:
        if p.proj.avg is not None:
            return 0.8 * p.proj.avg
        return p.season.avg or 0.0
    parts = [(w[0], p.season.avg), (w[1], p.last7.avg if p.last7.gp else None), (w[2], p.proj.avg)]
    parts = [(wi, v) for wi, v in parts if v is not None]
    tw = sum(wi for wi, _ in parts)
    return sum(wi * v for wi, v in parts) / tw if tw else 0.0


def value_now(p: Player) -> float:
    return round(_blend(p, W_NOW), 2)


def value_long(p: Player) -> float:
    return round(_blend(p, W_LONG), 2)


@dataclass
class Signal:
    kind: str           # minutes | start | news | teammate
    text: str           # Croatian, for the report
    since_sp: int | None = None
    detail: str = ""    # e.g. the English news sentence


@dataclass
class Valued:
    player: Player
    now: float
    long: float
    base_now: float
    base_long: float
    mpg: float | None
    mpg3: float | None
    signals: list[Signal] = field(default_factory=list)
    out: OutInfo | None = None
    hot: bool = False           # 🔥: strong enough role change to put him on top


    @property
    def last_game(self) -> GameLog | None:
        return self.player.games[-1] if self.player.games else None

    def reason(self, schedule: ProSchedule | None = None) -> str:
        bits = []
        for s in self.signals:
            if s.text not in bits:
                bits.append(s.text)
        g = self.last_game
        if g:
            bits.append(f"zadnja: {g.line()}")
        return ", ".join(bits)


def _mean(xs):
    xs = list(xs)
    return sum(xs) / len(xs) if xs else None


def role_signals(p: Player, news: list[NewsItem], now: dt.datetime, schedule: ProSchedule,
                 news_hours: float = 72) -> list[Signal]:
    """New starter / minutes jump / fresh 'will start' news."""
    out: list[Signal] = []
    today_sp = schedule.sp_of(now.astimezone(ET).date())
    games = [g for g in p.games if g.sp < today_sp]
    recent_ok = bool(games) and today_sp - games[-1].sp <= 8
    last3 = games[-3:]

    # 1) minutes: last 3 games vs. what he played before them (season totals minus those games)
    if recent_ok and len(last3) >= 2 and p.season.gp and p.season.mpg is not None:
        prior_gp = p.season.gp - len(last3)
        mpg3 = _mean(g.min for g in last3)
        if prior_gp >= 2:
            prior = (p.season.mpg * p.season.gp - sum(g.min for g in last3)) / prior_gp
            steady = sum(1 for g in last3 if g.min >= prior + 4) >= 2
            if (mpg3 >= prior + 6 and mpg3 >= 22 and steady) or (mpg3 >= 30 and prior < 27):
                out.append(Signal("minutes", f"minute {round(prior)} → {round(mpg3)} (zadnje {len(last3)})",
                                  last3[0].sp))

    # 2) started: last game a start, and he rarely started before the current streak
    if recent_ok and games[-1].gs:
        streak = 0
        for g in reversed(games):
            if not g.gs:
                break
            streak += 1
        prior_gp = p.season.gp - streak
        prior_gs = max(0, p.season.gs - streak)
        first = games[-streak]
        if prior_gp >= 2 and prior_gs / prior_gp <= 0.25 and today_sp - first.sp <= 8:
            out.append(Signal("start", f"starter od {fmt_date(schedule.date(first.sp))}", first.sp))

    # 3) news in the last `news_hours`
    cutoff = now - dt.timedelta(hours=news_hours)
    for n in news:
        if n.published < cutoff:
            break
        hit = starter_hit(n, p.short)
        if hit:
            d = n.published.astimezone(ET).date()
            out.append(Signal("news", f"vijest {fmt_date(d)}: starter/veća uloga", None, hit))
            break
    return out


def role_value(p: Player, signals: list[Signal]) -> float | None:
    """What he scores in the new role: games since the role changed (starts/minutes), else the last 3."""
    since = min((s.since_sp for s in signals if s.since_sp), default=None)
    games = [g for g in p.games if since is None or g.sp >= since] or p.games[-3:]
    if not games:
        return None
    avg = _mean(g.fpts for g in games)
    return avg if len(games) >= 2 else (avg + value_now(p)) / 2


GROUP = {"minutes": "minutes", "start": "role", "news": "role", "teammate": "teammate"}


def is_hot(signals: list[Signal], value: float, min_value: float = 12, single_value: float = 20) -> bool:
    """A role change (started / 'will start' news) or two independent signals, worth at least `min_value`;
    minutes alone or a teammate's injury alone only for a player who already scores `single_value`."""
    groups = {GROUP.get(s.kind, s.kind) for s in signals}
    return bool(groups) and value >= min_value and ("role" in groups or len(groups) >= 2 or value >= single_value)


def evaluate(p: Player, news: list[NewsItem] | None, now: dt.datetime, schedule: ProSchedule,
             news_hours: float = 72, extra: list[Signal] | None = None, out: OutInfo | None = None,
             hot_min: float = 12, hot_single: float = 20) -> Valued:
    sig = role_signals(p, news or [], now, schedule, news_hours) + list(extra or [])
    b_now, b_long = value_now(p), value_long(p)
    v_now, v_long = b_now, b_long
    if sig:
        rv = role_value(p, sig)
        if rv is not None:
            v_now = max(b_now, rv)
            v_long = max(b_long, (rv + b_long) / 2)
    g3 = p.games[-3:]
    return Valued(player=p, now=round(v_now, 2), long=round(v_long, 2), base_now=b_now, base_long=b_long,
                  mpg=p.season.mpg, mpg3=round(_mean(g.min for g in g3), 1) if g3 else None, signals=sig, out=out,
                  hot=is_hot(sig, v_now, hot_min, hot_single))


# ---------------------------------------------------------------------------------------------------------------
def as_of(p: Player, sp: int) -> Player:
    """The player as ESPN would have shown him on the morning of scoring period `sp` (from his game log).

    Used to replay past days in tests; needs the full game log for exact season numbers."""
    games = [g for g in p.games if g.sp < sp]
    g7 = [g for g in games if g.sp >= sp - 7]

    def line(gs: list[GameLog]) -> StatLine:
        if not gs:
            return StatLine()
        return StatLine(gp=len(gs), avg=round(_mean(g.fpts for g in gs), 2), mpg=round(_mean(g.min for g in gs), 1),
                        gs=sum(1 for g in gs if g.gs))

    return p.model_copy(update={"games": games, "season": line(games), "last7": line(g7)})
