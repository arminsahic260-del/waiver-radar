"""Add/drop planner with timing.

A move (drop X, add Y, day t) means: Y counts from day t and X keeps his games before t. ESPN locks adds at a day's
first tip-off: a move made after that goes on the roster for the next day, and X still counts that day's game, so
a move for day t can be made from the first tip-off of day t-1. Free agents can start today until today's first
tip-off, tomorrow after it; a waiver claim counts from the day ESPN processes it (X is dropped then too). Gains are projected fantasy points to Sunday
from the real lineup optimizer, so positions, a full bench and days where nobody fits are all accounted for."""
from __future__ import annotations

from dataclasses import dataclass, field, replace

from .engine import Engine

FA, WAIVER, IR_BACK, IR_IN = "FA", "WAIVER", "IR", "IR_IN"


@dataclass(frozen=True)
class Cand:
    pid: int
    kind: str           # FA | WAIVER
    avail: int          # first scoring period he can play for me


@dataclass
class Move:
    drop: int | None
    add: int | None
    t: int
    kind: str
    gain: float = 0.0          # projected FP to Sunday vs. the plan without this move
    long_gain: float = 0.0     # value_long(add) - value_long(drop)
    forced: bool = False
    reserves: list[int] = field(default_factory=list)   # next best adds for the same drop (if the target goes)


