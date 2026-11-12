"""Telegram commands. Pure text in, text out: `handle(text, ctx)`; the server does the I/O and answers only my
chat id. Answers come from the last pass (at most ~10 minutes old, otherwise the server refreshes first)."""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, replace
from typing import Callable
from zoneinfo import ZoneInfo

from .alarms import inj
from .availability import p_play
from .config import Config
from .injuries import opening
from .models import Player
from .montecarlo import pct
from .news import norm, parse_feed
from .planner import FA, WAIVER
from .plantrack import plan_text
from .report import cond_text, data_time, hhmm, move_line, short, waiver_when
from .schedule import DAY_SHORT, fmt_date
from .value import evaluate, minutes_trend
from .week import Analysis, days_text, lineup_alerts

HELP = """🤖 Waiver Radar — naredbe
/legendi, /banijska, /sve — rezultat, šansa, igrači po danima, za bacanje, za uzeti, plan
/danas — što napraviti danas u obje lige i do kad
/plan — plan s uvjetima: napravljeno ✅ i otvoreno ⬜
/igrac <ime> — vrijednost, minute, trend, raspored, status, vijesti, slobodan gdje
/usporedi <ime1> <ime2> — ostatak tjedna i dugoročno (više riječi: odvoji zarezom)
/pratim <ime> · /nepratim <ime> · /pratim — lista praćenja
/cilj <liga> tjedan|dugorocno — način rada do kraja ovog tjedna
/tisina 2h (ili 30m, off) — privremeno ugasi alarme
/tezine — prijedlog novih težina modela (/tezine prihvati | odbaci)
/osvjezi — odmah povuci nove podatke s ESPN-a"""


@dataclass
class Ctx:
    cfg: Config
    store: object
    res: object | None                      # build.RunResult
    now: dt.datetime
    client: object | None = None
    refresh: Callable[[], object] | None = None   # -> fresh RunResult

    @property
    def tz(self) -> str:
        return self.cfg.vremenska_zona


# ---- helpers -----------------------------------------------------------------------------------------------------
def _league_keys(cfg: Config) -> dict[str, str]:
    out = {}
    for lc in cfg.lige:
        out[lc.kljuc] = lc.kljuc
        out[norm(lc.oznaka)] = lc.kljuc
        out[norm(lc.naziv).split()[0]] = lc.kljuc
    return out


def find_players(ctx: Ctx, query: str) -> list[Player]:
    """Best matches for a (partial, accent-free) name across both leagues' pools; most owned first."""
    q = norm(query)
    if not q or ctx.res is None:
        return []
    pool: dict[int, Player] = {}
    for lg in ctx.res.states:
        for pid, p in lg.players.items():
            if pid not in pool or p.owned > pool[pid].owned:
                pool[pid] = p
    exact, last, part = [], [], []
    for p in pool.values():
        n = norm(p.name)
        if n == q:
            exact.append(p)
        elif norm(p.short) == q or n.split()[-1] == q or (len(n.split()) > 1 and n.split()[0] == q):
            last.append(p)
        elif q in n:
            part.append(p)
    for group in (exact, last, part):
        if group:
            return sorted(group, key=lambda p: -p.owned)
    return []


def _one(ctx: Ctx, query: str) -> tuple[Player | None, str]:
    hits = find_players(ctx, query)
    if not hits:
        return None, f"Ne nalazim igrača „{query}”."
    note = ""
    if len(hits) > 1:
        note = "Ima ih više: " + ", ".join(f"{p.name} ({p.team})" for p in hits[1:4])
    return hits[0], note


def _where(ctx: Ctx, pid: int) -> list[str]:
    out = []
    for lg in ctx.res.states:
        a = ctx.res.analysis(lg.key)
        label = a.label if a else lg.key
        free = next((f for f in lg.free_agents if f.player.id == pid), None)
        owner = next((t for t in lg.teams.values() if pid in t.player_ids()), None)
        if owner and owner.id == lg.me.id:
            out.append(f"{label}: tvoj igrač")
        elif owner:
            out.append(f"{label}: {owner.name}" + (" (protivnik)" if lg.opp and owner.id == lg.opp.id else ""))
        elif free:
            out.append(f"{label}: " + ("slobodan (FA)" if free.status == "FREEAGENT" else
                                       f"waiver do {waiver_when(None, free.waiver_ms, ctx.tz)}"))
        else:
            out.append(f"{label}: nije u bazi lige")
    return out


