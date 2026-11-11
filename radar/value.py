"""Player value (now / long-term), minutes trend, and the role-change signals v1 missed (Burries, 2.11.2026)."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from .models import GameLog, Player, StatLine
from .news import NewsItem, OutInfo, starter_hit
from .schedule import ET, ProSchedule, fmt_date

# Weights (config `model.tezine_*`; a weight proposal accepted with /tezine prihvati overrides them via configure()).
W = {
    "now": (0.45, 0.35, 0.20),      # season, last 7 days, ESPN projection
    "long": (0.35, 0.30, 0.35),
    "noproj": (0.5, 0.3, 0.2),      # no projection (rookies, new players): last 7, last 15, season
    "trend_k": 0.5,                 # minutes-trend bonus = k x FP per minute x minutes gained (no projection only)
    "trend_min": 4.0,               # minutes up/down at least this much -> trend arrow
}
W_NOW, W_LONG = W["now"], W["long"]   # backwards compatible names


def configure(now=None, long=None, noproj=None, trend_k=None, trend_min=None) -> None:
    global W_NOW, W_LONG
    for k, v in (("now", now), ("long", long), ("noproj", noproj), ("trend_k", trend_k), ("trend_min", trend_min)):
        if v is not None:
            W[k] = tuple(v) if isinstance(v, (list, tuple)) else float(v)
    W_NOW, W_LONG = W["now"], W["long"]


def configure_from(cfg) -> None:
    m = cfg.model
    configure(m.tezine_sada, m.tezine_dugo, m.tezine_bez_projekcije, m.trend_bonus, m.trend_minuta_prag)


def has_proj(p: Player) -> bool:
    """ESPN's projection exists and isn't 0 (a 0 must never sink a rookie's value)."""
    return p.proj.avg is not None and p.proj.avg > 0


def _mean(xs):
    xs = list(xs)
    return sum(xs) / len(xs) if xs else None


@dataclass(frozen=True)
class Trend:
    mpg3: float | None
    mpg5: float | None
    delta: float            # mean of (last 3, last 5) minus season minutes; 0 when unknown
    arrow: str              # "↑" | "↓" | ""


def minutes_trend(p: Player, before_sp: int | None = None, threshold: float | None = None) -> Trend:
    """Minutes in the last 3 and 5 games against his season average (games before `before_sp` only)."""
    thr = W["trend_min"] if threshold is None else threshold
    games = [g for g in p.games if before_sp is None or g.sp < before_sp]
    m3 = _mean(g.min for g in games[-3:])
    m5 = _mean(g.min for g in games[-5:])
    base = p.season.mpg
    if base is None or m3 is None or len(games) < 2:
        return Trend(m3, m5, 0.0, "")
    deltas = [m - base for m in (m3, m5) if m is not None]
    d = sum(deltas) / len(deltas)
    arrow = "↑" if d >= thr else "↓" if d <= -thr else ""
    return Trend(round(m3, 1), round(m5, 1) if m5 is not None else None, round(d, 1), arrow)


def trend_bonus(p: Player, base: float, k: float | None = None) -> float:
    """Extra FP for a player whose minutes moved: k x FP per minute x minutes gained, at most +-25 % of `base`."""
    k = W["trend_k"] if k is None else k
    t = minutes_trend(p)
    if not t.arrow or not base:
        return 0.0
    if p.season.avg and p.season.mpg:
        fppm = p.season.avg / p.season.mpg
    else:
        fppm = base / max(t.mpg5 or t.mpg3 or 1.0, 1.0)
    b = k * fppm * t.delta
    cap = 0.25 * abs(base)
    return max(-cap, min(cap, b))


def _noproj(p: Player) -> float:
    w7, w15, ws = W["noproj"]
    parts = [(w7, p.last7.avg if p.last7.gp else None), (w15, p.last15.avg if p.last15.gp else None),
             (ws, p.season.avg if p.season.gp else None)]
    parts = [(wi, v) for wi, v in parts if v is not None]
    tw = sum(wi for wi, _ in parts)
    if not tw:
        return p.prev.avg or 0.0
    base = sum(wi * v for wi, v in parts) / tw
    return base + trend_bonus(p, base)


def _blend(p: Player, w: tuple[float, float, float]) -> float:
    if not has_proj(p):
        return _noproj(p)
    if p.season.gp < 2:
        return 0.8 * p.proj.avg
    parts = [(w[0], p.season.avg), (w[1], p.last7.avg if p.last7.gp else None), (w[2], p.proj.avg)]
    parts = [(wi, v) for wi, v in parts if v is not None]
    tw = sum(wi for wi, _ in parts)
    return sum(wi * v for wi, v in parts) / tw if tw else 0.0


def value_now(p: Player) -> float:
    return round(_blend(p, W["now"]), 2)


def value_long(p: Player) -> float:
    return round(_blend(p, W["long"]), 2)


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
    trend: Trend | None = None
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
                  hot=is_hot(sig, v_now, hot_min, hot_single), trend=minutes_trend(p))


# ---------------------------------------------------------------------------------------------------------------
def as_of(p: Player, sp: int) -> Player:
    """The player as ESPN would have shown him on the morning of scoring period `sp` (from his game log).

    Used to replay past days in tests; needs the full game log for exact season numbers."""
    games = [g for g in p.games if g.sp < sp]
    g7 = [g for g in games if g.sp >= sp - 7]
    g15 = [g for g in games if g.sp >= sp - 15]

    def line(gs: list[GameLog]) -> StatLine:
        if not gs:
            return StatLine()
        return StatLine(gp=len(gs), avg=round(_mean(g.fpts for g in gs), 2), mpg=round(_mean(g.min for g in gs), 1),
                        gs=sum(1 for g in gs if g.gs))

    return p.model_copy(update={"games": games, "season": line(games), "last7": line(g7), "last15": line(g15)})
