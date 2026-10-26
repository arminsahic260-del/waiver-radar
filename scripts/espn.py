"""ESPN fantasy (fba) reads: league rosters + player pool -> site/data/rosters.json and cache/players.json."""
import datetime as dt
import json
import os
import sys

from common import (CACHE, ESPN_POS, ESPN_SLOT, ESPN_TEAM, ET, data_path, get, load_config, norm_name, now_iso,
                    read_json, write_json)

API = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/fba/seasons/{season}/segments/0/leagues/{league}"
POOL_LIMIT = 1000


def cookies():
    """Only needed for private leagues: set ESPN_S2 and ESPN_SWID (GitHub secrets)."""
    s2, swid = os.environ.get("ESPN_S2"), os.environ.get("ESPN_SWID")
    return {"espn_s2": s2, "SWID": swid} if s2 and swid else None


def fetch_raw(season, league_id):
    base = API.format(season=season, league=league_id)
    rosters = get(base + "?view=mTeam&view=mRoster&view=mDraftDetail", cookies=cookies()).json()
    flt = {"players": {"filterStatus": {"value": ["FREEAGENT", "WAIVERS", "ONTEAM"]}, "limit": POOL_LIMIT,
                       "sortPercOwned": {"sortPriority": 1, "sortAsc": False}}}
    pool = get(base + "?view=kona_player_info", headers={"X-Fantasy-Filter": json.dumps(flt)},
               cookies=cookies()).json()
    return rosters, pool


def _avg(stat):
    a = (stat or {}).get("averageStats") or {}
    return a.get("42") or 0, a.get("40") or 0  # games played, minutes per game


def estimate(stats, season, blend_games=4):
    """Fantasy points per game in this league's scoring (ESPN `appliedAverage`).

    This season is blended with a prior (last season with 10+ games, else ESPN's projection). This season weighs
    gp / (gp + blend_games): with the default 4, two games count a third, four games half, twelve games 3/4.
    Returns fpts/mpg (blended) and basis, plus this season's own numbers (gp, cur) and the prior."""
    by = {s.get("id"): s for s in stats or []}
    cur, last, proj = by.get(f"00{season}"), by.get(f"00{season - 1}"), by.get(f"10{season}")
    prior = None
    for s, min_gp, basis in ((last, 10, "prošla sezona"), (proj, 0, "projekcija")):
        gp, mpg = _avg(s)
        if s and s.get("appliedAverage") and gp >= min_gp:
            prior = (s["appliedAverage"], mpg, basis)
            break
    gp, mpg = _avg(cur)
    out = {"fpts": 0.0, "mpg": 0.0, "basis": None, "gp": int(gp), "cur": None, "prior": None}
    if cur and gp:
        out["cur"] = round(cur.get("appliedAverage") or 0, 1)
    if prior:
        out["prior"] = round(prior[0], 1)
    if out["cur"] is not None and prior:
        w = gp / (gp + blend_games)
        fpts, mpg, basis = (w * (cur.get("appliedAverage") or 0) + (1 - w) * prior[0], w * mpg + (1 - w) * prior[1],
                            "sezona+" + prior[2])
    elif out["cur"] is not None:
        fpts, basis = cur.get("appliedAverage") or 0, "sezona"
    elif prior:
        fpts, mpg, basis = prior
    else:
        return out
    return dict(out, fpts=round(fpts, 1), mpg=round(mpg, 1), basis=basis)


def projection(stats, season):
    """ESPN's preseason projection for this season: fantasy points per game in this league's scoring."""
    s = next((s for s in stats or [] if s.get("id") == f"10{season}"), None)
    return round(s["appliedAverage"], 1) if s and s.get("appliedAverage") else None


def slim(player, season, blend_games=4):
    elig = [ESPN_SLOT[s] for s in player.get("eligibleSlots", []) if s in (0, 1, 2, 3, 4)]
    return {
        "espnId": player["id"],
        "name": player.get("fullName"),
        "team": ESPN_TEAM.get(player.get("proTeamId"), "FA"),
        "pos": "/".join(elig) or ESPN_POS.get(player.get("defaultPositionId"), ""),
        "posId": player.get("defaultPositionId"),
        "injury": player.get("injuryStatus") or "ACTIVE",
        **estimate(player.get("stats"), season, blend_games),
        "owned": round(((player.get("ownership") or {}).get("percentOwned") or 0), 1),
    }


def waiver_date(entry):
    """ET date ESPN processes claims for a player on waivers (he can play for you from that day), else None."""
    ms = entry.get("waiverProcessDate") if entry.get("status") == "WAIVERS" else None
    return dt.datetime.fromtimestamp(ms / 1000, ET).date().isoformat() if ms else None