def _any_analysis(ctx: Ctx) -> Analysis | None:
    return ctx.res.analyses[0] if ctx.res and ctx.res.analyses else None


def _week_view(ctx: Ctx, p: Player) -> tuple[str, float, int]:
    """'čet, pet🔁 (p 0,97/0,87)', expected FP over the rest of the week, games."""
    a = _any_analysis(ctx)
    if a is None:
        return "—", 0.0, 0
    v = a.values.get(p.id) or evaluate(p, (a.news or {}).get(p.id), a.now, a.schedule)
    sched = a.schedule
    days, exp, n = [], 0.0, 0
    flag = a.flags.get(p.id)
    e = a.out_state.get(p.id)
    for d in a.remaining:
        if not sched.open_game(p.pro_team_id, d, a.now):
            continue
        if a.eng is not None and p.id in a.values:      # same numbers as the plan (RotoWire, teammates' boost)
            pp, fp = a.eng.p(p.id, d), a.values[p.id].now + a.eng.boost(p.id, d)
        else:
            pp = p_play(p, d, a.today_sp, sched, ctx.cfg.p_play, flag[0] if flag else None,
                        e.info.max_date if e else None, (a.eng.ages if a.eng else {}).get(p.id))
            fp = v.now
        b2b = sched.plays(p.pro_team_id, d - 1)
        lab = ("večeras" if d == a.today_sp else DAY_SHORT[sched.date(d).weekday()]) + ("🔁" if b2b else "")
        days.append(f"{lab} {round(pp * 100)}%")
        exp += fp * pp
        n += 1 if pp > 0 else 0
    return (", ".join(days) or "ne igra više ovaj tjedan"), round(exp, 1), n


def _stamp(ctx: Ctx) -> str:
    return f"🕒 {data_time(ctx.res.now, ctx.tz)}" if ctx.res else ""


