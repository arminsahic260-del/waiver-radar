"""The plan as steps with a condition, a time and reserves, checked against what I really did (mTransactions2).

Every pass stores the fresh plan (store key plan:<league>). Before that, my new transactions are compared with the
plan I was shown last time:
- a step I did -> ✅ (kept in `done` for /plan and the weekly review)
- I did something else (another drop, another add, a move outside the plan) -> that is the new state; the plan is
  recomputed from it and I get a short note on what it changed ("Bacio si Clayton umjesto Nesmith -> subota ti je
  sad slabija ...")
- a planned target taken by someone else -> alarms.py proposes the reserve."""
from __future__ import annotations

import datetime as dt

from .alarms import Event
from .planner import FA, WAIVER
from .report import cond_plan_line, move_line, short, waiver_when, when_label
from .schedule import DAY_NAMES
from .week import Analysis, TxRow, days_text


def step_dicts(a: Analysis, tz: str = "Europe/Zagreb") -> list[dict]:
    out = []
    for m in a.plan:
        d = {"kind": m.kind, "t": m.t, "drop": m.drop, "add": m.add, "gain": m.gain, "long_gain": m.long_gain,
             "text": move_line(a, m, tz), "reserves": list(m.reserves)}
        if m.kind in (FA, WAIVER) and m.add in a.values:
            d["name"] = a.values[m.add].player.name
            d["days"] = days_text(a, m.add, m.t)
            if m.kind == WAIVER:
                fs = a.free_status.get(m.add)
                d["when"] = f"CLAIM (obrada {waiver_when(a, fs.waiver_ms if fs else None, tz)})"
            else:
                d["when"] = when_label(a, m.t)
        else:
            d["when"] = when_label(a, m.t)
        out.append(d)
    for c in a.cond:
        out.append({"kind": "UVJET", "t": c.sp, "drop": c.pid if c.action == "zamjena" else None,
                    "add": c.add, "bench": c.bench, "if_out": c.pid, "status": c.status,
                    "deadline": c.start.isoformat() if c.start else None, "gain": c.gain,
                    "reserves": list(c.reserves), "text": cond_plan_line(a, c, tz),
                    "name": a.values[c.add].player.name if c.add in a.values else None,
                    "days": days_text(a, c.add, c.sp) if c.add in a.values else ""})
    return out


def _move_text(a: Analysis, m: TxRow) -> str:
    bits = []
    if m.drops:
        bits.append("baci " + ", ".join(short(a, x) for x in m.drops))
    if m.adds:
        bits.append("uzmi " + ", ".join(short(a, x) for x in m.adds))
    return " → ".join(bits) or "izmjena"


def impact(a: Analysis, m: TxRow, planned: dict | None) -> tuple[str, float | None]:
    """What the move did against the plan: ('subota ti je sad slabija (8 umjesto 9)', -6.0)."""
    eng = a.eng
    if eng is None or not a.remaining:
        return "", None
    from .espn.maps import IR
    actual = {r.player.id for r in a.me.roster if r.slot != IR}
    cf = (actual - set(m.adds)) | set(m.drops)
    if planned:
        if planned.get("drop") is not None:
            cf.discard(planned["drop"])
        if planned.get("add") is not None:
            cf.add(planned["add"])
    if any(p not in a.values for p in actual | cf):
        return "", None
    worse, better, delta = [], [], 0.0
    for d in a.remaining:
        la, lc = eng.opt(actual, d), eng.opt(cf, d)
        delta += la.total - lc.total
        name = DAY_NAMES[a.schedule.date(d).weekday()]
        if la.filled < lc.filled:
            worse.append(f"{name} ({la.filled} umjesto {lc.filled} igrača)")
        elif la.filled > lc.filled:
            better.append(f"{name} ({la.filled} umjesto {lc.filled})")
    parts = []
    if worse:
        parts.append(("slabiji su ti dani: " if len(worse) > 1 else "slabiji ti je dan: ") + ", ".join(worse))
    if better:
        parts.append("jači: " + ", ".join(better))
    return "; ".join(parts), round(delta, 1)


