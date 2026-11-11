"""Fast alarms: compare this pass with the previous snapshot of each league and say what changed.

- a new 🔥 free agent (new starter / minutes jump)
- someone dropped a strong player (owned >= 30 % or top 40 by value_long): FA or waiver, and who to drop for him
- a player from my plan or watchlist was taken: the next best one for the same goal
- my player's status changed (ACTIVE <-> DTD/OUT), watchlist players freed / taken / status
- the opponent made a move that shifts my chance to win by more than 5 points
Every alarm has a dedupe key; the server sends each key once and groups one pass into one message."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from .espn.maps import IR
from .models import LeagueState
from .montecarlo import pct
from .schedule import DAY_SHORT
from .value import value_long
from .week import Analysis, days_text

INJ = {"ACTIVE": "zdrav", "DAY_TO_DAY": "DTD", "OUT": "OUT", "INJURY_RESERVE": "IR", "SUSPENSION": "suspendiran",
       "QUESTIONABLE": "upitan", "DOUBTFUL": "DTD", "PROBABLE": "zdrav"}


@dataclass
class Event:
    key: str
    league: str
    kind: str
    text: str


def inj(s: str) -> str:
    return INJ.get(s, s)


def waiver_text(ms: int | None, tz: str) -> str:
    if not ms:
        return ""
    t = dt.datetime.fromtimestamp(ms / 1000, ZoneInfo(tz))
    return f"{DAY_SHORT[t.weekday()]} {t:%H:%M}"


# ---------------------------------------------------------------------------------------------------------------
def snapshot(lg: LeagueState, a: Analysis | None, top_n: int = 40) -> dict:
    """Who is where: pid -> [status FA/W/T, fantasy team, injury, hot, owned %]."""
    owner: dict[int, int] = {}
    for t in lg.teams.values():
        for r in t.roster:
            owner[r.player.id] = t.id
    for pid, tid in lg.on_team.items():
        if tid:
            owner.setdefault(pid, tid)
    free = {f.player.id: f for f in lg.free_agents}
    players = {}
    for pid, p in lg.players.items():
        if pid in owner:
            st, tid = "T", owner[pid]
        elif pid in free:
            st, tid = ("W" if free[pid].status == "WAIVERS" else "FA"), 0
        else:
            continue
        hot = bool(a and pid in a.values and a.values[pid].hot and st != "T")
        players[str(pid)] = [st, tid, p.injury, int(hot), round(p.owned, 1)]
    ranked = sorted(lg.players.values(), key=value_long, reverse=True)[:top_n]
    return {
        "period": a.period if a else lg.matchup_period, "sp": a.today_sp if a else lg.sp,
        "players": players, "top": [p.id for p in ranked],
        "prob": a.win_prob if a else None,
        "opp_tx": [m.id for m in a.opp_moves] if a else [],
        "opp_id": a.opp.id if a and a.opp else None,
    }


# ---------------------------------------------------------------------------------------------------------------
def _name(a: Analysis, lg: LeagueState, pid: int) -> str:
    if pid in a.values:
        return a.values[pid].player.name
    p = lg.players.get(pid)
    return p.name if p else f"#{pid}"


def _short(a: Analysis, lg: LeagueState, pid: int) -> str:
    if pid in a.values:
        return a.values[pid].player.short
    p = lg.players.get(pid)
    return p.short if p else f"#{pid}"


def drop_for(a: Analysis, pid_in: int) -> tuple[int | None, str]:
    """Who to drop for a strong newcomer: the least valuable droppable player; a center if the newcomer is a
    center and I'm at the limit."""
    lg = a.lg
    v_in = a.values.get(pid_in)
    mine = [r.player.id for r in a.me.roster if r.slot != IR and r.player.droppable and r.player.id in a.values]
    if not mine or v_in is None:
        return None, ""
    max_c = lg.settings.max_centers if lg else None
    n_c = sum(1 for r in a.me.roster if r.player.is_center)
    pool, why = mine, ""
    if v_in.player.is_center and max_c is not None and n_c >= max_c:
        centers = [p for p in mine if a.values[p].player.is_center]
        if centers:
            pool, why = centers, f"imaš {n_c} centra, limit {max_c}"
    eng = a.eng
    drop = min(pool, key=lambda p: (a.values[p].long, eng.expected(p) if eng else 0))
    return drop, why


def week_gain(a: Analysis, drop: int | None, add: int, from_sp: int) -> float | None:
    eng = a.eng
    if eng is None or not a.remaining or add not in a.values:
        return None
    ros = {r.player.id for r in a.me.roster if r.slot != IR}
    new = (ros - {drop}) | {add} if drop is not None else ros | {add}
    return round(sum(eng.total(new, d) - eng.total(ros, d) for d in a.remaining if d >= from_sp), 1)


