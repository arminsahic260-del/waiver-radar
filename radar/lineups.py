"""RotoWire NBA lineups (https://www.rotowire.com/basketball/nba-lineups.php): for today's games the expected or
confirmed starting five and the 'may not play' list with RotoWire's chance to play (0/25/50/75/100 %).

A second source next to ESPN (12.11.2026): it knows about late scratches, rest days and questionable tags before
ESPN's injury status changes, and who starts tonight. Tomorrow's page is empty until game day, so this is about
today's games only."""
from __future__ import annotations

import html as _html
import re
from dataclasses import dataclass

from .config import name_key
from .espn.maps import PRO_TEAM
from .models import Player
from .schedule import ProSchedule

URL = "https://www.rotowire.com/basketball/nba-lineups.php"
ALIAS = {"GS": "GSW", "NY": "NYK", "SA": "SAS", "NO": "NOP", "NOR": "NOP", "UTAH": "UTA", "WSH": "WAS",
         "PHO": "PHX", "BRK": "BKN", "CHO": "CHA"}
TEAM_ID = {abbr: tid for tid, abbr in PRO_TEAM.items()}
STATUS = {"Out": "out", "OFS": "out", "Ques": "upitan", "Doubt": "upitan", "GTD": "upitan", "Prob": "vjerojatno"}
SUFFIX = {"jr", "sr", "ii", "iii", "iv", "v"}


@dataclass
class Spot:
    name: str
    team: str                 # ESPN abbreviation
    pct: int | None           # RotoWire's chance to play, %
    status: str | None        # 'Out', 'Ques', 'Prob', 'OFS', ... (None: no injury tag)
    starter: bool             # in the expected/confirmed starting five
    confirmed: bool           # the team's lineup is confirmed (else expected)
    sp: int | None = None     # scoring period of the game, set by `match`

    def label(self) -> str:
        """'starter (potvrđeno), 100 %' / 'Ques, 50 %'."""
        bits = []
        if self.starter:
            bits.append(f"starter ({'potvrđeno' if self.confirmed else 'očekivano'})")
        if self.status:
            bits.append(self.status)
        if self.pct is not None:
            bits.append(f"{self.pct} % da igra")
        return ", ".join(bits)


def _team(abbr: str) -> str:
    a = abbr.strip().upper()
    return ALIAS.get(a, a)


def parse(page: str) -> list[tuple[str, str, list[Spot]]]:
    """[(away, home, spots)] for every game on the page."""
    games = []
    for block in re.split(r'<div class="lineup is-nba', page)[1:]:
        abbrs = [_team(a) for a in re.findall(r'<div class="lineup__abbr">\s*([A-Za-z]+)\s*</div>', block)]
        if len(abbrs) < 2:
            continue
        spots: list[Spot] = []
        for side, body in re.findall(r'<ul class="lineup__list is-(visit|home)">(.*?)</ul>', block, re.S):
            team = abbrs[0] if side == "visit" else abbrs[1]
            confirmed = bool(re.search(r'lineup__status is-confirmed', body))
            starters_part, _, rest = body.partition("MAY NOT PLAY")
            for part, starter in ((starters_part, True), (rest, False)):
                for li in re.findall(r'<li class="lineup__player([^"]*)"(.*?)</li>', part, re.S):
                    cls, inner = li
                    m = re.search(r'<a title="([^"]+)"', inner)
                    if not m:
                        continue
                    pct = re.search(r'is-pct-play-(\d+)', cls)
                    st = re.search(r'<span class="lineup__inj">\s*([^<]+?)\s*</span>', inner)
                    spots.append(Spot(name=_html.unescape(m.group(1)), team=team,
                                      pct=int(pct.group(1)) if pct else None,
                                      status=st.group(1) if st else None, starter=starter, confirmed=confirmed))
        games.append((abbrs[0], abbrs[1], spots))
    return games


def _keys(name: str) -> set[str]:
    k = name_key(name)
    words = k.split()
    bare = " ".join(w for w in words if w not in SUFFIX)
    return {k, bare}


def match(games: list[tuple[str, str, list[Spot]]], players: dict[int, Player], schedule: ProSchedule,
          today_sp: int) -> dict[int, Spot]:
    """ESPN player id -> his spot, for games that are on the ESPN schedule today (or tomorrow, if the page has
    already moved on). Names are matched within the team (accents, dots and Jr./III don't matter)."""
    by_team: dict[int, dict[str, int]] = {}
    for p in players.values():
        for k in _keys(p.name):
            by_team.setdefault(p.pro_team_id, {}).setdefault(k, p.id)
    out: dict[int, Spot] = {}
    for away, home, spots in games:
        ta, th = TEAM_ID.get(away), TEAM_ID.get(home)
        if ta is None or th is None:
            continue
        sp = next((d for d in (today_sp, today_sp + 1)
                   if (g := schedule.game(th, d)) is not None and {g.home, g.away} == {ta, th}), None)
        if sp is None:
            continue
        for s in spots:
            names = by_team.get(TEAM_ID.get(s.team), {})
            pid = next((names[k] for k in _keys(s.name) if k in names), None)
            if pid is None:
                continue
            s.sp = sp
            old = out.get(pid)
            if old is not None:      # listed twice (e.g. Embiid 12.11.: in the five and under 'may not play')
                s = Spot(name=s.name, team=s.team, starter=old.starter or s.starter,
                         confirmed=old.confirmed or s.confirmed, status=s.status or old.status, sp=sp,
                         pct=min((x for x in (old.pct, s.pct) if x is not None), default=None))
            out[pid] = s
    return out


def p_from(spot: Spot, active: float = 0.97) -> float | None:
    """RotoWire's chance to play as p_play (100 % -> the normal 'active' value)."""
    if spot.pct is None:
        return None
    return active if spot.pct >= 100 else spot.pct / 100


def fetch(session, timeout: int = 20) -> str | None:
    """Today's page, or None (never breaks a run)."""
    try:
        r = session.get(URL, timeout=timeout, headers={"Accept": "text/html"})
        if r.status_code == 200 and "lineup is-nba" in r.text:
            return r.text
    except Exception:
        pass
    return None
