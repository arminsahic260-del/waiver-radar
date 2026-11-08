"""Week analysis for one league: score, projection to Sunday, day table, contributions, pickups, plan, alerts."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field, replace

from .config import Config, LeagueCfg
from .engine import ACTIVE_SLOTS, Engine
from .espn.maps import BENCH, IR, SLOT
from .injuries import OutEntry, is_long, teammate_signals
from .models import FreeAgent, LeagueState, Matchup, Team
from .news import NewsItem
from .planner import FA, IR_BACK, IR_IN, WAIVER, Cand, Move, Planner
from .schedule import ET, ProSchedule, day_label
from .value import Valued, evaluate


@dataclass
class TeamDay:
    starters: list[tuple[int, int]] = field(default_factory=list)   # (pid, slot) sorted by slot
    extra: list[int] = field(default_factory=list)                  # had a game, no slot left
    total: float = 0.0

    @property
    def n(self) -> int:
        return len(self.starters)


@dataclass
class DayRow:
    sp: int
    date: dt.date
    label: str
    past: bool
    my_actual: float | None = None
    opp_actual: float | None = None
    my: TeamDay | None = None
    opp: TeamDay | None = None


@dataclass
class DropRow:
    pid: int
    loss: float            # projected FP lost to Sunday if he's dropped today
    days_in: list[int]     # days he's in my best lineup
    games: int             # open games left this week
    slot: int


@dataclass
class FreeRow:
    pid: int
    kind: str              # FA | WAIVER
    avail: int             # first sp he can play for me
    waiver_ms: int | None
    gain_add: float        # FP to Sunday if added without dropping anyone (spec 4.4)
    days: list[int]        # remaining days he plays (from avail)
    best_drop: int | None = None
    best_gain: float | None = None
    best_t: int | None = None


@dataclass
class Analysis:
    key: str
    label: str
    name: str
    period: int
    start_sp: int
    end_sp: int
    today_sp: int
    now: dt.datetime
    me: Team
    opp: Team | None
    my_live: float
    opp_live: float
    my_proj: float
    opp_proj: float
    rows: list[DayRow]
    values: dict[int, Valued]
    goal: str
    mode: str
    mode_reason: str
    drops: list[DropRow]
    free: list[FreeRow]
    plan: list[Move]
    alerts: list[str]
    infos: list[str]
    adds_used: int
    adds_limit: int
    news: dict[int, list[NewsItem]]
    out_state: dict[int, OutEntry]
    schedule: ProSchedule
    free_status: dict[int, FreeAgent] = field(default_factory=dict)

    @property
    def remaining(self) -> list[int]:
        return [r.sp for r in self.rows if not r.past]

    def p(self, pid: int):
        return self.values[pid].player

    def row(self, sp: int) -> DayRow | None:
        return next((r for r in self.rows if r.sp == sp), None)


# ---------------------------------------------------------------------------------------------------------------
def _pick_period(lg: LeagueState, schedule: ProSchedule, today_sp: int) -> tuple[int, Matchup | None, int, int]:
    period = lg.matchup_period
    m = lg.matchups.get(period)
    start, end = _bounds(schedule, m, today_sp)
    if end < today_sp and (period + 1) in lg.matchups:   # ESPN hasn't rolled the week over yet (Monday night ET)
        period += 1
        m = lg.matchups[period]
        start, end = _bounds(schedule, None, today_sp)
    return period, m, start, end


def _bounds(schedule: ProSchedule, m: Matchup | None, today_sp: int) -> tuple[int, int]:
    from .schedule import week_bounds
    played = sorted(m.my_by_sp) if m and m.my_by_sp else None
    return week_bounds(schedule, 0, played, fallback_sp=today_sp)


def _team_day(eng: Engine, roster: set[int], sp: int) -> TeamDay:
    lu = eng.opt(roster, sp)
    starters = sorted(lu.starters.items(), key=lambda kv: (_slot_rank(kv[1]), -eng.val(kv[0], sp)))
    return TeamDay(starters=starters, extra=[p for p in lu.bench], total=lu.total)


def _slot_rank(s: int) -> int:
    return [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11].index(s) if s in ACTIVE_SLOTS else 99


def _active_ids(t: Team) -> set[int]:
    return {r.player.id for r in t.roster if r.slot != IR}


def _mins_ok(v: Valued, min_minutes: float) -> bool:
    p = v.player
    m = max(p.season.mpg or 0, v.mpg3 or 0)
    if p.season.gp < 2:
        m = max(m, p.proj.mpg or 0)
    return m >= min_minutes


def analyze(lg: LeagueState, lc: LeagueCfg, cfg: Config, schedule: ProSchedule, now: dt.datetime,
            news: dict[int, list[NewsItem]], out_state: dict[int, OutEntry]) -> Analysis:
    mc = cfg.model
    today = now.astimezone(ET).date()
    today_sp = schedule.sp_of(today)
    period, matchup, start, end = _pick_period(lg, schedule, today_sp)
    opp = lg.teams.get(matchup.opp_team_id) if matchup and matchup.opp_team_id else None
    me = lg.me
    remaining = [d for d in range(max(start, today_sp), end + 1)]

    # ---- values
    values: dict[int, Valued] = {}

    def val(p, extra=None):
        values[p.id] = evaluate(p, news.get(p.id), now, schedule, mc.vijesti_sati, extra=extra,
                                out=out_state[p.id].info if p.id in out_state else None,
                                hot_min=mc.vatra_min_fp, hot_single=mc.vatra_jedan_signal_fp)

    for t in (me, opp):
        for r in t.roster if t else []:
            val(r.player)
    free_status = {f.player.id: f for f in lg.free_agents}
    for f in lg.free_agents:
        val(f.player)
    long_vals = {pid: v.long for pid, v in values.items()}
    mates = teammate_signals([f.player for f in lg.free_agents], out_state, today, long_vals)
    for pid, s in mates.items():
        val(lg.players[pid] if pid in lg.players else free_status[pid].player, extra=[s])

    eng = Engine(schedule=schedule, now=now, today_sp=today_sp, days=remaining,
                 slot_counts=lg.settings.slot_counts or {0: 1, 1: 1, 2: 1, 3: 1, 4: 1, 5: 1, 6: 1, 11: 3},
                 values=values, out_state=out_state, dtd_factor=mc.dtd_faktor)

    my_ros = _active_ids(me)
    opp_ros = _active_ids(opp) if opp else set()

    # ---- day table and projection
    rows = []
    for sp in range(start, end + 1):
        past = sp < today_sp or (sp == today_sp and schedule.day_done(sp, now))
        row = DayRow(sp=sp, date=schedule.date(sp), label=day_label(schedule, sp, today_sp), past=past)
        if matchup:
            row.my_actual = matchup.my_by_sp.get(sp)
            row.opp_actual = matchup.opp_by_sp.get(sp)
        if not past:
            row.my = _team_day(eng, my_ros, sp)
            row.opp = _team_day(eng, opp_ros, sp) if opp else None
        rows.append(row)
    my_live = matchup.my_points if matchup else 0.0
    opp_live = matchup.opp_points if matchup else 0.0
    my_proj = round(my_live + sum(r.my.total for r in rows if r.my), 1)
    opp_proj = round(opp_live + sum(r.opp.total for r in rows if r.opp), 1)

    # ---- mode
    goal = lc.goal_for(period)
    mode, mode_reason = goal, ("cilj: dugoročno (config)" if goal == "dugorocno" else "")
    if goal == "tjedan" and opp and my_proj - opp_proj <= mc.prag_izgubljen:
        mode = "dugorocno"
        mode_reason = f"tjedan je izgubljen (proj. {my_proj - opp_proj:+.0f}) → dugoročni način"
    if goal == "tjedan" and not remaining:
        mode, mode_reason = "dugorocno", "tjedan je gotov → dugoročni način"

    # ---- contributions of my players
    drops = []
    ir_ids = {r.player.id for r in me.roster if r.slot == IR}
    for r in me.roster:
        pid = r.player.id
        if r.slot == IR:
            continue
        loss = sum(eng.total(my_ros, d) - eng.total(my_ros - {pid}, d) for d in remaining)
        days_in = [d for d in remaining if pid in eng.opt(my_ros, d).starters]
        drops.append(DropRow(pid=pid, loss=round(loss, 1), days_in=days_in,
                             games=sum(1 for d in remaining if eng.plays(pid, d)), slot=r.slot))
    drops.sort(key=lambda x: (x.loss, values[x.pid].long))

    # ---- free agents
    def avail_of(f: FreeAgent) -> int:
        if f.status == "WAIVERS" and f.waiver_ms:
            return max(today_sp, schedule.sp_of(dt.datetime.fromtimestamp(f.waiver_ms / 1000, ET).date()))
        return today_sp

    free = []
    for f in lg.free_agents:
        v = values[f.player.id]
        if f.player.is_out or not _mins_ok(v, mc.min_minuta):
            continue
        if v.now <= 0 and not v.hot:
            continue
        a = avail_of(f)
        days = [d for d in remaining if d >= a and eng.plays(f.player.id, d)]
        gain = sum(eng.total(my_ros | {f.player.id}, d) - eng.total(my_ros, d) for d in remaining if d >= a)
        free.append(FreeRow(pid=f.player.id, kind=f.status, avail=a, waiver_ms=f.waiver_ms, gain_add=round(gain, 1),
                            days=days))

    # ---- plan
    adds_used = int(me.adds_by_period.get(str(period), 0))
    adds_limit = cfg.preuzimanja_tjedno
    pool = sorted(free, key=lambda x: (values[x.pid].hot, x.gain_add, values[x.pid].long), reverse=True)
    pool = pool[:mc.kandidata_po_ligi] + sorted(free, key=lambda x: values[x.pid].long, reverse=True)[:15]
    seen, cands = set(), []
    for x in pool:
        if x.pid not in seen:
            seen.add(x.pid)
            cands.append(Cand(pid=x.pid, kind=FA if x.kind == "FREEAGENT" else WAIVER, avail=x.avail))
    for pid in ir_ids:
        values.setdefault(pid, evaluate(lg.players[pid], news.get(pid), now, schedule, mc.vijesti_sati))
    planner = Planner(eng=eng, base=set(my_ros), ir=set(ir_ids), cands=cands,
                      adds_left=max(0, adds_limit - adds_used), mode=mode,
                      roster_cap=sum((lg.settings.slot_counts or {}).values()) + lg.settings.bench,
                      ir_cap=lg.settings.ir, max_centers=lg.settings.max_centers,
                      undroppable={r.player.id for r in me.roster if not r.player.droppable},
                      min_gain=mc.min_dobitak, min_long_gain=mc.min_dobitak_dugorocno, protect=mc.zastita_fp,
                      reserve=mc.rezerva_preuzimanja, reserve_from=end - 1)
    ir_returns, alerts, infos = [], [], []
    first = remaining[0] if remaining else today_sp
    for pid in ir_ids:
        p = lg.players.get(pid) or values[pid].player
        if not p.is_out:
            ir_returns.append((pid, first, True))
            alerts.append(f"⚠️ {p.name} je na IR-u, a više nije OUT ({_inj(p.injury)}) → ESPN blokira izmjene "
                          f"dok ga ne vratiš")
        elif pid in out_state and remaining:
            back = out_state[pid].info.min_date
            back_sp = schedule.sp_of(back)
            if back_sp <= end and out_state[pid].info.kind == "weeks":
                ir_returns.append((pid, max(first, back_sp), False))
    to_ir = None
    if len(ir_ids) < lg.settings.ir:
        outs = [r.player for r in me.roster if r.slot != IR and r.player.is_out]
        outs.sort(key=lambda p: (not (p.id in out_state and is_long(out_state[p.id], today)), -values[p.id].long))
        if outs:
            to_ir = outs[0].id
    plan = planner.plan(ir_returns=ir_returns, to_ir=to_ir) if remaining or mode == "dugorocno" else []
    infos += planner.notes

    # best single swap per free agent (for the "this week" list)
    if remaining:
        base_plan = planner.initial(list(ir_returns), to_ir)
        probe = replace(planner, adds_left=1, mode="tjedan", protect=1e9, notes=[])
        for mv, _ in probe.candidates(base_plan):
            fr = next((x for x in free if x.pid == mv.add), None)
            if fr is not None and (fr.best_gain is None or mv.gain > fr.best_gain):
                fr.best_drop, fr.best_gain, fr.best_t = mv.drop, mv.gain, mv.t

    # ---- alerts
    alerts += lineup_alerts(eng, me, today_sp, values)
    if to_ir is not None:
        p = values[to_ir].player
        e = out_state.get(to_ir)
        alerts.append(f"💡 {p.name} je OUT{' ' + e.info.label() if e else ''} → stavi ga na IR, dobiješ mjesto "
                      f"na rosteru")
    cap = planner.roster_cap
    if len(my_ros) < cap and to_ir is None:
        n = cap - len(my_ros)
        alerts.append(f"💡 Imaš {n} slobodn{'o mjesto' if n == 1 else 'a mjesta'} na rosteru")
    mc_ = lg.settings.max_centers
    n_c = sum(1 for r in me.roster if r.player.is_center)
    if mc_ is not None and n_c > mc_:
        alerts.append(f"⚠️ {n_c} centara na rosteru (limit {mc_})")
    infos += lg.roster_fixes

    return Analysis(key=lg.key, label=lc.oznaka, name=lc.naziv, period=period, start_sp=start, end_sp=end,
                    today_sp=today_sp, now=now, me=me, opp=opp, my_live=my_live, opp_live=opp_live, my_proj=my_proj,
                    opp_proj=opp_proj, rows=rows, values=values, goal=goal, mode=mode, mode_reason=mode_reason,
                    drops=drops, free=free, plan=plan, alerts=alerts, infos=infos, adds_used=adds_used,
                    adds_limit=adds_limit, news=news, out_state=out_state, schedule=schedule,
                    free_status=free_status)


def _inj(s: str) -> str:
    return {"DAY_TO_DAY": "DTD", "ACTIVE": "zdrav", "OUT": "OUT"}.get(s, s)


def lineup_alerts(eng: Engine, me: Team, today_sp: int, values: dict[int, Valued]) -> list[str]:
    """Today's ESPN lineup vs. the best one: OUT starters, benched players with a game while a starter has none."""
    if today_sp not in eng.days:
        return []
    sched, now = eng.schedule, eng.now
    out = []
    active = [r for r in me.roster if r.slot in ACTIVE_SLOTS]
    bench = [r for r in me.roster if r.slot == BENCH]
    roster = {r.player.id for r in me.roster if r.slot != IR}
    best = eng.opt(roster, today_sp)

    def locked(p):
        g = sched.game(p.pro_team_id, today_sp)
        return bool(g and g.start <= now)

    bad, worse = [], []
    for r in active:
        p = r.player
        if locked(p):
            continue
        has_game = sched.plays(p.pro_team_id, today_sp)
        if p.is_out and has_game:
            bad.append((r, "OUT u postavi"))
        elif not has_game:
            bad.append((r, "nema utakmicu danas"))
        elif p.id not in best.starters:
            worse.append(r)
    ins = [r for r in bench if r.player.id in best.starters and not locked(r.player)]
    used = set()

    def pick(slot):
        return next((b for b in ins if b.player.id not in used and slot in b.player.eligible), None)             or next((b for b in ins if b.player.id not in used), None)

    for r, why in bad:
        p = r.player
        rep = pick(r.slot)
        if rep is not None:
            used.add(rep.player.id)
            out.append(f"⚠️ {p.short} {why} ({SLOT.get(r.slot, r.slot)}) → zamijeni ga s {rep.player.short} "
                       f"(klupa, igra danas)")
        elif p.is_out:
            out.append(f"⚠️ {p.short} OUT u postavi ({SLOT.get(r.slot, r.slot)})")
    for r in worse:
        rep = pick(r.slot)
        if rep is None:
            break
        used.add(rep.player.id)
        diff = (eng.val(rep.player.id, today_sp) or 0) - (eng.val(r.player.id, today_sp) or 0)
        if diff >= 3:
            out.append(f"↕️ {rep.player.short} (klupa) umjesto {r.player.short} ({SLOT.get(r.slot, r.slot)}) danas, "
                       f"+{diff:.0f} FP")
    for b in ins:
        if b.player.id not in used:
            out.append(f"⚠️ {b.player.short} igra danas, a sjedi na klupi → stavi ga u postavu")
    return out