def deviation_event(a: Analysis, m: TxRow, open_steps: list[dict]) -> tuple[Event, dict | None]:
    L = a.label
    rel = next((s for s in open_steps if s.get("add") in m.adds), None) or \
        next((s for s in open_steps if s.get("drop") in m.drops), None)
    if rel and rel.get("add") in m.adds and m.drops:
        head = (f"🔄 {L}: za {short(a, rel['add'])} si bacio {', '.join(short(a, x) for x in m.drops)} "
                f"umjesto {short(a, rel.get('drop'))}")
    elif rel and rel.get("drop") in m.drops:
        head = (f"🔄 {L}: bacio si {short(a, rel['drop'])}, ali uzeo "
                f"{', '.join(short(a, x) for x in m.adds) or 'nikoga'} umjesto {short(a, rel.get('add'))}")
    else:
        head = f"🔄 {L}: izmjena izvan plana ({_move_text(a, m)})"
    what, delta = impact(a, m, rel)
    if what or delta is not None:
        head += " → " + (what or "broj igrača po danima isti")
        if delta is not None and abs(delta) >= 1:
            head += f"; do nedjelje {delta:+.0f} FP u odnosu na {'plan' if rel else 'stanje prije'}"
    first = next((x for x in a.plan if x.kind in (FA, WAIVER)), None)
    if first is not None:
        head += f". Novi plan: {move_line(a, first)}"
    elif a.remaining:
        head += ". Novi plan: nema izmjene koja se isplati"
    return Event(f"dev:{L}:{m.id}", a.key, "plan", head), rel


def reconcile(store, a: Analysis, now: dt.datetime, tz: str = "Europe/Zagreb") -> list[Event]:
    key = f"plan:{a.key}"
    prev = store.get(key)
    events: list[Event] = []
    if prev is None or prev.get("period") != a.period:
        done, seen = [], [m.id for m in a.my_moves]      # moves made before I started tracking: not news
    else:
        done = list(prev.get("done") or [])
        seen = list(prev.get("seen_tx") or [])
        open_steps = [s for s in prev.get("steps") or [] if s.get("kind") in (FA, WAIVER, "UVJET") and s.get("add")]
        for m in a.my_moves:
            if m.id in seen:
                continue
            seen.append(m.id)
            hit = next((s for s in open_steps if s["add"] in m.adds), None)
            if hit and (hit.get("drop") is None or hit["drop"] in m.drops or not m.drops):
                done.append({**hit, "status": "izvrsen", "ts": now.isoformat(), "tx": m.id})
                open_steps.remove(hit)
                continue
            ev, rel = deviation_event(a, m, open_steps)
            events.append(ev)
            done.append({"kind": "VLASTITO", "adds": m.adds, "drops": m.drops, "status": "drugacije",
                         "ts": now.isoformat(), "tx": m.id, "text": ev.text,
                         "instead": rel.get("text") if rel else None})
            if rel in open_steps:
                open_steps.remove(rel)
    store.set(key, {"period": a.period, "updated": now.isoformat(), "steps": step_dicts(a, tz), "done": done,
                    "seen_tx": seen[-200:]})
    return events


def plan_targets(store, league: str) -> dict[int, dict]:
    plan = store.get(f"plan:{league}") or {}
    return {s["add"]: s for s in plan.get("steps") or [] if s.get("add") and s.get("kind") in (FA, WAIVER, "UVJET")}


def plan_text(store, a: Analysis, tz: str = "Europe/Zagreb") -> str:
    """/plan: what was done this week, what is open (with conditions and reserves)."""
    plan = store.get(f"plan:{a.key}") or {}
    L = [f"🗒 {a.label} — plan (tjedan {a.period}, preuzimanja {a.adds_used}/{a.adds_limit})"]
    done = plan.get("done") or [] if plan.get("period") == a.period else []
    for d in done:
        if d.get("status") == "izvrsen":
            L.append(f"✅ {d.get('text', '').split(': ', 1)[-1]}")
        else:
            L.append(f"↪️ {d.get('text', 'tvoja izmjena')}")
    steps = plan.get("steps") or step_dicts(a, tz)
    if steps:
        for s in steps:
            L.append(f"⬜ {s['text']}")
    elif a.remaining:
        L.append("⬜ nema izmjene koja se isplati")
    if a.mode_reason:
        L.append(f"📉 {a.mode_reason}")
    return "\n".join(L)