def avail_sp(a: Analysis, pid: int) -> int:
    f = a.free_status.get(pid)
    if f and f.status == "WAIVERS" and f.waiver_ms:
        d = dt.datetime.fromtimestamp(f.waiver_ms / 1000, dt.timezone.utc)
        from .schedule import ET
        return max(a.today_sp, a.schedule.sp_of(d.astimezone(ET).date()))
    return a.today_sp


def _status_text(a: Analysis, pid: int, tz: str) -> str:
    f = a.free_status.get(pid)
    if f and f.status == "WAIVERS":
        return f"na waiveru, claim prolazi {waiver_text(f.waiver_ms, tz)}"
    return "slobodan (FA), uzmi odmah"


def _when(a: Analysis, t: int | None) -> str:
    from .report import when_label
    return when_label(a, t) if t is not None else ""


# ---------------------------------------------------------------------------------------------------------------
def diff(prev: dict | None, cur: dict, a: Analysis, lg: LeagueState, *, watch: dict[int, str] | None = None,
         plan_targets: dict[int, dict] | None = None, team_names: dict[int, str] | None = None,
         strong_owned: float = 30, prob_delta: float = 0.05, tz: str = "Europe/Zagreb") -> list[Event]:
    if not prev or not prev.get("players"):
        return []           # first pass: just remember
    watch = watch or {}
    plan_targets = plan_targets or {}
    names = team_names or lg.team_names
    me = lg.me.id
    L = a.label
    P, C = prev["players"], cur["players"]
    top = set(prev.get("top") or []) | set(cur.get("top") or [])
    out: list[Event] = []
    dropped_strong = set()

    for spid, c in C.items():
        pid = int(spid)
        st, team, injury, hot, owned = c
        p0 = P.get(spid)
        pst, pteam, pinj, phot, powned = p0 if p0 else (None, None, None, 0, 0)

        # 1) a strong player was dropped by someone else
        if pst == "T" and pteam != me and st in ("FA", "W"):
            if max(owned, powned or 0) >= strong_owned or pid in top:
                dropped_strong.add(pid)
                out.append(_strong_drop(a, lg, pid, owned, powned, pteam, names, tz))

        # 2) a new 🔥 free agent
        if st in ("FA", "W") and hot and pid not in dropped_strong and not (pst in ("FA", "W") and phot):
            ev = _hot(a, lg, pid, st, tz)
            if ev:
                out.append(ev)

        # 3) a player from my plan / watchlist was taken
        if pst in ("FA", "W") and st == "T" and team != me:
            if pid in plan_targets:
                out.append(_taken(a, lg, pid, team, plan_targets[pid], C, names))
            elif pid in watch:
                out.append(Event(f"watch:taken:{L}:{pid}:{a.today_sp}", a.key, "pracenje",
                                 f"👀 {L}: {_name(a, lg, pid)} više nije slobodan, uzeo ga je {names.get(team, team)}"))

        # 4) watchlist: freed
        if pid in watch and pst == "T" and st in ("FA", "W") and pid not in dropped_strong:
            out.append(Event(f"watch:free:{L}:{pid}:{a.today_sp}", a.key, "pracenje",
                             f"👀 {L}: {_name(a, lg, pid)} je slobodan ({_status_text(a, pid, tz)})"))

        # 5) my player's status changed
        if p0 and team == me and pteam == me and injury != pinj:
            out.append(_my_status(a, lg, pid, pinj, injury, tz))
        # 6) watchlist status (not mine: that's 5)
        elif p0 and pid in watch and injury != pinj:
            out.append(Event(f"watch:status:{pid}:{injury}:{a.today_sp}", a.key, "pracenje",
                             f"👀 {_name(a, lg, pid)}: {inj(pinj)} → {inj(injury)}"))

    # 7) the opponent made a move that shifts my chance
    new_tx = [m for m in a.opp_moves if m.id not in set(prev.get("opp_tx") or [])]
    p_old, p_new = prev.get("prob"), cur.get("prob")
    if new_tx and prev.get("opp_id") == cur.get("opp_id") and p_old is not None and p_new is not None \
            and abs(p_new - p_old) > prob_delta:
        bits = []
        for m in new_tx:
            if m.adds:
                bits.append("uzeo " + ", ".join(_short(a, lg, x) for x in m.adds))
            if m.drops:
                bits.append("bacio " + ", ".join(_short(a, lg, x) for x in m.drops))
        opp = a.opp.name if a.opp else "protivnik"
        out.append(Event(f"opp:{L}:{new_tx[-1].id}", a.key, "protivnik",
                         f"👥 {L}: {opp} je {', '.join(bits)} → šansa za pobjedu {pct(p_old)} → {pct(p_new)} "
                         f"(njegove izmjene {a.opp_adds_used}/{a.adds_limit})"))
    return out