@dataclass
class Planner:
    eng: Engine
    base: set[int]                      # my active roster (not IR)
    ir: set[int]                        # in the IR slot
    cands: list[Cand]
    adds_left: int
    mode: str = "tjedan"                # tjedan | dugorocno
    roster_cap: int = 13
    ir_cap: int = 1
    max_centers: int | None = 4
    undroppable: set[int] = field(default_factory=set)
    min_gain: float = 4.0
    min_long_gain: float = 2.5
    protect: float = 4.0
    max_long_moves: int = 3
    reserve: int = 0                    # adds kept back for injuries ...
    reserve_from: int | None = None     # ... until this scoring period (moves from then on may use them)
    notes: list[str] = field(default_factory=list)

    # ---- plan -> rosters ------------------------------------------------------------------------------------
    @staticmethod
    def roster_at(base: set[int], plan: list[Move], d: int, skip: Move | None = None) -> set[int]:
        r = set(base)
        for m in sorted(plan, key=lambda m: m.t):
            if m is skip or m.t > d:
                continue
            if m.drop is not None:
                r.discard(m.drop)
            if m.add is not None:
                r.add(m.add)
        return r

    def rosters(self, plan: list[Move]) -> dict[int, set[int]]:
        return {d: self.roster_at(self.base, plan, d) for d in self.eng.days}

    def ir_at(self, plan: list[Move], d: int) -> set[int]:
        ir = set(self.ir)
        for m in plan:
            if m.t <= d:
                if m.kind == IR_BACK:
                    ir.discard(m.add)
                elif m.kind == IR_IN:
                    ir.add(m.drop)
        return ir

    def _center(self, pid) -> bool:
        v = self.eng.values.get(pid)
        return bool(v and v.player.is_center)

    def valid(self, plan: list[Move]) -> bool:
        dropped, added = set(), set()
        for m in plan:
            if m.drop is not None:
                if m.drop in dropped or m.drop not in self.roster_at(self.base, plan, m.t, skip=m):
                    return False
                dropped.add(m.drop)
            if m.add is not None and m.kind != IR_BACK:
                if m.add in added or m.add in self.base or m.add in self.ir:
                    return False
                added.add(m.add)
        days = sorted(set(self.eng.days) | {m.t for m in plan})
        for d in days:
            r, ir = self.roster_at(self.base, plan, d), self.ir_at(plan, d)
            if len(r) > self.roster_cap or len(ir) > self.ir_cap:
                return False
            if self.max_centers is not None and sum(self._center(p) for p in r | ir) > self.max_centers:
                return False
        return True

    def total(self, plan: list[Move]) -> float:
        return self.eng.week_total(self.rosters(plan))

    def _long(self, pid) -> float:
        return self.eng.values[pid].long if pid is not None and pid in self.eng.values else 0.0

    # ---- moves we don't choose freely: IR in/out ---------------------------------------------------------------
    def repick_ir_drops(self, plan: list[Move]) -> list[Move]:
        """Each IR return needs a spot; pick the drop that leaves the plan scoring the most."""
        plan = list(plan)
        for i, m in enumerate(plan):
            if m.kind != IR_BACK:
                continue
            before = self.roster_at(self.base, plan, m.t, skip=m)
            best, best_tot = None, None
            for x in [None] + sorted(p for p in before if p not in self.undroppable and p != m.add):
                trial = plan[:i] + [replace(m, drop=x)] + plan[i + 1:]
                if not self.valid(trial):
                    continue
                tot = self.total(trial)
                if best_tot is None or tot > best_tot + 1e-9:
                    best, best_tot = trial, tot
            if best is not None:
                plan = best
        return plan

    def initial(self, ir_returns: list[tuple[int, int, bool]], to_ir: int | None) -> list[Move]:
        """IR returns ((pid, day, forced)) and an OUT player who should go to IR (frees a roster spot)."""
        plan: list[Move] = []
        first = self.eng.days[0] if self.eng.days else self.eng.today_sp
        if to_ir is not None and len(self.ir) < self.ir_cap:
            plan.append(Move(drop=to_ir, add=None, t=first, kind=IR_IN, forced=True))
        for pid, day, forced in ir_returns:
            trial = self.repick_ir_drops(plan + [Move(drop=None, add=pid, t=day, kind=IR_BACK, forced=forced)])
            if self.valid(trial):
                plan = trial
                m = trial[-1]
                m.gain = round(self.total(plan) - self.total(plan[:-1]), 1)
            else:
                name = self.eng.values[pid].player.name if pid in self.eng.values else pid
                self.notes.append(f"{name}: nema mjesta za povratak s IR-a")
        return plan

    # ---- free moves ----------------------------------------------------------------------------------------------
    def _droppable(self, plan: list[Move]) -> dict[int, int]:
        """pid -> earliest day he may be dropped."""
        out = {}
        dropped = {m.drop for m in plan if m.drop is not None}
        first = self.eng.days[0] if self.eng.days else self.eng.today_sp
        for p in self.base:
            if p not in dropped and p not in self.undroppable:
                out[p] = first
        for m in plan:
            if m.add is not None and m.add not in dropped and m.add not in self.undroppable:
                out[m.add] = m.t + 1 if m.kind != IR_BACK else m.t
        return out

    def candidates(self, plan: list[Move]) -> list[tuple[Move, list[Move]]]:
        eng, days = self.eng, self.eng.days
        added = {m.add for m in plan if m.add is not None}
        drops = self._droppable(plan)
        base_tot = self.total(plan)
        ros = self.rosters(plan)
        day_tot = {d: eng.total(ros[d], d) for d in days}
        centers = {d: sum(self._center(p) for p in ros[d] | self.ir_at(plan, d)) for d in days}
        ir_spots = {m.drop for m in plan if m.kind == IR_BACK and m.drop is not None}
        out = []
        for c in self.cands:
            if c.pid in added or c.pid in self.base or c.pid in self.ir:
                continue
            if c.kind == FA:
                ts = [d for d in days if d >= c.avail]
            else:
                ts = [c.avail] if c.avail in days or (self.mode == "dugorocno" and c.avail > eng.today_sp) else []
            if not ts:
                continue
            y_c = self._center(c.pid)
            for x, x_from in list(drops.items()) + [(None, days[0] if days else ts[0])]:
                if x is not None and self.mode == "tjedan" and self._long(x) > self._long(c.pid) + self.protect:
                    continue        # don't trade a better player for a stream
                lg = self._long(c.pid) - self._long(x)
                if self.mode == "dugorocno" and lg < self.min_long_gain:
                    continue
                ts_x = [t for t in ts if t >= x_from]
                if not ts_x:
                    continue
                if x in ir_spots:
                    # x is the spot an IR return takes later: the newcomer holds it until then, then re-pick
                    best = None
                    for t in ts_x:
                        new = Move(drop=x, add=c.pid, t=t, kind=c.kind, long_gain=round(lg, 1))
                        trial = self.repick_ir_drops(
                            [replace(m, drop=c.pid) if m.kind == IR_BACK and m.drop == x else m for m in plan] + [new])
                        if self.valid(trial):
                            new.gain = round(self.total(trial) - base_tot, 1)
                            if best is None or new.gain > best[0].gain + 1e-6:
                                best = (new, trial)
                    if best:
                        out.append(best)
                    continue
                # fast path: per-day deltas, gain(t) = sum over days >= t
                delta, ok = {}, {}
                dc = int(y_c) - int(self._center(x)) if x is not None else int(y_c)
                for d in days:
                    if d < ts_x[0]:
                        continue
                    r = ros[d]
                    fits = (x is not None or len(r) < self.roster_cap) and \
                        (self.max_centers is None or centers[d] + dc <= self.max_centers)
                    ok[d] = fits
                    new_r = (r - {x}) | {c.pid}
                    delta[d] = eng.total(new_r, d) - day_tot[d]
                best = None
                for t in ts_x:
                    if not all(ok.get(d, True) for d in days if d >= t):
                        continue
                    if t > days[-1] and x is None and len(ros[days[-1]]) >= self.roster_cap:
                        continue
                    gain = sum(v for d, v in delta.items() if d >= t)
                    if best is None or gain > best.gain + 1e-6:
                        best = Move(drop=x, add=c.pid, t=t, kind=c.kind, gain=round(gain, 1), long_gain=round(lg, 1))
                if best is not None:
                    out.append((best, plan + [best]))
        return out

    def alternatives(self, plan: list[Move], move: Move, n: int = 3) -> list[int]:
        """Reserves for `move`: the best other adds for the same drop (any day), best first."""
        if move.kind not in (FA, WAIVER):
            return []
        rest = [m for m in plan if m is not move]
        long_mode = self.mode == "dugorocno"
        best: dict[int, Move] = {}
        for mv, trial in self.candidates(rest):
            if mv.add == move.add or mv.drop != move.drop:
                continue
            score = mv.long_gain if long_mode else mv.gain
            if score <= 0 or (long_mode and mv.long_gain < self.min_long_gain):
                continue
            if not self.valid(trial):
                continue
            old = best.get(mv.add)
            if old is None or score > (old.long_gain if long_mode else old.gain):
                best[mv.add] = mv
        ranked = sorted(best.values(), key=lambda m: (m.long_gain, m.gain) if long_mode else (m.gain, m.long_gain),
                        reverse=True)
        return [m.add for m in ranked[:n]]

    def _early(self, t: int) -> bool:
        return self.reserve_from is not None and t < self.reserve_from

    def plan(self, ir_returns=(), to_ir=None) -> list[Move]:
        plan = self.initial(list(ir_returns), to_ir)
        adds, long_moves = self.adds_left, 0
        while adds > 0:
            cands = self.candidates(plan)
            if self.mode == "dugorocno":
                cands = [c for c in cands if c[0].long_gain >= self.min_long_gain]
                key = lambda c: (c[0].long_gain, c[0].gain, -c[0].t)  # noqa: E731
                if long_moves >= self.max_long_moves:
                    break
            else:
                cands = [c for c in cands if c[0].gain >= self.min_gain]
                key = lambda c: (c[0].gain, c[0].long_gain, -c[0].t)  # noqa: E731
            early = sum(1 for m in plan if m.kind in (FA, WAIVER) and self._early(m.t))
            early_cap = self.adds_left - self.reserve
            picked = None
            for move, trial in sorted(cands, key=key, reverse=True):
                if self._early(move.t) and early >= early_cap:
                    continue
                if self.valid(trial):
                    picked = trial
                    break
            if picked is None:
                break
            plan = picked
            long_moves += self.mode == "dugorocno"
            adds -= 1
        for m in plan:
            m.reserves = self.alternatives(plan, m)
        return sorted(plan, key=lambda m: (m.t, 0 if m.forced else 1, -m.gain))
