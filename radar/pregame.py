"""60 and 30 minutes before every game with one of my players: is someone in my lineup OUT or questionable,
does a bench player have a game while a starter has none? The message says exactly what to do and by when; free
agents are suggested only if they play that night."""
from __future__ import annotations

import datetime as dt

from .alarms import Event
from .engine import ACTIVE_SLOTS
from .espn.maps import IR, SLOT
from .report import cond_text, hhmm, reserves_text
from .scoreboard import SbGame
from .week import Analysis, if_out, lineup_alerts, days_text


def due(games: list[SbGame], offsets: list[int], now: dt.datetime, done) -> list[tuple[SbGame, int]]:
    """(game, minutes before) whose check time has passed while the game hasn't started; `done(key)` -> bool."""
    out = []
    for g in games:
        if g.state != "pre" or g.start <= now:
            continue
        for off in sorted(offsets, reverse=True):
            if g.start - dt.timedelta(minutes=off) <= now and not done(f"pg:{g.id}:{off}"):
                out.append((g, off))
    return out


def next_check(games: list[SbGame], offsets: list[int], now: dt.datetime, done) -> dt.datetime | None:
    times = [g.start - dt.timedelta(minutes=off) for g in games if g.state == "pre" and g.start > now
             for off in offsets if not done(f"pg:{g.id}:{off}")]
    future = [t for t in times if t > now]
    return min(future) if future else None


def involved(a: Analysis, games: list[SbGame]) -> bool:
    teams = set().union(*(g.teams() for g in games)) if games else set()
    return any(r.player.pro_team_id in teams for r in a.me.roster if r.slot != IR)


def checks(a: Analysis, games: list[SbGame], now: dt.datetime, tz: str = "Europe/Zagreb") -> list[Event]:
    if a.eng is None or a.today_sp not in a.remaining or not games:
        return []
    L = a.label
    teams = set().union(*(g.teams() for g in games))
    by_team = {t: g for g in games for t in g.teams()}
    my_ros = {r.player.id for r in a.me.roster if r.slot != IR}
    out: list[Event] = []
    handled = set()

    # OUT players sitting in my active lineup
    for r in a.me.roster:
        p = r.player
        if r.slot not in ACTIVE_SLOTS or not p.is_out or p.pro_team_id not in teams:
            continue
        g = by_team[p.pro_team_id]
        when = hhmm(g.start, tz)
        action, bench, add, gain, res = if_out(a, my_ros, p.id, a.today_sp)
        if action == "klupa":
            act = f"stavi {a.values[bench].player.short} s klupe u postavu (igra {days_text(a, bench, a.today_sp)})"
        elif action == "zamjena":
            act = (f"makni ga iz postave ili baci {p.short} → uzmi {a.values[add].player.short} (FA, igra "
                   f"{days_text(a, add, a.today_sp)}; {gain:+.0f} do ned.)")
            if res:
                act += f"; rezerve: {reserves_text(a, res)}"
        else:
            act = "makni ga iz postave"
        out.append(Event(f"pre:{L}:{a.today_sp}:{p.id}:OUT:{action}:{bench or add}", a.key, "utakmica",
                         f"⚠️ {L}: {p.short} ({p.team}) je OUT, a stoji u postavi ({SLOT.get(r.slot, r.slot)}); "
                         f"{g.label} u {when}. → {act}. Imaš do {when}."))
        handled.add(p.short)

    # questionable / DTD / ruled out in news
    for c in a.cond:
        if c.sp != a.today_sp:
            continue
        p = a.values[c.pid].player
        if p.pro_team_id not in teams:
            continue
        g = by_team[p.pro_team_id]
        c.start = g.start                   # scoreboard time is the truth (time changes)
        c.game = g.label
        out.append(Event(f"pre:{L}:{a.today_sp}:{c.pid}:{c.status}:{c.action}:{c.bench or c.add}", a.key,
                         "utakmica", f"{L}: " + cond_text(a, c, tz)))
        handled.add(p.short)

    # bench player plays while a starter has no game / is OUT (once a day per line)
    for line in lineup_alerts(a.eng, a.me, a.today_sp, a.values):
        if any(line.startswith(f"⚠️ {s} OUT") for s in handled):
            continue
        out.append(Event(f"pre:{L}:{a.today_sp}:{line}", a.key, "postava", f"{L}: {line}"))
    return out
