"""Analysis -> JSON for the dashboard (site/data/radar.json)."""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from .espn.maps import SLOT
from .planner import FA, IR_BACK, IR_IN, WAIVER
from .report import cond_plan_line, league_report, move_line, move_parts, short, waiver_when, when_label
from .schedule import DAY_SHORT, fmt_date
from .week import Analysis

INJ = {"ACTIVE": None, "DAY_TO_DAY": "DTD", "OUT": "OUT", "INJURY_RESERVE": "IR", "SUSPENSION": "SUSP"}


def _r(x, n=1):
    return None if x is None else round(x, n)


def _news(a: Analysis, pid: int, limit: int = 3) -> list[dict]:
    out = []
    for n in (a.news.get(pid) or [])[:limit]:
        out.append({"date": n.published.isoformat(), "headline": n.headline, "story": n.story[:600],
                    "summary": n.summary_hr})
    return out


def player(a: Analysis, pid: int, tz: str, extra: dict | None = None) -> dict:
    v = a.values[pid]
    p = v.player
    g = v.last_game
    e = a.out_state.get(pid)
    games_left = [d for d in a.remaining if a.schedule.plays(p.pro_team_id, d)]
    t = v.trend
    b2b = lambda x: a.schedule.plays(p.pro_team_id, x - 1)  # noqa: E731
    d = {
        "id": p.id, "name": p.name, "short": p.short, "team": p.team, "pos": p.positions,
        "injury": INJ.get(p.injury, p.injury), "out": e.info.label() if e else None,
        "now": _r(v.now), "long": _r(v.long), "avg": _r(p.season.avg), "last7": _r(p.last7.avg),
        "proj": _r(p.proj.avg), "gp": p.season.gp, "mpg": _r(p.season.mpg), "mpg3": _r(v.mpg3),
        "owned": _r(p.owned), "owned_change": _r(p.owned_change, 2),
        "hot": v.hot, "signals": [{"kind": s.kind, "text": s.text, "detail": s.detail} for s in v.signals],
        "last": {"line": g.line(), "fpts": _r(g.fpts), "date": fmt_date(a.schedule.date(g.sp))} if g else None,
        "days": [DAY_SHORT[a.schedule.date(x).weekday()] + ("🔁" if b2b(x) else "") for x in games_left],
        "trend": t.arrow if t else "", "trend_delta": _r(t.delta) if t else None, "mpg5": _r(t.mpg5) if t else None,
        "noproj": not (p.proj.avg and p.proj.avg > 0),
        "p_play": [_r(a.eng.p(pid, x), 2) for x in games_left] if a.eng else None,
        "flag": a.flags[pid][0] if pid in a.flags else None,
    }
    if extra:
        d.update(extra)
    return d


def _team_day(a: Analysis, td, sp: int | None = None) -> dict | None:
    if td is None:
        return None
    v = a.values
    return {"n": td.n, "total": _r(td.total, 0),
            "players": [{"id": pid, "short": v[pid].player.short, "slot": SLOT.get(slot, slot),
                         "dtd": v[pid].player.is_dtd, "b2b": bool(sp and a.eng and a.eng.b2b(pid, sp))}
                        for pid, slot in td.starters],
            "extra": [{"id": pid, "short": v[pid].player.short} for pid in td.extra]}


