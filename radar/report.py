"""Short Croatian report (Telegram / terminal) and the detailed terminal view."""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from .espn.maps import SLOT
from .planner import FA, IR_BACK, IR_IN, WAIVER, Move
from .schedule import DAY_NAMES, DAY_SHORT, ET, fmt_date
from .week import Analysis

MAX_HOT = 3


def _n(x: float) -> str:
    return f"{x:.0f}"


def when_label(a: Analysis, t: int) -> str:
    if t <= a.today_sp:
        return "DANAS"
    if t == a.today_sp + 1:
        return "SUTRA UJUTRO"
    return f"{DAY_NAMES[a.schedule.date(t).weekday()].upper()} UJUTRO"


def _days(a: Analysis, pid: int, from_sp: int) -> list[int]:
    from .engine import Engine  # noqa: F401  (type only)
    sched = a.schedule
    p = a.values[pid].player
    return [d for d in a.remaining if d >= from_sp and sched.plays(p.pro_team_id, d)
            and (d > a.today_sp or sched.open_game(p.pro_team_id, d, a.now))]


def _dshort(a: Analysis, sps: list[int]) -> str:
    return ", ".join(DAY_SHORT[a.schedule.date(d).weekday()] for d in sps)


def waiver_when(a: Analysis, ms: int | None, tz: str) -> str:
    if not ms:
        return ""
    t = dt.datetime.fromtimestamp(ms / 1000, ZoneInfo(tz))
    return f"{DAY_SHORT[t.weekday()]} {t:%H:%M}"


def move_line(a: Analysis, m: Move, tz: str = "Europe/Zagreb") -> str:
    v = a.values
    name = lambda pid: v[pid].player.short if pid in v else str(pid)  # noqa: E731
    if m.kind == IR_IN:
        return f"{when_label(a, m.t)}: {v[m.drop].player.short} na IR (OUT) → mjesto za novog igrača"
    if m.kind == IR_BACK:
        e = a.out_state.get(m.add)
        back = f" (povratak ~{fmt_date(e.info.min_date)})" if e and not m.forced else ""
        drop = f", baci {name(m.drop)}" if m.drop else ""
        return f"{when_label(a, m.t)}: vrati {name(m.add)} s IR-a{back}{drop}"
    y = v[m.add].player
    y_days = _days(a, m.add, m.t)
    why = [f"{y.short}: {_dshort(a, y_days)}" if y_days else f"{y.short} ovaj tjedan ne igra"]
    if m.drop is not None:
        lost = _days(a, m.drop, m.t)
        why.append(f"{name(m.drop)} gubi {_dshort(a, lost)}" if lost else "ne gubiš ništa")
    if y.is_dtd:
        why.append("ako nije OUT")
    if a.mode == "dugorocno":
        why.append(f"dugoročno {m.long_gain:+.1f} FP/utak.")
    gain = f"{m.gain:+.0f}" if m.gain else "±0"
    swap = f"{name(m.drop)} → {y.short}" if m.drop is not None else f"uzmi {y.short} na slobodno mjesto"
    if m.kind == WAIVER:
        fs = a.free_status.get(m.add)
        when = waiver_when(a, fs.waiver_ms if fs else None, tz)
        return f"DANAS (claim, prolazi {when}): {swap} ({gain}; {'; '.join(why)})"
    return f"{when_label(a, m.t)}: {swap} ({gain}; {'; '.join(why)})"


def hot_free(a: Analysis) -> list:
    rows = [f for f in a.free if a.values[f.pid].hot]
    rows.sort(key=lambda f: (-a.values[f.pid].now))
    return rows


def league_report(a: Analysis, tz: str = "Europe/Zagreb") -> str:
    L = []
    opp = a.opp.name if a.opp else "?"
    ok = "✅" if a.my_proj > a.opp_proj else "❌"
    L.append(f"🏀 {a.label} — {a.me.name} vs {opp}: {_n(a.my_live)} : {_n(a.opp_live)} "
             f"(proj. {_n(a.my_proj)} : {_n(a.opp_proj)} {ok})")
    parts = []
    for sp, lab in ((a.today_sp, "Danas"), (a.today_sp + 1, "Sutra")):
        r = a.row(sp)
        if r is None:
            continue
        if r.past or r.my is None:
            parts.append(f"{lab}: gotovo")
        else:
            parts.append(f"{lab}: ti {r.my.n} | oni {r.opp.n if r.opp else 0}")
    if parts:
        L.append("   ".join(parts))
    if a.mode_reason:
        L.append(f"📉 {a.mode_reason[0].upper()}{a.mode_reason[1:]}")
    L += a.alerts
    for f in hot_free(a)[:MAX_HOT]:
        v = a.values[f.pid]
        p = v.player
        tag = f", waiver do {waiver_when(a, f.waiver_ms, tz)}" if f.kind == "WAIVERS" else ""
        L.append(f"🔥 {p.name} ({p.team}{tag}) — {v.reason()}")
    for m in a.plan:
        L.append(move_line(a, m, tz))
    if not a.plan and a.remaining:
        L.append("Plan: nema izmjene koja se isplati" + (" (iskorištena sva preuzimanja)"
                                                         if a.adds_used >= a.adds_limit else ""))
    L.append(f"Preuzimanja: {a.adds_used}/{a.adds_limit}")
    return "\n".join(L)


