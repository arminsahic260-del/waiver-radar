"""Per-day player values (value x p_play) and a memoized lineup optimizer shared by the week analysis, the planner
and the win-probability simulation."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Iterable

from .availability import p_play
from .config import ModelCfg, PPlayCfg
from .injuries import OutEntry
from .lineup import Lineup, optimize
from .lineups import p_from
from .models import Player
from .schedule import ProSchedule
from .value import Valued, value_now

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
    pcfg: PPlayCfg = field(default_factory=PPlayCfg)
    flags: dict[int, tuple[str, str]] = field(default_factory=dict)   # pid -> ('upitan'|'out', news sentence)
    ages: dict[int, int] = field(default_factory=dict)
    hurt: dict[int, list[Player]] = field(default_factory=dict)       # NBA team -> who matters and is OUT/DTD
    mcfg: ModelCfg = field(default_factory=ModelCfg)
    rw: dict = field(default_factory=dict)                            # pid -> lineups.Spot (RotoWire, today)

    def _rw_p(self, pid: int, sp: int) -> float | None:
        """RotoWire's chance to play for that game, when he is on today's lineups page."""
        s = self.rw.get(pid)
        if s is None or s.sp != sp:
            return None
        return p_from(s, self.pcfg.active)
    _memo: dict = field(default_factory=dict, repr=False)
    _val: dict = field(default_factory=dict, repr=False)
    _p: dict = field(default_factory=dict, repr=False)
    _boost: dict = field(default_factory=dict, repr=False)

    def miss(self, mate: Player, sp: int) -> float:
        """Probability that `mate` (a hurt player, maybe not in `values`) misses his team's game on day `sp`."""
        if mate.id in self.values:
            return 1.0 - self.p(mate.id, sp)
        r = self._rw_p(mate.id, sp)
        if r is not None:
            return 1.0 - r
        e = self.out_state.get(mate.id)
        return 1.0 - p_play(mate, sp, self.today_sp, self.schedule, self.pcfg, None, e.info.max_date if e else None)

    def boost(self, pid: int, sp: int) -> float:
        """Extra FP for `pid` on day `sp` because teammates who matter will probably miss that game: their FP x
        share (bench/rotation players gain, 28+ min starters hardly), x the chance they miss, minus what his own
        recent games (already without them) show. Capped."""
        key = (pid, sp)
        if key in self._boost:
            return self._boost[key]
        v, res = self.values.get(pid), 0.0
        mates = self.hurt.get(v.player.pro_team_id) if v is not None else None
        if mates:
            c = self.mcfg
            mpg = v.mpg3 if v.mpg3 is not None else (v.mpg or 0)
            share = c.suigrac_udio if mpg < c.suigrac_starter_min else c.suigrac_udio_starter
            for m in mates:
                if m.id == pid or not m.games:
                    continue
                miss = self.miss(m, sp)
                if miss <= 0:
                    continue
                without = sum(1 for g in v.player.games if g.sp > m.games[-1].sp)
                res += miss * share * value_now(m) * max(0.0, 1 - without / 4)
            res = round(max(0.0, min(res, c.suigrac_max_fp, c.suigrac_max_udio * max(v.now, 0))), 2)
        self._boost[key] = res
        return res

    def p(self, pid: int, sp: int) -> float:
        """p_play for `pid` on day `sp` (0 when there is no open game)."""
        key = (pid, sp)
        if key in self._p:
            return self._p[key]
        v = self.values.get(pid)
        res = 0.0
        if v is not None and self.schedule.open_game(v.player.pro_team_id, sp, self.now):
            r = self._rw_p(pid, sp)
            if r is not None:
                res = r                    # RotoWire knows tonight's scratches before ESPN's status changes
            else:
                e = self.out_state.get(pid)
                flag = self.flags.get(pid)
                res = p_play(v.player, sp, self.today_sp, self.schedule, self.pcfg, flag[0] if flag else None,
                             e.info.max_date if e else None, self.ages.get(pid))
        self._p[key] = res
        return res

    def val(self, pid: int, sp: int) -> float | None:
        """Expected points for `pid` on day `sp` (value x p_play), or None if he won't play (no open game, OUT)."""
        key = (pid, sp)
        if key in self._val:
            return self._val[key]
        pp = self.p(pid, sp)
        res = round((self.values[pid].now + self.boost(pid, sp)) * pp, 3) if pp > 0 else None
        self._val[key] = res
        return res

    def plays(self, pid: int, sp: int) -> bool:
        return self.val(pid, sp) is not None

    def b2b(self, pid: int, sp: int) -> bool:
        v = self.values.get(pid)
        return bool(v) and self.schedule.plays(v.player.pro_team_id, sp) and \
            self.schedule.plays(v.player.pro_team_id, sp - 1)

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

    def expected(self, pid: int, from_sp: int | None = None) -> float:
        """Expected FP over his remaining games this week (ignoring whether he'd make my lineup)."""
        return round(sum(self.val(pid, d) or 0 for d in self.days if from_sp is None or d >= from_sp), 1)
