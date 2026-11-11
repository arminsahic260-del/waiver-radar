"""Raw ESPN JSON -> models."""
from __future__ import annotations

import datetime as dt

from ..models import (FreeAgent, GameLog, LeagueSettings, LeagueState, Matchup, Player, RosterSpot, StatLine, Team,
                      Transaction)
from .maps import BENCH, SOURCE_PROJ, SOURCE_REAL, SPLIT_GAME, SPLIT_LAST7, SPLIT_LAST15, SPLIT_SEASON, STAT

STARTING_SLOT_IDS = {0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11}


def _line(st: dict | None) -> StatLine:
    if not st:
        return StatLine()
    s = st.get("stats") or {}
    gp = int(s.get(STAT["GP"]) or 0)
    mins = s.get(STAT["MIN"])
    avg = st.get("appliedAverage")
    if avg is None and gp:
        avg = (st.get("appliedTotal") or 0) / gp
    return StatLine(gp=gp, avg=round(avg, 2) if avg is not None and gp else None,
                    mpg=round(mins / gp, 1) if mins is not None and gp else None, gs=int(s.get(STAT["GS"]) or 0))


def parse_player(raw: dict, season: int) -> Player:
    stats = raw.get("stats") or []
    by_id = {s.get("id"): s for s in stats}

    def pick(split, source, year):
        s = by_id.get(f"{source}{split}{year}")
        if s:
            return s
        for s in stats:  # ids are usually "<source><split><season>", but don't rely on it
            if (s.get("statSplitTypeId") == split and s.get("statSourceId") == source and s.get("seasonId") == year
                    and not s.get("scoringPeriodId")):
                return s
        return None

    games = []
    for s in stats:
        if s.get("statSplitTypeId") != SPLIT_GAME or s.get("statSourceId") != SOURCE_REAL:
            continue
        if s.get("seasonId") not in (None, season):
            continue
        v = s.get("stats") or {}
        mins = float(v.get(STAT["MIN"]) or 0)
        if not mins and not v.get(STAT["GP"]):
            continue  # DNP
        games.append(GameLog(sp=int(s.get("scoringPeriodId") or 0), fpts=float(s.get("appliedTotal") or 0), min=mins,
                             gs=bool(v.get(STAT["GS"])), pts=int(v.get(STAT["PTS"]) or 0),
                             reb=int(v.get(STAT["REB"]) or 0), ast=int(v.get(STAT["AST"]) or 0),
                             stl=int(v.get(STAT["STL"]) or 0), blk=int(v.get(STAT["BLK"]) or 0)))
    games.sort(key=lambda g: g.sp)
    own = raw.get("ownership") or {}
    season_line = _line(pick(SPLIT_SEASON, SOURCE_REAL, season))
    return Player(
        id=int(raw["id"]), name=raw.get("fullName") or f"#{raw['id']}", pro_team_id=int(raw.get("proTeamId") or 0),
        pos_id=int(raw.get("defaultPositionId") or 0), eligible=[int(x) for x in raw.get("eligibleSlots") or []],
        injury=raw.get("injuryStatus") or "ACTIVE", owned=round(float(own.get("percentOwned") or 0), 2),
        owned_change=round(float(own["percentChange"]), 2) if own.get("percentChange") is not None else None,
        droppable=raw.get("droppable", True) is not False,
        season=season_line,
        last7=_line(pick(SPLIT_LAST7, SOURCE_REAL, season)),
        last15=_line(pick(SPLIT_LAST15, SOURCE_REAL, season)),
        proj=_line(pick(SPLIT_SEASON, SOURCE_PROJ, season)),
        prev=_line(pick(SPLIT_SEASON, SOURCE_REAL, season - 1)),
        games=games,
        last_news_ms=raw.get("lastNewsDate"),
    )


def parse_settings(raw_settings: dict) -> LeagueSettings:
    rs = raw_settings.get("rosterSettings") or {}
    counts = {int(k): int(v) for k, v in (rs.get("lineupSlotCounts") or {}).items() if v}
    pos_limits = {int(k): int(v) for k, v in (rs.get("positionLimits") or {}).items()}
    acq = raw_settings.get("acquisitionSettings") or {}
    c = pos_limits.get(5)
    return LeagueSettings(
        slot_counts={k: v for k, v in counts.items() if k in STARTING_SLOT_IDS},
        bench=counts.get(12, 3), ir=counts.get(13, 1),
        max_centers=c if c and c > 0 else None,
        acquisition_limit=int(acq["matchupAcquisitionLimit"]) if acq.get("matchupAcquisitionLimit") not in (None, -1)
        else None,
        waiver_hours=acq.get("waiverHours"),
    )


def parse_transactions(raw: dict | None) -> list[Transaction]:
    out = []
    for t in (raw or {}).get("transactions") or []:
        adds = [int(i["playerId"]) for i in t.get("items") or [] if i.get("type") == "ADD"]
        drops = [int(i["playerId"]) for i in t.get("items") or [] if i.get("type") == "DROP"]
        out.append(Transaction(id=str(t.get("id")), team_id=int(t.get("teamId") or 0), type=str(t.get("type") or ""),
                               status=str(t.get("status") or ""), sp=t.get("scoringPeriodId"),
                               date_ms=t.get("processDate") or t.get("proposedDate"), adds=adds, drops=drops))
    return out


