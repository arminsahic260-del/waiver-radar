"""NBA schedule by scoring period (= one ET day), dates and the current H2H week."""
from __future__ import annotations

import collections
import datetime as dt
from dataclasses import dataclass
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
GAME_LENGTH = dt.timedelta(hours=2, minutes=45)

DAY_NAMES = ["ponedjeljak", "utorak", "srijeda", "četvrtak", "petak", "subota", "nedjelja"]
DAY_SHORT = ["pon", "uto", "sri", "čet", "pet", "sub", "ned"]
DAY_ACC = ["ponedjeljak", "utorak", "srijedu", "četvrtak", "petak", "subotu", "nedjelju"]


@dataclass(frozen=True)
class Game:
    id: int
    sp: int
    start: dt.datetime        # aware (UTC)
    home: int
    away: int


class ProSchedule:
    """`proTeamSchedules_wl`: which pro teams play in which scoring period, and when."""

    def __init__(self, raw: dict, fallback_first_day: dt.date = dt.date(2026, 10, 20)):
        self.by_team: dict[int, dict[int, Game]] = collections.defaultdict(dict)
        starts = collections.Counter()
        for t in (raw.get("settings") or {}).get("proTeams") or []:
            tid = int(t.get("id") or 0)
            if not tid:
                continue
            for sp, games in (t.get("proGamesByScoringPeriod") or {}).items():
                for g in games or []:
                    start = dt.datetime.fromtimestamp(g["date"] / 1000, dt.timezone.utc)
                    game = Game(int(g.get("id") or 0), int(sp), start, int(g.get("homeProTeamId") or 0),
                                int(g.get("awayProTeamId") or 0))
                    self.by_team[tid][int(sp)] = game
                    starts[start.astimezone(ET).date() - dt.timedelta(days=int(sp) - 1)] += 1
        # scoringPeriodId 1 = this date (ET); every game agrees on it, but take the most common to be safe
        self.first_day = starts.most_common(1)[0][0] if starts else fallback_first_day

    def game(self, pro_team_id: int, sp: int) -> Game | None:
        return self.by_team.get(pro_team_id, {}).get(sp)

    def plays(self, pro_team_id: int, sp: int) -> bool:
        return sp in self.by_team.get(pro_team_id, {})

    def open_game(self, pro_team_id: int, sp: int, now: dt.datetime) -> bool:
        """Plays that day and the game hasn't tipped off yet (its points are not banked)."""
        g = self.game(pro_team_id, sp)
        return bool(g and g.start > now)

    def games_on(self, sp: int) -> list[Game]:
        seen = {}
        for games in self.by_team.values():
            g = games.get(sp)
            if g:
                seen[g.id] = g
        return sorted(seen.values(), key=lambda g: g.start)

    def day_done(self, sp: int, now: dt.datetime) -> bool:
        """Every game of the day is over (or there were none and the ET day is past)."""
        games = self.games_on(sp)
        if games:
            return all(g.start + GAME_LENGTH < now for g in games)
        return self.date(sp) < now.astimezone(ET).date()

    def date(self, sp: int) -> dt.date:
        return self.first_day + dt.timedelta(days=sp - 1)

    def sp_of(self, d: dt.date) -> int:
        return (d - self.first_day).days + 1


def week_bounds(schedule: ProSchedule, period: int, played_sps: list[int] | None = None,
                fallback_sp: int | None = None) -> tuple[int, int]:
    """First and last scoring period of matchup `period`.

    Start: the earliest day ESPN already lists for this matchup (pointsByScoringPeriod), else the Monday of the
    week containing `fallback_sp` (week 1 starts on opening day). End: the Sunday of that week."""
    if played_sps:
        start = min(played_sps)
    else:
        sp = fallback_sp or 1
        d = schedule.date(sp)
        start = max(1, schedule.sp_of(d - dt.timedelta(days=d.weekday())))
    d0 = schedule.date(start)
    end = schedule.sp_of(d0 + dt.timedelta(days=6 - d0.weekday()))
    return start, end


def day_label(schedule: ProSchedule, sp: int, today_sp: int) -> str:
    if sp == today_sp:
        return "danas"
    if sp == today_sp + 1:
        return "sutra"
    return DAY_NAMES[schedule.date(sp).weekday()]


def fmt_date(d: dt.date) -> str:
    return f"{d.day}.{d.month}."
