"""Short Croatian report (Telegram / terminal) and the detailed terminal view."""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from .espn.maps import SLOT
from .montecarlo import pct
from .planner import FA, IR_BACK, IR_IN, WAIVER, Move
from .schedule import DAY_NAMES, DAY_SHORT, ET, fmt_date
from .week import Analysis, CondStep, days_text

MAX_HOT = 3


def _n(x: float) -> str:
    return f"{x:.0f}"


TZ = ZoneInfo("Europe/Zagreb")


def when_label(a: Analysis, t: int) -> str:
    """When to make a move that counts from scoring period `t`.

    ESPN rule in these leagues: once the first game of a day tips off, an add goes on the roster for the next day and
    a player dropped then still counts that day's game. So a move for day t can be made from the first tip-off of
    day t-1 ("NOĆAS OD 01:00"), and a move for today has to be made before today's first tip-off."""
    sched, now = a.schedule, a.now
    if t <= a.today_sp:
        tip = sched.first_tip(a.today_sp)
        return f"DANAS DO {hhmm(tip)}" if tip and tip > now else "DANAS"
    tip = sched.first_tip(t - 1)
    if tip is None:                     # no games the day before: the add counts once that day is over
        return "SUTRA UJUTRO" if t == a.today_sp + 1 else f"{DAY_NAMES[sched.date(t).weekday()].upper()} UJUTRO"
    if tip <= now:
        return "ODMAH"
    return f"{_tip_day(now, tip)} OD {hhmm(tip)}"


def _tip_day(now: dt.datetime, tip: dt.datetime) -> str:
    """'NOĆAS' / 'VEČERAS' / 'SUTRA' / 'NOĆ PET/SUB' / 'SUBOTA' for a tip-off, seen from `now` in Zagreb."""
    n, t = now.astimezone(TZ), tip.astimezone(TZ)
    days = (t.date() - n.date()).days
    night = t.hour < 6                                  # US evening games start after midnight in Zagreb
    if night and (days == 0 or (days == 1 and n.hour >= 6)):
        return "NOĆAS"
    if not night and days == 0:
        return "VEČERAS" if t.hour >= 17 else "DANAS"
    if night:
        prev = (t - dt.timedelta(days=1)).weekday()
        return f"NOĆ {DAY_SHORT[prev].upper()}/{DAY_SHORT[t.weekday()].upper()}"
    return "SUTRA" if days == 1 else DAY_NAMES[t.weekday()].upper()


def _days(a: Analysis, pid: int, from_sp: int) -> list[int]:
    from .engine import Engine  # noqa: F401  (type only)
    sched = a.schedule
    p = a.values[pid].player
    return [d for d in a.remaining if d >= from_sp and sched.plays(p.pro_team_id, d)
            and (d > a.today_sp or sched.open_game(p.pro_team_id, d, a.now))]


def _dshort(a: Analysis, sps: list[int], pid: int | None = None) -> str:
    """'uto, sri🔁, pet' (🔁 = back-to-back for player `pid`)."""
    out = []
    for d in sps:
        lab = DAY_SHORT[a.schedule.date(d).weekday()]
        if pid is not None and a.eng is not None and a.eng.b2b(pid, d):
            lab += "🔁"
        out.append(lab)
    return ", ".join(out)


def hhmm(t: dt.datetime | None, tz: str = "Europe/Zagreb") -> str:
    return t.astimezone(ZoneInfo(tz)).strftime("%H:%M") if t else "?"


def data_time(now: dt.datetime, tz: str = "Europe/Zagreb") -> str:
    return f"podaci od {now.astimezone(ZoneInfo(tz)):%H:%M}"


def short(a: Analysis, pid: int | None) -> str:
    if pid is None:
        return "—"
    return a.values[pid].player.short if pid in a.values else str(pid)


def reserves_text(a: Analysis, pids: list[int]) -> str:
    return ", ".join(short(a, x) for x in pids if x in a.values)