# ---- commands ----------------------------------------------------------------------------------------------------
def league_summary(ctx: Ctx, a: Analysis) -> str:
    tz = ctx.tz
    v = a.values
    L = [f"🏀 {a.label} — {a.me.name} vs {a.opp.name if a.opp else '?'}: {a.my_live:.0f} : {a.opp_live:.0f} "
         f"(proj. {a.my_proj:.0f} : {a.opp_proj:.0f})"]
    if a.win_prob is not None:
        L.append(f"Šansa za pobjedu: {pct(a.win_prob)}" +
                 (f" (s planom {pct(a.win_prob_plan)})" if a.win_prob_plan is not None else ""))
    if a.prob_hint:
        L.append(f"💡 {a.prob_hint}")
    if a.mode_reason:
        L.append(f"📉 {a.mode_reason}")
    days = []
    for r in a.rows:
        if r.past or r.my is None:
            continue
        days.append(f"{DAY_SHORT[r.date.weekday()]} {r.my.n}:{r.opp.n if r.opp else 0}")
    if days:
        L.append("Igrači po danima (ti:oni): " + " · ".join(days))
    drops = [f"{v[d.pid].player.short} ({d.loss:.0f}{' · ⚠️ ' + a.hurt_mates(d.pid) if a.hurt_mates(d.pid) else ''})"
             for d in a.drops[:3]]
    if drops:
        L.append("Za bacanje (gubitak do ned.): " + ", ".join(drops))
    week = sorted([f for f in a.free if f.best_gain and f.best_gain > 0], key=lambda f: -f.best_gain)[:5]
    if week:
        L.append("Uzmi ovaj tjedan:")
        for i, f in enumerate(week, 1):
            st = "FA" if f.kind == "FREEAGENT" else f"W {waiver_when(a, f.waiver_ms, tz)}"
            note = a.hurt_note(f.pid, f.best_t or f.avail)
            L.append(f" {i}. {v[f.pid].player.short} ({st}) {f.best_gain:+.0f} za {short(a, f.best_drop)}"
                     f" — {days_text(a, f.pid, f.best_t or f.avail) or '—'}{' · 🩹 ' + note if note else ''}")
    longl = sorted(a.free, key=lambda f: (not v[f.pid].hot, -v[f.pid].long))[:5]
    if longl:
        L.append("Uzmi dugoročno:")
        for i, f in enumerate(longl, 1):
            x = v[f.pid]
            arrow = x.trend.arrow if x.trend else ""
            st = "FA" if f.kind == "FREEAGENT" else "W"
            L.append(f" {i}. {'🔥' if x.hot else ''}{x.player.short} ({x.player.team}, {st}) {x.long:.1f}{arrow}"
                     f" · {x.player.owned:.0f} %")
    if a.plan or a.cond:
        L.append("Plan:")
        L += [f" • {move_line(a, m, tz)}" for m in a.plan]
        from .report import cond_plan_line
        L += [f" • {cond_plan_line(a, c, tz)}" for c in a.cond]
    elif a.remaining:
        L.append("Plan: nema izmjene koja se isplati")
    opp = ""
    if a.opp:
        opp = f" · protivnik {a.opp_adds_used}/{a.adds_limit}"
        moves = []
        for m in a.opp_moves[-3:]:
            if m.adds:
                moves.append("+" + ", ".join(short(a, x) for x in m.adds))
            if m.drops:
                moves.append("−" + ", ".join(short(a, x) for x in m.drops))
        if moves:
            opp += f" ({' '.join(moves)})"
    L.append(f"Preuzimanja {a.adds_used}/{a.adds_limit}{opp}")
    return "\n".join(L)


def today_text(ctx: Ctx, a: Analysis) -> str:
    tz = ctx.tz
    L = [f"{a.label}:"]
    now_steps = [m for m in a.plan if m.t <= a.today_sp or m.kind == WAIVER]
    for m in now_steps:
        dl = ""
        if m.kind == WAIVER:
            fs = a.free_status.get(m.add)
            dl = f" — claim postavi prije obrade ({waiver_when(a, fs.waiver_ms if fs else None, tz)})"
        elif m.add in a.values:
            g = a.schedule.game(a.values[m.add].player.pro_team_id, a.today_sp)
            dl = f" — napravi prije {hhmm(g.start, tz)} (njegova utakmica)" if g and g.start > a.now else \
                " — napravi danas"
        L.append(f" • {move_line(a, m, tz)}{dl}")
    for c in a.cond:
        if c.sp == a.today_sp:
            L.append(f" • {cond_text(a, c, tz)}")
    for al in lineup_alerts(a.eng, a.me, a.today_sp, a.values) if a.eng else []:
        L.append(f" • {al}")
    later = [m for m in a.plan if m.t == a.today_sp + 1 and m.kind == FA]
    for m in later:
        L.append(f" • (sutra) {move_line(a, m, tz)}")
    if len(L) == 1:
        L.append(" • ništa za napraviti" + (" (preuzimanja potrošena)" if a.adds_used >= a.adds_limit else ""))
    return "\n".join(L)


