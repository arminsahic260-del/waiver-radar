"""Typed view of what we use from ESPN."""
from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel, Field

from .espn.maps import CENTER_POSITION_ID, INJURY_DTD, INJURY_OUT, POSITION, POSITION_SLOTS, PRO_TEAM


def short_name(name: str) -> str:
    """Last name, keeping suffixes ('Brown Jr.')."""
    parts = name.split()
    if len(parts) >= 3 and parts[-1].rstrip(".").lower() in ("jr", "sr", "ii", "iii", "iv"):
        return " ".join(parts[-2:])
    return parts[-1] if len(parts) > 1 else name


class GameLog(BaseModel):
    sp: int                     # scoringPeriodId of the game
    fpts: float
    min: float = 0
    gs: bool = False            # started
    pts: int = 0
    reb: int = 0
    ast: int = 0
    stl: int = 0
    blk: int = 0

    def line(self) -> str:
        return f"{self.pts}/{self.reb}/{self.ast} u {round(self.min)} min"


class StatLine(BaseModel):
    gp: int = 0
    avg: float | None = None    # fantasy points per game (ESPN appliedAverage, league scoring)
    mpg: float | None = None
    gs: int = 0                 # games started


class Player(BaseModel):
    id: int
    name: str
    pro_team_id: int = 0
    pos_id: int = 0
    eligible: list[int] = Field(default_factory=list)   # lineupSlotIds
    injury: str = "ACTIVE"
    owned: float = 0.0
    owned_change: float | None = None
    droppable: bool = True
    season: StatLine = Field(default_factory=StatLine)
    last7: StatLine = Field(default_factory=StatLine)
    last15: StatLine = Field(default_factory=StatLine)
    proj: StatLine = Field(default_factory=StatLine)
    prev: StatLine = Field(default_factory=StatLine)
    games: list[GameLog] = Field(default_factory=list)  # newest last
    last_news_ms: int | None = None

    @property
    def team(self) -> str:
        return PRO_TEAM.get(self.pro_team_id, "FA")

    @property
    def positions(self) -> str:
        p = [POSITION_SLOTS[s] for s in sorted(self.eligible) if s in POSITION_SLOTS]
        return "/".join(p) or POSITION.get(self.pos_id, "")

    @property
    def pos_set(self) -> set[str]:
        return {POSITION_SLOTS[s] for s in self.eligible if s in POSITION_SLOTS} or {POSITION.get(self.pos_id, "")}

    @property
    def is_center(self) -> bool:
        return self.pos_id == CENTER_POSITION_ID

    @property
    def is_out(self) -> bool:
        return self.injury in INJURY_OUT

    @property
    def has_team(self) -> bool:
        """False for players without an NBA team (waived/unsigned: ESPN proTeamId 0) - they can't play."""
        return self.pro_team_id in PRO_TEAM

    @property
    def is_dtd(self) -> bool:
        return self.injury in INJURY_DTD

    @property
    def short(self) -> str:
        """Last name (keeps suffixes: 'Brown Jr.')."""
        return short_name(self.name)

    def last_games(self, n: int) -> list[GameLog]:
        return self.games[-n:] if n else []


class RosterSpot(BaseModel):
    player: Player
    slot: int                   # lineupSlotId for the current scoring period
    added_now: bool = False     # in ESPN's player pool but not (yet) in the cached roster
    acq: str = ""               # ESPN acquisitionType: DRAFT / ADD / TRADE ("" when unknown)


class DraftPick(BaseModel):
    pick: int                   # overall pick number
    round: int
    team_id: int                # fantasy team that made the pick


class Team(BaseModel):
    id: int
    name: str
    abbrev: str = ""
    roster: list[RosterSpot] = Field(default_factory=list)
    adds_by_period: dict[str, int] = Field(default_factory=dict)

    def player_ids(self) -> set[int]:
        return {r.player.id for r in self.roster}


class FreeAgent(BaseModel):
    player: Player
    status: Literal["FREEAGENT", "WAIVERS"]
    waiver_ms: int | None = None  # when the claim is processed (ESPN waiverProcessDate)


class Matchup(BaseModel):
    period: int
    my_points: float = 0
    opp_points: float = 0
    my_by_sp: dict[int, float] = Field(default_factory=dict)
    opp_by_sp: dict[int, float] = Field(default_factory=dict)
    opp_team_id: int | None = None


class Transaction(BaseModel):
    id: str
    team_id: int
    type: str                     # FREEAGENT / WAIVER / ROSTER / ...
    status: str                   # EXECUTED / CANCELED / PENDING
    sp: int | None = None
    date_ms: int | None = None
    adds: list[int] = Field(default_factory=list)
    drops: list[int] = Field(default_factory=list)


class LeagueSettings(BaseModel):
    slot_counts: dict[int, int] = Field(default_factory=dict)  # starting lineup slots, e.g. {0:1,...,11:3}
    bench: int = 3
    ir: int = 1
    max_centers: int | None = 4
    acquisition_limit: int | None = None   # ESPN's per-matchup number (may be misleading, see config)
    waiver_hours: int | None = None


class LeagueState(BaseModel):
    key: str
    league_id: int
    name: str
    sp: int                        # ESPN's current scoringPeriodId
    matchup_period: int
    settings: LeagueSettings
    me: Team
    opp: Team | None = None
    matchup: Matchup | None = None
    teams: dict[int, Team] = Field(default_factory=dict)          # every fantasy team (rosters corrected)
    matchups: dict[int, Matchup] = Field(default_factory=dict)    # my matchup per period
    free_agents: list[FreeAgent] = Field(default_factory=list)
    players: dict[int, Player] = Field(default_factory=dict)   # whole pool incl. rostered
    on_team: dict[int, int] = Field(default_factory=dict)      # player id -> fantasy team id (truth from pool)
    team_names: dict[int, str] = Field(default_factory=dict)
    transactions: list[Transaction] = Field(default_factory=list)
    fetched_at: dt.datetime | None = None
    roster_fixes: list[str] = Field(default_factory=list)      # corrections applied over the cached roster
    draft: dict[int, DraftPick] = Field(default_factory=dict)  # player id -> this league's draft pick (mDraftDetail)