def cond_text(a: Analysis, c: CondStep, tz: str = "Europe/Zagreb", short_form: bool = False,
              short: bool = False) -> str:
    """'⚠️ Johnson (DAL) upitan, DAL–IND u 01:30. Ako bude OUT: baci Johnson → uzmi Clayton (FA, igra večeras,
    čet, sub, ned; +14). Imaš do 01:30.'"""
    v = a.values
    p = v[c.pid].player
    when = hhmm(c.start, tz)
    day = "" if c.sp == a.today_sp else "sutra "
    if c.action == "klupa":
        act = f"u postavu stavi {v[c.bench].player.short} (s klupe, igra {days_text(a, c.bench, c.sp) or 'tada'})"
    elif c.action == "zamjena":
        act = (f"baci {p.short} → uzmi {v[c.add].player.short} (FA, igra {days_text(a, c.add, c.sp)}; "
               f"{c.gain:+.0f} do ned.)")
        if c.reserves:
            act += f"; rezerve: {reserves_text(a, c.reserves)}"
    else:
        act = "nema dobre zamjene, ostavi ga"
    if short or short_form:
        lead = "→" if c.status == "OUT" else "Ako bude OUT:"
        return f"{lead} {act} (do {day}{when})"
    if c.status == "OUT":
        return f"⚠️ {p.short} ({p.team}) OUT prema vijesti, {c.game} {day}u {when}. → {act}. Imaš do {day}{when}."
    st = "DTD" if c.status == "DTD" else "upitan"
    return f"⚠️ {p.short} ({p.team}) {st}, {c.game} {day}u {when}. Ako bude OUT: {act}. Imaš do {day}{when}."


def cond_plan_line(a: Analysis, c: CondStep, tz: str = "Europe/Zagreb") -> str:
    """Plan step: 'AKO Johnson OUT prije DAL–IND (01:30) → baci Johnson, uzmi Clayton'."""
    v = a.values
    p = v[c.pid].player
    day = "" if c.sp == a.today_sp else "sutra "
    head = f"AKO {p.short} OUT prije {c.game} ({day}{hhmm(c.start, tz)})"
    if c.action == "klupa":
        return f"{head} → u postavu stavi {v[c.bench].player.short} (s klupe)"
    if c.action == "zamjena":
        r = f"; rezerve: {reserves_text(a, c.reserves)}" if c.reserves else ""
        return (f"{head} → baci {p.short}, uzmi {v[c.add].player.short} (FA, igra {days_text(a, c.add, c.sp)}; "
                f"{c.gain:+.0f}){r}")
    return f"{head} → nema dobre zamjene, ostavi ga"


def waiver_when(a: Analysis, ms: int | None, tz: str) -> str:
    if not ms:
        return ""
    t = dt.datetime.fromtimestamp(ms / 1000, ZoneInfo(tz))
    return f"{DAY_SHORT[t.weekday()]} {t:%H:%M}"


def move_parts(a: Analysis, m: Move, tz: str = "Europe/Zagreb") -> tuple[str, str]:
    """(when, what): ('SUTRA UJUTRO', 'Zubac → Clayton Jr. (+19; ...)')."""
    v = a.values
    name = lambda pid: v[pid].player.short if pid in v else str(pid)  # noqa: E731
    if m.kind == IR_IN:
        return when_label(a, m.t), f"{v[m.drop].player.short} na IR (OUT) → mjesto za novog igrača"
    if m.kind == IR_BACK:
        e = a.out_state.get(m.add)
        back = f" (povratak ~{fmt_date(e.info.min_date)})" if e and not m.forced else ""
        drop = f", baci {name(m.drop)}" if m.drop else ""
        return when_label(a, m.t), f"vrati {name(m.add)} s IR-a{back}{drop}"
    y = v[m.add].player
    y_days = _days(a, m.add, m.t)
    why = [f"{y.short}: {_dshort(a, y_days, m.add)}" if y_days else f"{y.short} ovaj tjedan ne igra"]
    if a.hurt_note(m.add, m.t):
        why.append(f"🩹 {a.hurt_note(m.add, m.t)}")
    if m.drop is not None:
        lost = _days(a, m.drop, m.t)
        why.append(f"{name(m.drop)} gubi {_dshort(a, lost, m.drop)}" if lost else "ne gubiš ništa")
        if a.hurt_mates(m.drop):
            why.append(f"⚠️ {a.hurt_mates(m.drop)}")
    if y.is_dtd:
        why.append("ako nije OUT")
    if a.mode == "dugorocno":
        why.append(f"dugoročno {m.long_gain:+.1f} FP/utak.")
    gain = f"{m.gain:+.0f}" if m.gain else "±0"
    swap = f"{name(m.drop)} → {y.short}" if m.drop is not None else f"uzmi {y.short} na slobodno mjesto"
    res = f" · rezerve: {reserves_text(a, m.reserves)}" if m.reserves else ""
    if m.kind == WAIVER:
        fs = a.free_status.get(m.add)
        when = waiver_when(a, fs.waiver_ms if fs else None, tz)
        return f"DANAS (claim, prolazi {when})", f"{swap} ({gain}; {'; '.join(why)}){res}"
    return when_label(a, m.t), f"{swap} ({gain}; {'; '.join(why)}){res}"