def player_text(ctx: Ctx, query: str) -> str:
    p, note = _one(ctx, query)
    if p is None:
        return note
    a = _any_analysis(ctx)
    v = (a.values.get(p.id) if a else None) or (evaluate(p, None, ctx.now, a.schedule) if a else None)
    t = minutes_trend(p)
    st = inj(p.injury)
    e = a.out_state.get(p.id) if a else None
    if e:
        st += f" {e.info.label()}"
    flag = a.flags.get(p.id) if a else None
    if flag:
        st += f" · vijest: {'upitan' if flag[0] == 'upitan' else 'OUT'}"
    L = [f"👤 {p.name} ({p.team}, {p.positions}) · {st}"]
    if v:
        L.append(f"Vrijednost: sada {v.now:.1f} · dugoročno {v.long:.1f}" +
                 ("" if p.proj.avg else " (nema ESPN projekcije)") + (" 🔥" if v.hot else ""))
    hurt = opening(p, a.hurt, a.out_state) if a else None
    if v:
        sig = [s for s in v.signals if not (hurt and s.text == hurt.text)]
        if sig:
            L.append("🔥 " + replace(v, signals=sig).reason())
    if hurt:
        b = a.mate_boost(p.id) if p.id in a.values else 0.0
        L.append(f"🩹 Ozljede u momčadi: {hurt.text} → " +
                 (f"+{b:.0f} FP po utakmici dok ih nema (već uračunato)" if b >= 1 else "više minuta za njega"))
    spot = a.lineups.get(p.id) if a else None
    if spot is not None:
        L.append(f"📋 RotoWire ({'večeras' if spot.sp == a.today_sp else 'sutra'}): {spot.label()}")
    L.append(f"Minute: sezona {p.season.mpg or 0:.1f} · zadnje 3: {t.mpg3 or 0:.1f} · zadnjih 5: {t.mpg5 or 0:.1f}"
             + (f" {t.arrow} ({t.delta:+.1f})" if t.arrow else ""))
    L.append(f"FP: sezona {p.season.avg or 0:.1f} ({p.season.gp} utk.) · zadnjih 7 dana "
             f"{p.last7.avg if p.last7.gp else 0:.1f} · zadnjih 15 {p.last15.avg if p.last15.gp else 0:.1f} · "
             f"projekcija {f'{p.proj.avg:.1f}' if p.proj.avg else '—'}")
    games = p.games[-3:]
    if games and a:
        L.append("Zadnje: " + " · ".join(f"{fmt_date(a.schedule.date(g.sp))} {g.fpts:.0f} FP ({round(g.min)} min)"
                                         for g in reversed(games)))
    days, exp, n = _week_view(ctx, p)
    L.append(f"Ovaj tjedan: {days}" + (f" → ~{exp:.0f} FP" if n else ""))
    L += _where(ctx, p.id)
    news = []
    if a and a.news.get(p.id):
        news = a.news[p.id]
    elif ctx.client is not None:
        try:
            news = parse_feed(ctx.client.news(p.id), p.id)
        except Exception:
            news = []
    if news:
        L.append("Vijesti:")
        for n_ in news[:3]:
            d = n_.published.astimezone(ZoneInfo(ctx.tz))
            L.append(f" • {d.day}.{d.month}. {n_.summary_hr or n_.headline}")
    if note:
        L.append(note)
    return "\n".join(L)


def compare_text(ctx: Ctx, args: str) -> str:
    parts = [x.strip() for x in args.split(",")] if "," in args else args.split()
    if len(parts) != 2:
        return "Napiši: /usporedi Stirtz Clayton (ili s zarezom: /usporedi Walter Clayton, Bennett Stirtz)"
    ps = []
    for q in parts:
        p, note = _one(ctx, q)
        if p is None:
            return note
        ps.append(p)
    a = _any_analysis(ctx)
    L = [f"⚖️ {ps[0].name} vs {ps[1].name}"]
    rows = []
    for p in ps:
        v = (a.values.get(p.id) if a else None) or evaluate(p, None, ctx.now, a.schedule)
        days, exp, n = _week_view(ctx, p)
        t = minutes_trend(p)
        rows.append((p, v, days, exp, n, t))
    for p, v, days, exp, n, t in rows:
        L.append(f"{p.short} ({p.team}, {inj(p.injury)}): ostatak tjedna {n} utk. ~{exp:.0f} FP [{days}]; "
                 f"dugoročno {v.long:.1f}; sada {v.now:.1f}; minute z5 {t.mpg5 or 0:.0f}{t.arrow}")
    (p1, v1, _, e1, _, _), (p2, v2, _, e2, _, _) = rows
    week = p1.short if e1 > e2 else p2.short
    long_ = p1.short if v1.long > v2.long else p2.short
    L.append(f"Ovaj tjedan bolji: {week} ({abs(e1 - e2):.0f} FP) · dugoročno bolji: {long_} "
             f"({abs(v1.long - v2.long):.1f} FP po utakmici)")
    return "\n".join(L)