def _matchup(raw_league: dict, team_id: int, period: int) -> Matchup | None:
    for m in raw_league.get("schedule") or []:
        if m.get("matchupPeriodId") != period:
            continue
        home, away = m.get("home") or {}, m.get("away") or {}
        if home.get("teamId") == team_id:
            mine, opp = home, away
        elif away.get("teamId") == team_id:
            mine, opp = away, home
        else:
            continue

        def pts(side):
            v = side.get("totalPointsLive")
            return float(v if v is not None else side.get("totalPoints") or 0)

        def by_sp(side):
            return {int(k): float(v or 0) for k, v in (side.get("pointsByScoringPeriod") or {}).items()}

        return Matchup(period=period, my_points=pts(mine), opp_points=pts(opp) if opp else 0, my_by_sp=by_sp(mine),
                       opp_by_sp=by_sp(opp) if opp else {}, opp_team_id=opp.get("teamId") if opp else None)
    return None


def _team(raw_team: dict, season: int, pool_players: dict[int, Player]) -> Team:
    roster = []
    for e in (raw_team.get("roster") or {}).get("entries") or []:
        raw_p = (e.get("playerPoolEntry") or {}).get("player") or {}
        if not raw_p.get("id"):
            continue
        pid = int(raw_p["id"])
        p = pool_players.get(pid) or parse_player(raw_p, season)
        roster.append(RosterSpot(player=p, slot=int(e.get("lineupSlotId", BENCH))))
    tc = raw_team.get("transactionCounter") or {}
    return Team(id=int(raw_team["id"]), name=(raw_team.get("name") or "").strip() or f"Tim {raw_team['id']}",
                abbrev=raw_team.get("abbrev") or "", roster=roster,
                adds_by_period={str(k): int(v) for k, v in (tc.get("matchupAcquisitionTotals") or {}).items()})


def _fix_roster(team: Team, on_team: dict[int, int], players: dict[int, Player]) -> list[str]:
    """mRoster lags after an add/drop; kona_player_info's onTeamId is the truth. Returns what was corrected."""
    fixes = []
    keep = []
    for r in team.roster:
        owner = on_team.get(r.player.id)
        if owner is not None and owner != team.id:
            fixes.append(f"{r.player.name} više nije na rosteru (ESPN cache kasni)")
            continue
        keep.append(r)
    have = {r.player.id for r in keep}
    for pid, owner in on_team.items():
        if owner == team.id and pid not in have and pid in players:
            keep.append(RosterSpot(player=players[pid], slot=BENCH, added_now=True))
            fixes.append(f"{players[pid].name} je već na rosteru (ESPN cache kasni)")
    team.roster = keep
    return fixes


def parse_league(key: str, raw_league: dict, raw_pool: dict | None, raw_tx: dict | None, my_team_id: int,
                 season: int, name: str = "", fetched_at: dt.datetime | None = None) -> LeagueState:
    status = raw_league.get("status") or {}
    sp = int(raw_league.get("scoringPeriodId") or status.get("latestScoringPeriod") or 1)
    period = int(status.get("currentMatchupPeriod") or 1)
    settings = parse_settings(raw_league.get("settings") or {})

    players: dict[int, Player] = {}
    on_team: dict[int, int] = {}
    fas: list[FreeAgent] = []
    for x in (raw_pool or {}).get("players") or []:
        raw_p = x.get("player") or {}
        if not raw_p.get("id"):
            continue
        p = parse_player(raw_p, season)
        players[p.id] = p
        st = x.get("status")
        on_team[p.id] = int(x.get("onTeamId") or 0) if st == "ONTEAM" else 0
        if st in ("FREEAGENT", "WAIVERS"):
            fas.append(FreeAgent(player=p, status=st,
                                 waiver_ms=x.get("waiverProcessDate") if st == "WAIVERS" else None))

    raw_teams = {int(t["id"]): t for t in raw_league.get("teams") or []}
    if my_team_id not in raw_teams:
        raise ValueError(f"tim {my_team_id} ne postoji u ligi {key}")
    teams = {tid: _team(t, season, players) for tid, t in raw_teams.items()}
    for t in teams.values():
        for r in t.roster:
            players.setdefault(r.player.id, r.player)
    fixes = []
    if on_team:
        for tid, t in teams.items():
            f = _fix_roster(t, on_team, players)
            if tid == my_team_id:
                fixes += f
        rostered = {pid for t in teams.values() for pid in t.player_ids()}
        fas = [f for f in fas if f.player.id not in rostered]
    periods = sorted({int(m.get("matchupPeriodId") or 0) for m in raw_league.get("schedule") or []})
    matchups = {}
    for per in periods:
        m = _matchup(raw_league, my_team_id, per)
        if m:
            matchups[per] = m
    me = teams[my_team_id]
    matchup = matchups.get(period)
    opp = teams.get(matchup.opp_team_id) if matchup and matchup.opp_team_id else None

    return LeagueState(
        key=key, league_id=int(raw_league.get("id") or 0), name=name or (raw_league.get("settings") or {}).get("name", key),
        sp=sp, matchup_period=period, settings=settings, me=me, opp=opp, matchup=matchup, free_agents=fas,
        players=players, on_team=on_team, team_names={k: t.name for k, t in teams.items()},
        teams=teams, matchups=matchups,
        transactions=parse_transactions(raw_tx), fetched_at=fetched_at, roster_fixes=fixes,
    )