def move_line(a: Analysis, m: Move, tz: str = "Europe/Zagreb") -> str:
    when, what = move_parts(a, m, tz)
    return f"{when}: {what}"


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
    if a.win_prob is not None:
        plan = f" (s planom {pct(a.win_prob_plan)})" if a.win_prob_plan is not None else ""
        L.append(f"Šansa za pobjedu: {pct(a.win_prob)}{plan}")
    if a.prob_hint:
        L.append(f"💡 {a.prob_hint[0].upper()}{a.prob_hint[1:]}")
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
    for c in a.cond:
        L.append(cond_plan_line(a, c, tz))
    if not a.plan and a.remaining:
        L.append("Plan: nema izmjene koja se isplati" + (" (iskorištena sva preuzimanja)"
                                                         if a.adds_used >= a.adds_limit else ""))
    opp = f" (protivnik {a.opp_adds_used}/{a.adds_limit})" if a.opp else ""
    L.append(f"Preuzimanja: {a.adds_used}/{a.adds_limit}{opp}")
    return "\n".join(L)


def error_report(label: str, err: str) -> str:
    return f"🏀 {label} — ⚠️ {err}"


def full_report(analyses: list[Analysis], errors: dict[str, tuple[str, str]], dashboard_url: str = "",
                tz: str = "Europe/Zagreb", now: dt.datetime | None = None) -> str:
    parts = [league_report(a, tz) for a in analyses]
    parts += [error_report(label, err) for label, err in errors.values()]
    text = "\n\n".join(parts)
    stamp = now or (analyses[0].now if analyses else None)
    tail = []
    if stamp is not None:
        tail.append(f"🕒 {data_time(stamp, tz)}")
    if dashboard_url:
        tail.append(f"📊 {dashboard_url}")
    if tail:
        text += "\n\n" + "  ".join(tail)
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
        hurt = f"  ⚠️ {a.hurt_mates(d.pid)}" if a.hurt_mates(d.pid) else ""
        L.append(f"  {p.name:<26} {d.loss:6.1f}  u postavi {len(d.days_in)}/{d.games}  long {v[d.pid].long:5.1f}{hurt}")
    L.append("\nZA UZETI — OVAJ TJEDAN (neto s najboljim bacanjem)")
    week = sorted(a.free, key=lambda f: (not v[f.pid].hot, -(f.best_gain if f.best_gain is not None else -999)))
    for f in week[:12]:
        p = v[f.pid].player
        drop = v[f.best_drop].player.short if f.best_drop in v else "—"
        st = "W" if f.kind == "WAIVERS" else "FA"
        L.append(f"  {'🔥' if v[f.pid].hot else '  '} {p.name:<24} {p.team:<3} {st:<2} igra {_dshort(a, f.days):<20} "
                 f"neto {f.best_gain if f.best_gain is not None else 0:5.1f} (za {drop})  now {v[f.pid].now:4.1f}"
                 f"{'  🩹 ' + a.hurt_note(f.pid) if a.hurt_note(f.pid) else ''}")
    L.append("\nZA UZETI — DUGOROČNO (value_long)")
    longl = sorted(a.free, key=lambda f: (not v[f.pid].hot, -v[f.pid].long))
    for f in longl[:10]:
        p = v[f.pid].player
        x = v[f.pid]
        arrow = x.trend.arrow if x.trend else ""
        L.append(f"  {'🔥' if x.hot else '  '} {p.name:<24} {p.team:<3} long {x.long:5.1f}  min {x.mpg or 0:4.1f}/"
                 f"{x.mpg3 or 0:4.1f}{arrow or ' '} vl. {p.owned:4.1f}%{'' if p.owned_change is None else f' ({p.owned_change:+.1f})'}"
                 f"{'  ' + x.reason() if x.hot else ''}")
    if a.win_prob is not None:
        L.append(f"\nŠANSA ZA POBJEDU {pct(a.win_prob)}" + (f" · s planom {pct(a.win_prob_plan)}"
                                                           if a.win_prob_plan is not None else ""))
    if a.opp:
        L.append(f"PROTIVNIK: izmjene {a.opp_adds_used}/{a.adds_limit}" + "".join(
            f"\n  {('+' + ', '.join(short(a, x) for x in m.adds)) if m.adds else ''}"
            f"{(' −' + ', '.join(short(a, x) for x in m.drops)) if m.drops else ''}" for m in a.opp_moves))
    if a.infos:
        L.append("\nINFO: " + "; ".join(a.infos))
    return "\n".join(L)
