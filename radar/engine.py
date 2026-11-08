"""Per-day player values and a memoized lineup optimizer shared by the week analysis and the planner."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Iterable

from .injuries import OutEntry
from .lineup import Lineup, optimize
from .schedule import ProSchedule
from .value import Valued

ACTIVE_SLOTS = {0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11}


@dataclass
class Engine:
    schedule: ProSchedule
    now: dt.datetime
    today_sp: int
    days: list[int]                       # remaining scoring periods of the matchup (with open games or not)
    slot_counts: dict[int, int]
    values: dict[int, Valued]
    out_state: dict[int, OutEntry] = field(default_factory=dict)
    dtd_factor: float = 0.85
    _memo: dict = field(default_factory=dict, repr=False)
    _val: dict = field(default_factory=dict, repr=False)

    def val(self, pid: int, sp: int) -> float | None:
        """Projected points for `pid` on day `sp`, or None if he won't play (no open game, OUT)."""
        key = (pid, sp)
        if key in self._val:
            return self._val[key]
        v = self.values.get(pid)
        res = None
        if v is not None:
            p = v.player
            if self.schedule.open_game(p.pro_team_id, sp, self.now):
                res = v.now
                if p.is_out:
                    e = self.out_state.get(pid)
                    back = e.info.max_date if e else None
                    res = v.now * 0.7 if back and self.schedule.date(sp) > back else None
                elif p.is_dtd and sp <= self.today_sp + 1:
                    res = v.now * self.dtd_factor
        self._val[key] = res
        return res

    def plays(self, pid: int, sp: int) -> bool:
        return self.val(pid, sp) is not None

    def opt(self, roster: Iterable[int], sp: int) -> Lineup:
        playing = frozenset(pid for pid in roster if self.plays(pid, sp))
        key = (sp, playing)
        hit = self._memo.get(key)
        if hit is None:
            hit = optimize(((pid, self.values[pid].player.eligible, self.val(pid, sp)) for pid in playing),
                           self.slot_counts)
            self._memo[key] = hit
        return hit

    def total(self, roster: Iterable[int], sp: int) -> float:
        return self.opt(roster, sp).total

    def week_total(self, roster_by_day: dict[int, set[int]]) -> float:
        return round(sum(self.total(r, d) for d, r in roster_by_day.items()), 2)