def _strong_drop(a, lg, pid, owned, powned, pteam, names, tz) -> Event:
    L = a.label
    p = lg.players.get(pid) or a.values[pid].player
    own = max(owned, powned or 0)
    head = (f"🚨 {L}: {names.get(pteam, 'netko')} je bacio {p.name} ({p.team}, {p.positions}, {own:.0f} % vl.) — "
            f"{_status_text(a, pid, tz)}.")
    if p.injury != "ACTIVE":
        e = a.out_state.get(pid)
        head += f" Pazi: {inj(p.injury)}{' ' + e.info.label() if e else ''}."
    drop, why = drop_for(a, pid)
    if drop is not None:
        dv, nv = a.values[drop], a.values.get(pid)
        lg_gain = (nv.long - dv.long) if nv else 0
        wk = week_gain(a, drop, pid, avail_sp(a, pid))
        bits = [f"dugoročno {lg_gain:+.1f} FP po utakmici"]
        if wk is not None:
            bits.append(f"do nedjelje {wk:+.0f}")
        head += f" Za njega baci {dv.player.short}{' (' + why + ')' if why else ''}: {', '.join(bits)}."
    return Event(f"drop:{L}:{pid}:{a.today_sp}", a.key, "bacen", head)


def _hot(a: Analysis, lg, pid, st, tz) -> Event | None:
    v = a.values.get(pid)
    if v is None:
        return None
    p = v.player
    L = a.label
    tag = "FA" if st == "FA" else f"waiver do {waiver_text(a.free_status[pid].waiver_ms, tz)}" \
        if pid in a.free_status else "waiver"
    text = f"🔥 {L}: {p.name} ({p.team}, {tag}) — {v.reason()}"
    fr = next((f for f in a.free if f.pid == pid), None)
    if fr and fr.best_gain and fr.best_gain > 0 and fr.best_drop in a.values:
        text += (f". Uzmi ga za {a.values[fr.best_drop].player.short}: {fr.best_gain:+.0f} do ned. "
                 f"({_when(a, fr.best_t).lower()})")
    elif fr is None:
        return None             # filtered out (OUT, too few minutes)
    days = days_text(a, pid, avail_sp(a, pid)) if a.eng else ""
    if days:
        text += f"; igra: {days}"
    return Event(f"hot:{L}:{pid}:{a.period}", a.key, "vatra", text)


def _taken(a: Analysis, lg, pid, team, step: dict, C: dict, names) -> Event:
    L = a.label
    who = step.get("name") or _name(a, lg, pid)
    text = f"❌ {L}: {who} je otišao ({names.get(team, team)})"
    rep = None
    for r in step.get("reserves") or []:
        c = C.get(str(r))
        if c and c[0] in ("FA", "W") and r in a.values:
            rep = r
            break
    if rep is None:      # the fresh plan may already have picked someone for the same drop
        for m in a.plan:
            if m.add is not None and m.drop == step.get("drop") and m.add != pid:
                rep = m.add
                break
    if rep is not None:
        t = step.get("t") or a.today_sp
        d_new = days_text(a, rep, max(t, avail_sp(a, rep))) if a.eng else ""
        same = d_new and d_new == step.get("days")
        st = "FA" if (C.get(str(rep)) or ["FA"])[0] == "FA" else "waiver"
        drop = step.get("drop")
        dtxt = f", baci {a.values[drop].player.short}" if drop in a.values else ""
        text += f" → uzmi {a.values[rep].player.short} ({st}{dtxt}; {'isti dani' if same else 'igra'}: {d_new or '—'})"
    else:
        text += " → nema dobre zamjene za isti cilj"
    return Event(f"taken:{L}:{pid}:{a.today_sp}", a.key, "otisao", text)


def _my_status(a: Analysis, lg, pid, old, new, tz) -> Event:
    L = a.label
    p = lg.players.get(pid) or a.values[pid].player
    text = f"🩹 {L}: {p.name} ({p.team}) {inj(old)} → {inj(new)}"
    c = next((c for c in a.cond if c.pid == pid and c.sp == a.today_sp), None)
    if c is not None:
        from .report import cond_text
        text += ". " + cond_text(a, c, tz, short=True)
    elif new == "OUT":
        g = a.schedule.game(p.pro_team_id, a.today_sp)
        r = next((r for r in a.me.roster if r.player.id == pid), None)
        if g and g.start > a.now and r and r.slot != 12 and r.slot != IR:
            text += " — u postavi je, a igra danas: makni ga iz postave"
    return Event(f"status:{L}:{pid}:{new}:{a.today_sp}", a.key, "status", text)
