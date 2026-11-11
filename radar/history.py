"""Learning from history: every recommendation, whether I did it, and what it really gained; model accuracy per
game; the Sunday review; a weight grid search once there are 3+ weeks of data (proposed, never applied silently)."""
from __future__ import annotations

import datetime as dt
import itertools
import logging

from .models import Player
from .montecarlo import pct
from .planner import FA, WAIVER
from .value import W, has_proj, minutes_trend, value_now
from .week import Analysis

log = logging.getLogger("radar.history")


# ---- recommendations ------------------------------------------------------------------------------------------
def record(store, a: Analysis) -> int:
    n = 0
    v = a.values
    for m in a.plan:
        if m.kind not in (FA, WAIVER) or m.add is None:
            continue
        n += store.add_rec(a.key, a.period, m.kind, m.drop, v[m.drop].player.name if m.drop in v else None,
                           m.add, v[m.add].player.name if m.add in v else None, m.t, a.end_sp, m.gain)
    return n


def mark_executed(store, a: Analysis, now: dt.datetime) -> int:
    """A recommendation counts as done when I add that player AFTER it was recommended."""
    n = 0
    open_recs = store.recs(a.key, a.period, where="executed=0")
    for m in a.my_moves:
        for r in open_recs:
            if r["add_id"] not in m.adds:
                continue
            if m.date_ms is not None:
                rec_t = dt.datetime.fromisoformat(r["ts"]).timestamp()
                if m.date_ms / 1000 < rec_t - 60:
                    continue            # I had added him before the advice (e.g. dropped him again since)
            store.update_rec(r["id"], executed=1, executed_ts=now.isoformat(timespec="seconds"))
            n += 1
    return n


def _points(p: Player | None, lo: int, hi: int) -> float:
    if p is None:
        return 0.0
    return round(sum(g.fpts for g in p.games if lo <= g.sp <= hi), 1)


def evaluate(store, a: Analysis, extra_players: dict[int, Player] | None = None) -> int:
    """Actual gain of past recommendations: points of the added player minus the dropped one from the move's day
    to the end of that week (whether I made the move or not: it measures the advice)."""
    players = dict(a.lg.players if a.lg else {})
    players.update(extra_players or {})
    n = 0
    for r in store.recs(a.key, where="final=0"):
        if r["t_sp"] is None or r["t_sp"] >= a.today_sp:
            continue
        hi = min(r["end_sp"], a.today_sp - 1)
        add = players.get(r["add_id"])
        drop = players.get(r["drop_id"]) if r["drop_id"] is not None else None
        if add is None:
            continue
        pa = _points(add, r["t_sp"], hi)
        pd = _points(drop, r["t_sp"], hi)
        final = a.today_sp > r["end_sp"]
        store.update_rec(r["id"], actual_add=pa, actual_drop=pd, actual=round(pa - pd, 1), final=int(final))
        n += 1
    return n


def missing_players(store, a: Analysis) -> list[int]:
    """Players of open recommendations that this pass's pool doesn't have (for one extra ESPN call)."""
    have = set(a.lg.players) if a.lg else set()
    out = set()
    for r in store.recs(a.key, where="final=0"):
        for pid in (r["add_id"], r["drop_id"]):
            if pid is not None and pid not in have:
                out.add(pid)
    return sorted(out)


# ---- per-game observations (model accuracy, weight search) ---------------------------------------------------
def obs_rows(a: Analysis, top_fa: int = 40) -> list[dict]:
    if a.eng is None or a.today_sp not in a.remaining:
        return []
    pids = {r.player.id for t in (a.me, a.opp) for r in (t.roster if t else [])}
    fas = sorted(a.free, key=lambda f: -a.values[f.pid].long)[:top_fa]
    pids |= {f.pid for f in fas}
    rows = []
    for pid in pids:
        v = a.values.get(pid)
        if v is None or not a.schedule.open_game(v.player.pro_team_id, a.today_sp, a.now):
            continue
        p = v.player
        t = minutes_trend(p, a.today_sp)
        rows.append({"sp": a.today_sp, "pid": pid, "period": a.period, "season": p.season.avg,
                     "last7": p.last7.avg if p.last7.gp else None, "last15": p.last15.avg if p.last15.gp else None,
                     "proj": p.proj.avg if has_proj(p) else None, "mpg": p.season.mpg, "mpg3": t.mpg3,
                     "mpg5": t.mpg5, "fppm": (p.season.avg / p.season.mpg) if p.season.avg and p.season.mpg else None,
                     "value": value_now(p)})
    return rows


def fill_obs(store, players: dict[int, Player], today_sp: int) -> int:
    n = 0
    for o in store.open_obs(today_sp):
        p = players.get(o["pid"])
        if p is None:
            if today_sp - o["sp"] > 10:
                store.set_obs_actual(o["sp"], o["pid"], None, False)
            continue
        g = next((g for g in p.games if g.sp == o["sp"]), None)
        if g is not None:
            store.set_obs_actual(o["sp"], o["pid"], g.fpts, True)
            n += 1
        elif today_sp - o["sp"] >= 2:
            store.set_obs_actual(o["sp"], o["pid"], None, False)        # DNP
    return n


def accuracy(rows: list[dict]) -> tuple[float | None, int]:
    rows = [r for r in rows if r["actual"] is not None and r["value"] is not None]
    if not rows:
        return None, 0
    return round(sum(abs(r["value"] - r["actual"]) for r in rows) / len(rows), 1), len(rows)


