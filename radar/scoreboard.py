"""Tonight's NBA games from ESPN's public scoreboard: tip-off times (shown in Europe/Zagreb) and live state.

The fantasy schedule (proTeamSchedules_wl) has the season; the scoreboard is the up-to-date truth for today
(time changes, postponements, games in progress), so the pre-game checks run off it."""
from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from .espn.maps import PRO_TEAM
from .schedule import ET

log = logging.getLogger("radar.scoreboard")
_ABBR = {"GS": "GSW", "NY": "NYK", "SA": "SAS", "NO": "NOP", "UTAH": "UTA", "WSH": "WAS", "BKN": "BKN"}
_BY_ABBR = {v: k for k, v in PRO_TEAM.items()}


@dataclass(frozen=True)
class SbGame:
    id: str
    start: dt.datetime          # UTC
    home: int                   # fantasy proTeamId
    away: int
    state: str                  # pre | in | post
    detail: str = ""            # "Final", "Q3 5:12", "Postponed"...

    @property
    def label(self) -> str:
        return f"{PRO_TEAM.get(self.away, '?')}–{PRO_TEAM.get(self.home, '?')}"

    def teams(self) -> set[int]:
        return {self.home, self.away}

    def local(self, tz: str = "Europe/Zagreb") -> str:
        return self.start.astimezone(ZoneInfo(tz)).strftime("%H:%M")


def _team_id(t: dict) -> int:
    team = t.get("team") or {}
    try:
        tid = int(team.get("id") or 0)
    except ValueError:
        tid = 0
    if tid in PRO_TEAM:
        return tid
    ab = str(team.get("abbreviation") or "").upper()
    return _BY_ABBR.get(_ABBR.get(ab, ab), 0)


def parse_scoreboard(raw: dict) -> list[SbGame]:
    out = []
    for e in (raw or {}).get("events") or []:
        try:
            start = dt.datetime.fromisoformat(str(e.get("date")).replace("Z", "+00:00"))
        except ValueError:
            continue
        if start.tzinfo is None:
            start = start.replace(tzinfo=dt.timezone.utc)
        comp = (e.get("competitions") or [{}])[0]
        home = away = 0
        for c in comp.get("competitors") or []:
            if c.get("homeAway") == "home":
                home = _team_id(c)
            else:
                away = _team_id(c)
        st = ((e.get("status") or {}).get("type") or {})
        state = st.get("state") or "pre"
        if st.get("name") in ("STATUS_POSTPONED", "STATUS_CANCELED"):
            state = "post"
        out.append(SbGame(id=str(e.get("id")), start=start.astimezone(dt.timezone.utc), home=home, away=away,
                          state=state, detail=str(st.get("shortDetail") or st.get("detail") or "")))
    out.sort(key=lambda g: (g.start, g.id))
    return out


def games_for(client, date_et: dt.date) -> list[SbGame]:
    try:
        return parse_scoreboard(client.scoreboard(date_et))
    except Exception as e:  # the pre-game check falls back to the fantasy schedule
        log.warning("scoreboard %s: %s", date_et, e)
        return []


def from_schedule(schedule, sp: int) -> list[SbGame]:
    """Fallback when the scoreboard is unreachable: the fantasy schedule's games of that day."""
    return [SbGame(id=str(g.id), start=g.start, home=g.home, away=g.away, state="pre") for g in schedule.games_on(sp)]


def et_today(now: dt.datetime) -> dt.date:
    return now.astimezone(ET).date()


def busy(games: list[SbGame], now: dt.datetime, ahead_min: float = 90) -> bool:
    """A game is on, or one starts within `ahead_min` minutes: poll more often."""
    soon = now + dt.timedelta(minutes=ahead_min)
    return any(g.state == "in" or (g.state == "pre" and now <= g.start <= soon) for g in games)