def watch_cmd(ctx: Ctx, args: str, add: bool) -> str:
    watch = {int(k): v for k, v in (ctx.store.get("watch") or {}).items()}
    if not args.strip():
        if not watch:
            return "Lista praćenja je prazna. /pratim <ime>"
        return "👀 Pratim: " + ", ".join(sorted(watch.values()))
    p, note = _one(ctx, args)
    if p is None:
        # allow removing by stored name even if he left the pool
        if not add:
            q = norm(args)
            for pid, name in list(watch.items()):
                if q in norm(name):
                    watch.pop(pid)
                    ctx.store.set("watch", {str(k): v for k, v in watch.items()})
                    return f"Više ne pratim: {name}"
        return note
    if add:
        watch[p.id] = p.name
        msg = f"👀 Pratim {p.name} ({p.team}): alarm kad se oslobodi, promijeni status ili ga netko uzme."
    else:
        if watch.pop(p.id, None) is None:
            return f"{p.name} nije na listi praćenja."
        msg = f"Više ne pratim: {p.name}"
    ctx.store.set("watch", {str(k): v for k, v in watch.items()})
    where = _where(ctx, p.id)
    return msg + ("\n" + " · ".join(where) if where and add else "") + (f"\n{note}" if note else "")


def goal_cmd(ctx: Ctx, args: str) -> str:
    parts = norm(args).split()
    keys = _league_keys(ctx.cfg)
    if len(parts) != 2 or parts[0] not in keys or parts[1] not in ("tjedan", "dugorocno", "dugorocni", "auto"):
        return "Napiši: /cilj legendi tjedan | /cilj banijska dugorocno | /cilj legendi auto"
    key = keys[parts[0]]
    a = ctx.res.analysis(key) if ctx.res else None
    period = a.period if a else None
    if period is None:
        return "Nemam podatke za tu ligu (ESPN?)."
    goal = "dugorocno" if parts[1].startswith("dugoroc") else parts[1]
    k = f"cilj:{key}:{period}"
    if goal == "auto":
        ctx.store.delete(k)
        msg = f"{a.label}: cilj vraćen na postavke iz configa."
    else:
        ctx.store.set(k, goal)
        msg = f"{a.label}: cilj do kraja tjedna {period} → {'dugoročno' if goal == 'dugorocno' else 'ovaj tjedan'}."
    if ctx.refresh:
        ctx.res = ctx.refresh()
        a = ctx.res.analysis(key)
        if a:
            first = next((move_line(a, m, ctx.tz) for m in a.plan), "nema izmjene koja se isplati")
            msg += f"\nNovi plan: {first}"
    return msg


def silence_cmd(ctx: Ctx, args: str) -> str:
    s = norm(args)
    if s in ("off", "0", "kraj", "stop", "ne"):
        ctx.store.delete("tisina_do")
        return "🔔 Alarmi su opet uključeni."
    m = re.fullmatch(r"(\d+(?:[.,]\d+)?)\s*(h|sat|sata|sati|m|min|minuta)?", s or "1h")
    if not m:
        return "Napiši: /tisina 2h, /tisina 30m ili /tisina off"
    n = float(m.group(1).replace(",", "."))
    minutes = n if (m.group(2) or "h").startswith("m") else n * 60
    until = ctx.now + dt.timedelta(minutes=minutes)
    ctx.store.set("tisina_do", until.isoformat())
    return f"🔕 Alarmi utišani do {hhmm(until, ctx.tz)} (izvještaji i odgovori bota rade). /tisina off za kraj."