def error_report(label: str, err: str) -> str:
    return f"🏀 {label} — ⚠️ {err}"


def full_report(analyses: list[Analysis], errors: dict[str, tuple[str, str]], dashboard_url: str = "",
                tz: str = "Europe/Zagreb") -> str:
    parts = [league_report(a, tz) for a in analyses]
    parts += [error_report(label, err) for label, err in errors.values()]
    text = "\n\n".join(parts)
    if dashboard_url:
        text += f"\n\n📊 {dashboard_url}"
    return text


# ---------------------------------------------------------------------------------------------------------------
def detail_report(a: Analysis) -> str:
    """Terminal: both rosters, the day table with names, drops, pickups."""
    v = a.values
    L = [f"══ {a.name} · tjedan {a.period} ({fmt_date(a.schedule.date(a.start_sp))}–"
         f"{fmt_date(a.schedule.date(a.end_sp))}) · način: {a.mode}"]

    def roster(team, title):
        L.append(f"\n{title}: {team.name}")
        for r in sorted(team.roster, key=lambda r: (r.slot if r.slot < 12 else 50 + r.slot, -v[r.player.id].now)):
            p = r.player
            x = v[p.id]
            inj = "" if p.injury == "ACTIVE" else f" [{p.injury}]"
            games = sum(1 for d in a.remaining if a.schedule.plays(p.pro_team_id, d))
            L.append(f"  {SLOT.get(r.slot, r.slot):<4} {p.name:<26} {p.team:<3} {p.positions:<9} "
                     f"now {x.now:5.1f}  long {x.long:5.1f}  utak. {games}{inj}{'  🔥' if x.hot else ''}")

    roster(a.me, "MOJ ROSTER")
    if a.opp:
        roster(a.opp, "PROTIVNIK")
    L.append(f"\nREZULTAT {a.my_live:.0f} : {a.opp_live:.0f} · projekcija {a.my_proj:.0f} : {a.opp_proj:.0f}")
    L.append("\nPO DANIMA (* = day-to-day)")
    for r in a.rows:
        d = f"{DAY_SHORT[r.date.weekday()]} {fmt_date(r.date)}"
        if r.past:
            mine = f"{r.my_actual:.0f}" if r.my_actual is not None else "–"
            theirs = f"{r.opp_actual:.0f}" if r.opp_actual is not None else "–"
            L.append(f"  {d:<10} odigrano: ti {mine} | oni {theirs}")
            continue

        def names(td):
            out = []
            for pid, _slot in td.starters:
                p = v[pid].player
                out.append(p.short + ("*" if p.is_dtd else ""))
            if td.extra:
                out.append("višak: " + ", ".join(v[x].player.short for x in td.extra))
            return ", ".join(out)

        L.append(f"  {d:<10} ti {r.my.n:>2} ({r.my.total:.0f}) | oni {r.opp.n if r.opp else 0:>2} "
                 f"({r.opp.total if r.opp else 0:.0f})")
        L.append(f"             ti:  {names(r.my)}")
        if r.opp:
            L.append(f"             oni: {names(r.opp)}")
    L.append("\nZA BACANJE (gubitak do nedjelje ako ga baciš danas · dani u postavi · value_long)")
    for d in a.drops:
        p = v[d.pid].player
        L.append(f"  {p.name:<26} {d.loss:6.1f}  u postavi {len(d.days_in)}/{d.games}  long {v[d.pid].long:5.1f}")
    L.append("\nZA UZETI — OVAJ TJEDAN (neto s najboljim bacanjem)")
    week = sorted(a.free, key=lambda f: (not v[f.pid].hot, -(f.best_gain if f.best_gain is not None else -999)))
    for f in week[:12]:
        p = v[f.pid].player
        drop = v[f.best_drop].player.short if f.best_drop in v else "—"
        st = "W" if f.kind == "WAIVERS" else "FA"
        L.append(f"  {'🔥' if v[f.pid].hot else '  '} {p.name:<24} {p.team:<3} {st:<2} igra {_dshort(a, f.days):<20} "
                 f"neto {f.best_gain if f.best_gain is not None else 0:5.1f} (za {drop})  now {v[f.pid].now:4.1f}")
    L.append("\nZA UZETI — DUGOROČNO (value_long)")
    longl = sorted(a.free, key=lambda f: (not v[f.pid].hot, -v[f.pid].long))
    for f in longl[:10]:
        p = v[f.pid].player
        x = v[f.pid]
        L.append(f"  {'🔥' if x.hot else '  '} {p.name:<24} {p.team:<3} long {x.long:5.1f}  min {x.mpg or 0:4.1f}/"
                 f"{x.mpg3 or 0:4.1f}  vl. {p.owned:4.1f}%{'' if p.owned_change is None else f' ({p.owned_change:+.1f})'}"
                 f"{'  ' + x.reason() if x.hot else ''}")
    if a.infos:
        L.append("\nINFO: " + "; ".join(a.infos))
    return "\n".join(L)