def league(a: Analysis, tz: str) -> dict:
    v = a.values
    sched = a.schedule
    days = []
    for r in a.rows:
        days.append({"sp": r.sp, "date": fmt_date(r.date), "dow": DAY_SHORT[r.date.weekday()], "label": r.label,
                     "past": r.past, "today": r.sp == a.today_sp, "actual_me": _r(r.my_actual, 0),
                     "actual_opp": _r(r.opp_actual, 0), "me": _team_day(a, r.my, r.sp),
                     "opp": _team_day(a, r.opp, r.sp)})

    picks = a.lg.draft if a.lg else {}

    def draft(pid, team_id):
        d = picks.get(pid)
        return {"pick": d.pick, "round": d.round, "mine": d.team_id == team_id} if d else None

    def roster(team):
        if team is None:
            return []
        rows = sorted(team.roster, key=lambda r: (r.slot if r.slot < 12 else 50 + r.slot, -v[r.player.id].now))
        return [player(a, r.player.id, tz, {"slot": SLOT.get(r.slot, r.slot), "news": _news(a, r.player.id),
                                            "acq": r.acq, "draft": draft(r.player.id, team.id),
                                            "keep": r.player.id in a.keep})
                for r in rows]

    drops = []
    for dr in a.drops:
        drops.append(player(a, dr.pid, tz, {"loss": _r(dr.loss), "in_lineup": len(dr.days_in), "games": dr.games,
                                             "slot": SLOT.get(dr.slot, dr.slot),
                                             "hurt_mates": a.hurt_mates(dr.pid) or None}))

    def free_row(f):
        fs = a.free_status.get(f.pid)
        return player(a, f.pid, tz, {
            "status": "WAIVER" if f.kind == "WAIVERS" else "FA",
            "waiver_until": waiver_when(a, f.waiver_ms, tz) if f.kind == "WAIVERS" else None,
            "gain_add": _r(f.gain_add), "gain": _r(f.best_gain),
            "drop": v[f.best_drop].player.short if f.best_drop in v else None,
            "when": when_label(a, f.best_t) if f.best_t else None,
            "days": [DAY_SHORT[sched.date(x).weekday()] + ("🔁" if a.eng and a.eng.b2b(f.pid, x) else "")
                     for x in f.days],
        })

    week = sorted(a.free, key=lambda f: (not v[f.pid].hot, -(f.best_gain if f.best_gain is not None else f.gain_add)))
    longl = sorted(a.free, key=lambda f: (not v[f.pid].hot, -v[f.pid].long))
    plan = []
    for m in a.plan:
        when, body = move_parts(a, m, tz)
        if m.kind == WAIVER:
            fs = a.free_status.get(m.add)
            when = f"CLAIM · {waiver_when(a, fs.waiver_ms if fs else None, tz)}"
        plan.append({"when": when, "kind": m.kind, "body": body,
                     "text": move_line(a, m, tz), "gain": _r(m.gain), "long_gain": _r(m.long_gain),
                     "drop": v[m.drop].player.name if m.drop in v else None,
                     "add": v[m.add].player.name if m.add in v else None,
                     "reserves": [v[x].player.name for x in m.reserves if x in v]})
    for c in a.cond:
        line = cond_plan_line(a, c, tz)
        plan.append({"when": "AKO", "kind": "UVJET", "body": line.removeprefix("AKO "), "text": line,
                     "gain": _r(c.gain), "drop": v[c.pid].player.name if c.action == "zamjena" else None,
                     "add": v[c.add].player.name if c.add in v else None,
                     "reserves": [v[x].player.name for x in c.reserves if x in v]})

    inj = []
    for side, team in (("me", a.me), ("opp", a.opp)):
        for r in team.roster if team else []:
            p = r.player
            if p.injury != "ACTIVE" or a.news.get(p.id):
                inj.append(player(a, p.id, tz, {"side": side, "slot": SLOT.get(r.slot, r.slot),
                                               "news": _news(a, p.id)}))
    inj.sort(key=lambda x: (x["side"] != "me", x["injury"] is None, x["name"]))

    return {
        "key": a.key, "label": a.label, "name": a.name, "period": a.period,
        "week": f"{fmt_date(sched.date(a.start_sp))}–{fmt_date(sched.date(a.end_sp))}",
        "goal": a.goal, "mode": a.mode, "mode_reason": a.mode_reason,
        "me": {"id": a.me.id, "name": a.me.name}, "opp": {"id": a.opp.id, "name": a.opp.name} if a.opp else None,
        "score": {"me": _r(a.my_live, 0), "opp": _r(a.opp_live, 0), "proj_me": _r(a.my_proj, 0),
                  "proj_opp": _r(a.opp_proj, 0)},
        "adds": {"used": a.adds_used, "limit": a.adds_limit, "opp_used": a.opp_adds_used},
        "win_prob": _r(a.win_prob, 3), "win_prob_plan": _r(a.win_prob_plan, 3), "prob_hint": a.prob_hint,
        "opp_moves": [{"adds": [short(a, x) for x in m.adds], "drops": [short(a, x) for x in m.drops],
                       "day": DAY_SHORT[sched.date(m.sp).weekday()] if m.sp else None} for m in a.opp_moves],
        "alerts": a.alerts, "infos": a.infos, "report": league_report(a, tz),
        "days": days, "roster_me": roster(a.me), "roster_opp": roster(a.opp), "drops": drops,
        "keep": [r.player.short for r in a.me.roster if r.player.id in a.keep],
        "pick_week": [free_row(f) for f in week[:30]], "pick_long": [free_row(f) for f in longl[:30]],
        "plan": plan, "injuries": inj,
    }


def export(res, cfg, slot: str | None, report_text: str, previous: dict | None = None, store=None,
           source: str = "actions") -> dict:
    tz = cfg.vremenska_zona
    local = res.now.astimezone(ZoneInfo(tz))
    leagues = [league(a, tz) for a in res.analyses]
    have = {L["key"] for L in leagues}
    # a league that failed this run keeps its last good snapshot, marked stale
    for old in (previous or {}).get("leagues") or []:
        if old.get("key") in res.errors and old.get("key") not in have:
            leagues.append(dict(old, stale=True, error=res.errors[old["key"]][1]))
            have.add(old["key"])
    order = {lc.kljuc: i for i, lc in enumerate(cfg.lige)}
    leagues.sort(key=lambda L: order.get(L["key"], 99))
    return {
        "generated": res.now.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "generated_local": f"{local.day}.{local.month}. {local:%H:%M}",
        "slot": slot, "report": report_text, "leagues": leagues,
        "errors": [{"key": k, "label": lab, "message": msg} for k, (lab, msg) in res.errors.items()],
        "claude": res.claude_items,
        "source": source,
        "alarms": store.recent_alerts(50) if store is not None else [],
        "history": history_rows(store, cfg) if store is not None else [],
    }


def history_rows(store, cfg, limit: int = 60) -> list[dict]:
    labels = {lc.kljuc: lc.oznaka for lc in cfg.lige}
    out = []
    for r in store.recs(limit=limit):
        out.append({"ts": r["ts"], "league": labels.get(r["league"], r["league"]), "period": r["period"],
                    "kind": r["kind"], "drop": r["drop_name"], "add": r["add_name"], "expected": _r(r["expected"]),
                    "executed": bool(r["executed"]), "actual": _r(r["actual"]), "final": bool(r["final"])})
    return out