def weights_cmd(ctx: Ctx, args: str) -> str:
    s = norm(args)
    prop = ctx.store.get("tezine_prijedlog")
    cur = ctx.store.get("tezine")
    if s.startswith("prihvat"):
        if not prop:
            return "Nema prijedloga težina."
        ctx.store.set("tezine", {"sada": prop["sada"], "trend": prop["trend"], "od": ctx.now.isoformat()})
        return f"✅ Nove težine: sezona/7 dana/projekcija {prop['sada']}, trend {prop['trend']}. Vrijede od idućeg prolaza."
    if s.startswith("odbac") or s.startswith("odbij"):
        if prop:
            prop["odbijeno"] = True
            ctx.store.set("tezine_prijedlog", prop)
        if s.endswith("sve") or "vrati" in s:
            ctx.store.delete("tezine")
        return "Prijedlog odbačen."
    if s.startswith("vrati"):
        ctx.store.delete("tezine")
        return "Težine vraćene na config.yaml."
    L = [f"Težine sada: {cur['sada'] if cur else list(ctx.cfg.model.tezine_sada)} (sezona/7 dana/projekcija)"]
    if prop:
        L.append(f"Prijedlog: {prop['sada']} + trend {prop['trend']} → greška {prop['mae']} umjesto "
                 f"{prop['mae_cur']} FP ({prop['n']} utakmica){' · odbijeno' if prop.get('odbijeno') else ''}")
        L.append("/tezine prihvati · /tezine odbaci · /tezine vrati")
    else:
        L.append("Prijedlog dolazi nakon barem 3 tjedna podataka.")
    return "\n".join(L)


# ---------------------------------------------------------------------------------------------------------------
def handle(text: str, ctx: Ctx) -> str:
    text = (text or "").strip()
    if not text.startswith("/"):
        text = "/" + text          # "legendi" works like "/legendi"
    cmd, _, args = text.partition(" ")
    cmd = norm(cmd.split("@")[0].lstrip("/"))
    if cmd in ("start", "pomoc", "help", "pomoć"):
        return HELP
    if cmd == "tisina":
        return silence_cmd(ctx, args)
    if cmd == "osvjezi" and ctx.refresh:
        ctx.res = ctx.refresh()
    if ctx.res is None or (not ctx.res.analyses and not ctx.res.errors):
        return "Još nemam podatke s ESPN-a, probaj za minutu."
    keys = _league_keys(ctx.cfg)
    errs = "".join(f"\n⚠️ {lab}: {msg}" for lab, msg in ctx.res.errors.values())
    if cmd in keys or cmd in ("sve", "all", "osvjezi"):
        want = [keys[cmd]] if cmd in keys else [lc.kljuc for lc in ctx.cfg.lige]
        parts = [league_summary(ctx, a) for a in ctx.res.analyses if a.key in want]
        bad = "".join(f"\n⚠️ {lab}: {msg}" for k, (lab, msg) in ctx.res.errors.items() if k in want)
        return "\n\n".join(parts) + bad + f"\n{_stamp(ctx)}"
    if cmd == "danas":
        today = ctx.now.astimezone(ZoneInfo(ctx.tz))
        head = f"📋 Danas ({DAY_SHORT[today.weekday()]} {today.day}.{today.month}.)"
        return "\n\n".join([head] + [today_text(ctx, a) for a in ctx.res.analyses]) + errs + f"\n{_stamp(ctx)}"
    if cmd == "plan":
        return "\n\n".join(plan_text(ctx.store, a, ctx.tz) for a in ctx.res.analyses) + errs + f"\n{_stamp(ctx)}"
    if cmd in ("igrac", "igrač"):
        return player_text(ctx, args) if args.strip() else "Napiši: /igrac Stirtz"
    if cmd == "usporedi":
        return compare_text(ctx, args)
    if cmd == "pratim":
        return watch_cmd(ctx, args, True)
    if cmd == "nepratim":
        return watch_cmd(ctx, args, False)
    if cmd == "cilj":
        return goal_cmd(ctx, args)
    if cmd == "tezine":
        return weights_cmd(ctx, args)
    return "Ne znam tu naredbu. /pomoc"