def _trend_feature(r: dict, thr: float) -> float:
    if not r.get("mpg") or r.get("mpg3") is None:
        return 0.0
    ds = [m - r["mpg"] for m in (r.get("mpg3"), r.get("mpg5")) if m is not None]
    d = sum(ds) / len(ds)
    if abs(d) < thr:
        return 0.0
    return (r.get("fppm") or 0.0) * d


def grid_search(rows: list[dict], current: tuple[float, float, float], thr: float = 4.0, step: float = 0.05
                ) -> dict | None:
    """Weights (season, last 7, projection) + minutes-trend factor that predict the next game best (MAE)."""
    data = []
    for r in rows:
        if r.get("actual") is None or not r.get("proj") or r.get("season") is None:
            continue
        l7 = r["last7"] if r.get("last7") is not None else r["season"]
        data.append((r["season"], l7, r["proj"], _trend_feature(r, thr), r["actual"]))
    if len(data) < 100:
        return None

    def mae(ws, w7, wp, k):
        return sum(abs(ws * s + w7 * l + wp * p + k * t - y) for s, l, p, t, y in data) / len(data)

    cur = mae(*current, 0.0)
    n = round(1 / step)
    best = (cur, current, 0.0)
    for i, j in itertools.product(range(n + 1), repeat=2):
        if i + j > n:
            continue
        ws, w7 = i * step, j * step
        wp = round(1 - ws - w7, 4)
        for k in (0.0, 0.25, 0.5, 0.75, 1.0):
            m = mae(ws, w7, wp, k)
            if m < best[0] - 1e-9:
                best = (m, (round(ws, 2), round(w7, 2), round(wp, 2)), k)
    return {"mae": round(best[0], 2), "mae_cur": round(cur, 2), "sada": list(best[1]), "trend": best[2],
            "n": len(data), "better": best[0] < cur * 0.97}


def weeks_of_data(store) -> int:
    rows = store.obs("played=1 AND period IS NOT NULL")
    return len({r["period"] for r in rows})


# ---- Sunday review -----------------------------------------------------------------------------------------------
def weekly_review(store, a: Analysis) -> str:
    L = [f"📅 Tjedni pregled · {a.label} (tjedan {a.period})"]
    res = "✅" if a.my_proj > a.opp_proj else "❌"
    L.append(f"Rezultat: {a.my_live:.0f} : {a.opp_live:.0f} (proj. {a.my_proj:.0f} : {a.opp_proj:.0f} {res}"
             + (f", šansa {pct(a.win_prob)})" if a.win_prob is not None else ")"))
    L.append(f"Izmjene: {a.adds_used}/{a.adds_limit}" + (f" · protivnik {a.opp_adds_used}/{a.adds_limit}"
                                                         if a.opp else ""))
    recs = [r for r in store.recs(a.key, a.period) if r["actual"] is not None]
    done = [r for r in recs if r["executed"]]
    pool = done or recs
    if pool:
        best = max(pool, key=lambda r: r["actual"])
        worst = min(pool, key=lambda r: r["actual"])
        tag = "" if done else " (nije napravljeno)"

        def line(r):
            return (f"{r['add_name'] or r['add_id']} za {r['drop_name'] or '—'}: {r['actual']:+.0f} FP "
                    f"(očekivano {r['expected']:+.0f})")

        L.append(f"Najbolja preporuka{tag}: {line(best)}")
        if worst["id"] != best["id"]:
            L.append(f"Najgora preporuka{tag}: {line(worst)}")
        exp = sum(r["expected"] or 0 for r in done)
        act = sum(r["actual"] or 0 for r in done)
        if done:
            L.append(f"Napravljene preporuke ({len(done)}): očekivano {exp:+.0f}, stvarno {act:+.0f} FP")
    else:
        L.append("Preporuke: još nema izmjerenih (dobitak se računa nakon dana izmjene)")
    rows = store.obs("played=1 AND sp BETWEEN ? AND ?", (a.start_sp, a.end_sp))
    m, n = accuracy(rows)
    if m is not None:
        within = sum(1 for r in rows if r["actual"] is not None and abs(r["value"] - r["actual"]) <= 10)
        L.append(f"Točnost modela: prosječna greška {m:.1f} FP po utakmici ({n} utakmica, "
                 f"{round(100 * within / n)} % unutar ±10)")
    prop = store.get("tezine_prijedlog")
    if prop and prop.get("better") and not prop.get("odbijeno"):
        L.append(f"💡 Prijedlog težina (sezona/7 dana/projekcija): {prop['sada']} + trend {prop['trend']} → greška "
                 f"{prop['mae']} umjesto {prop['mae_cur']} ({prop['n']} utakmica). /tezine prihvati")
    return "\n".join(L)


def propose_weights(store, cfg) -> dict | None:
    if weeks_of_data(store) < 3:
        return None
    w = store.get("tezine") or {}
    cur = tuple(w.get("sada") or W["now"])
    res = grid_search(store.obs("played=1"), cur, cfg.model.trend_minuta_prag)
    if res:
        old = store.get("tezine_prijedlog") or {}
        if old.get("sada") == res["sada"] and old.get("odbijeno"):
            res["odbijeno"] = True
        store.set("tezine_prijedlog", res)
    return res