def build_league(lc, season, rosters_raw, pool_raw, blend_games=4):
    teams = {t["id"]: t for t in rosters_raw.get("teams", [])}
    my_id = lc.get("espnTeamId")
    if not my_id and lc.get("teamName"):
        want = norm_name(lc["teamName"])
        my_id = next((tid for tid, t in teams.items() if want and want in norm_name(t.get("name"))), None)
    picks = {p["playerId"]: p for p in (rosters_raw.get("draftDetail") or {}).get("picks", [])}
    players, rostered, waivers, mine = {}, {}, {}, []
    for tid, t in teams.items():
        for e in (t.get("roster") or {}).get("entries", []):
            p = e["playerPoolEntry"]["player"]
            sp = slim(p, season, blend_games)
            players[sp["espnId"]] = sp
            rostered[sp["espnId"]] = tid
            if tid == my_id:
                # this season's average next to the projection; `draft` is the league draft pick (any team's),
                # `acq` how he got here (DRAFT / ADD / TRADE)
                pick = picks.get(sp["espnId"])
                draft = ({"pick": pick["overallPickNumber"], "round": pick["roundId"],
                          "roundPick": pick["roundPickNumber"], "teamId": pick["teamId"]} if pick else None)
                mine.append({"name": sp["name"], "team": sp["team"], "pos": sp["pos"],
                             "slot": ESPN_SLOT.get(e.get("lineupSlotId"), ""), "espnId": sp["espnId"],
                             "gp": sp["gp"], "cur": sp["cur"], "proj": projection(p.get("stats"), season),
                             "acq": e.get("acquisitionType"), "draft": draft})
    for x in pool_raw.get("players", []):
        sp = slim(x["player"], season, blend_games)
        players.setdefault(sp["espnId"], sp)
        if x.get("onTeamId"):
            rostered.setdefault(sp["espnId"], x["onTeamId"])
        elif waiver_date(x):
            waivers[str(sp["espnId"])] = waiver_date(x)
    slot_order = {s: i for i, s in enumerate(["PG", "SG", "SF", "PF", "C", "G", "F", "UTIL", "BE", "IR"])}
    mine.sort(key=lambda p: slot_order.get(p["slot"], 99))
    # adds used in the current H2H matchup (ESPN counts them per matchup period); the limit is in config.json
    period = (rosters_raw.get("status") or {}).get("currentMatchupPeriod")
    used = ((teams.get(my_id) or {}).get("transactionCounter") or {}).get("matchupAcquisitionTotals") or {}
    entry = {"id": lc["id"], "name": lc["name"], "team": lc.get("teamName") or (teams.get(my_id) or {}).get("name"),
             "espnLeagueId": lc["espnLeagueId"], "espnTeamId": my_id, "public": cookies() is None,
             "updated": now_iso(), "adds": {"period": period, "used": used.get(str(period), 0)}, "players": mine}
    pool = {"myTeamId": my_id, "teams": {str(k): v.get("name") for k, v in teams.items()},
            "rostered": {str(k): v for k, v in rostered.items()}, "waivers": waivers,
            "players": {str(k): v for k, v in players.items()}}
    return entry, pool


def manual_entry(lc):
    return {"id": lc["id"], "name": lc["name"], "team": lc.get("teamName"), "espnLeagueId": lc.get("espnLeagueId"),
            "espnTeamId": lc.get("espnTeamId"), "public": False, "updated": None,
            "players": [dict(p, slot=p.get("slot", ""), espnId=p.get("espnId")) for p in lc.get("manualPlayers", [])]}


def league_error(e):
    """Short Croatian reason shown next to the league in the UI."""
    code = getattr(getattr(e, "response", None), "status_code", None)
    if code in (401, 403):
        if cookies():
            return "ESPN odbija prijavu — obnovi ESPN_S2/ESPN_SWID"
        return "privatna liga — dodaj ESPN_S2 i ESPN_SWID (GitHub secrets)"
    if code == 404:
        return "liga nije pronađena — provjeri espnLeagueId"
    return f"ESPN greška: {e}"[:200]


def run(cfg, prev=None):
    """Returns (rosters doc, {league id: pool}). A failing league keeps its previous ESPN roster (or the manual one)
    and carries an `error` the UI shows next to the league."""
    prev_by_id = {L["id"]: L for L in (prev or {}).get("leagues", [])}
    leagues, pools, errors = [], {}, []
    for lc in cfg["leagues"]:
        if not lc.get("espnLeagueId"):
            leagues.append(manual_entry(lc))
            continue
        try:
            entry, pool = build_league(lc, cfg["season"], *fetch_raw(cfg["season"], lc["espnLeagueId"]),
                                       blend_games=cfg.get("blendGames", 4))
            if not entry["players"]:
                raise RuntimeError(f"my team not found (espnTeamId={lc.get('espnTeamId')})")
            entry["adds"]["limit"] = lc.get("weeklyAdds", cfg.get("weeklyAdds", 7))
            leagues.append(entry)
            pools[lc["id"]] = pool
        except Exception as e:
            errors.append(f"{lc['id']}: {e}")
            print(f"  ! ESPN {lc['id']}: {e}", file=sys.stderr)
            old = prev_by_id.get(lc["id"])
            keep = old if old and old.get("updated") and old.get("espnLeagueId") == lc["espnLeagueId"] else None
            leagues.append(dict(keep or manual_entry(lc), error=league_error(e)))
    doc = {"updated": now_iso(), "leagues": leagues}
    write_json(data_path("rosters.json"), doc)
    write_json(os.path.join(CACHE, "players.json"), pools)
    if errors and not pools:
        raise RuntimeError("; ".join(errors))
    return doc, pools


def load_pools():
    return read_json(os.path.join(CACHE, "players.json"), {})


if __name__ == "__main__":
    d, p = run(load_config(), read_json(data_path("rosters.json")))
    for L in d["leagues"]:
        print(L["name"], len(L["players"]), "players;", len(p.get(L["id"], {}).get("players", {})), "in pool")
