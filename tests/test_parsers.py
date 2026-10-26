"""Parsers against pages captured on 2026-10-19 (tests/fixtures)."""
import datetime as dt

import pytest
import requests

import espn
import fetch
from common import ABBRS, norm_name, parse_month_day, team_abbr
from conftest import fixture

CFG = {"season": 2027, "week1": {"start": "2026-10-20", "end": "2026-10-25"}, "scheduleWeeks": 3}


# ---------------------------------------------------------------- common

def test_team_aliases_cover_source_spellings():
    assert team_abbr("Golden St.") == "GSW"
    assert team_abbr("L.A. Clippers") == "LAC"
    assert team_abbr("New York") == "NYK"
    assert team_abbr("PHO") == "PHX"
    assert team_abbr("BRK") == "BKN"
    assert team_abbr("CHO") == "CHA"
    assert team_abbr("Free Agent") == "FA"
    assert team_abbr("Nowhere") is None


def test_norm_name_matches_across_sources():
    assert norm_name("Jimmy Butler III") == norm_name("Jimmy Butler")
    assert norm_name("Nikola Jokić") == norm_name("Nikola Jokic")
    assert norm_name("Darius Acuff Jr.") == norm_name("Darius Acuff")
    assert norm_name("Kel'el Ware") == "kelel ware"


def test_season_dates_roll_over_new_year():
    assert parse_month_day("Mon, Oct 5", 2027) == "2026-10-05"
    assert parse_month_day("at least Jan 2", 2027) == "2027-01-02"
    assert parse_month_day("October 18, 2026", 0) == "2026-10-18"
    assert parse_month_day("Out for the season", 2027) is None


# ---------------------------------------------------------------- CBS injuries

@pytest.mark.parametrize("text,kind,ret,status", [
    ("Game Time Decision", "gtd", None, "GTD"),
    ("Out for the season", "out_season", None, "Out for season"),
    ("Expected to be out until at least Nov 8", "out", "2026-11-08", "Expected return: at least Nov 8"),
    ("Expected to be out until at least Jan 2", "out", "2027-01-02", "Expected return: at least Jan 2"),
    ("Out", "out", None, "Out"),
    ("Day-To-Day", "gtd", None, "GTD"),
])
def test_classify_injury(text, kind, ret, status):
    assert fetch.classify_injury(text, 2027) == (status, kind, ret)


def test_parse_cbs_injuries():
    items = fetch.parse_cbs_injuries(fixture("cbs_injuries.html"), 2027)
    assert len(items) == 91
    assert {i["team"] for i in items} <= set(ABBRS)
    by = {i["player"]: i for i in items}
    assert by["Henri Veesaar"] == {"team": "ATL", "player": "Henri Veesaar", "pos": "C", "updated": "2026-09-21",
                                   "injury": "Knee", "status": "Out for season", "kind": "out_season", "ret": None}
    assert by["Bradley Beal"]["kind"] == "out" and by["Bradley Beal"]["ret"] == "2026-11-08"
    assert by["Jimmy Butler"]["team"] == "GSW" and by["Jimmy Butler"]["ret"] == "2027-01-02"
    assert by["Stephen Curry"]["kind"] == "gtd"
    # sorted by team, then out_season/out/gtd
    assert items == sorted(items, key=lambda x: (x["team"], fetch.KIND_ORDER[x["kind"]], x["player"]))


# ---------------------------------------------------------------- RotoWire news

def test_parse_rotowire_news():
    items = fetch.parse_rotowire_news(fixture("rotowire_news.html"))
    assert len(items) == 25
    first = items[0]
    assert (first["date"], first["team"], first["player"], first["headline"], first["kind"]) == \
        ("2026-10-19", "FA", "Dwight Powell", "Waived by Dallas", "transaction")
    assert first["url"].startswith("https://www.rotowire.com/basketball/headlines/")
    by = {(i["player"], i["headline"]): i for i in items}
    assert by[("Oso Ighodaro", "Inks three-year extension")]["team"] == "PHX"
    assert by[("Stephen Curry", "Suffers minor ankle injury")]["kind"] == "injury"
    assert by[("Fred VanVleet", "Won't face minutes restrictions")]["kind"] == "lineup"
    assert by[("Santi Aldama", "Uncertain for regular-season opener")]["kind"] == "injury"
    assert all(i["note"] and i["source"] == "RotoWire" for i in items)


@pytest.mark.parametrize("headline,note,expected", [
    ("Will start Wednesday", "Smith will start Wednesday against Boston.", True),
    ("Starting Friday", "Kalkbrenner is in the Hornets' starting lineup for Friday's game.", True),
    ("Named starter", "Coach named Jones the starter at point guard.", True),
    ("Starting tonight", "Lee will start, with Brown moving to the bench.", True),
    ("Not listed as starter Friday", "Black is not in the starting lineup for Friday's game.", False),
    ("Shifting to bench", "Alvarado isn't in the starting lineup for Thursday's game.", False),
    ("Coming off bench Friday", "Clifford is not listed in the starting lineup.", False),
    ("Won't face minutes restrictions", "No minutes limit to begin the season.", False),
])
def test_is_start_news(headline, note, expected):
    assert fetch.is_start_news({"headline": headline, "note": note}) is expected


def test_merge_news_dedupes_and_keeps_newest():
    a = {"date": "2026-10-19", "player": "A", "headline": "h1", "note": "new"}
    a_old = dict(a, note="old")
    b = {"date": "2026-10-17", "player": "B", "headline": "h2"}
    c = {"date": "2026-10-18", "player": "C", "headline": "h3"}
    out = fetch.merge_news([a], [a_old, b, c], keep=2)
    assert out == [a, c]


# ---------------------------------------------------------------- Basketball-Reference schedule

def test_parse_bref_schedule():
    games = fetch.parse_bref_schedule(fixture("bref_october.html"))
    assert len(games) == 88
    assert games[0] == {"date": "2026-10-20", "visitor": "BOS", "home": "DET"}
    assert all(g["visitor"] in ABBRS and g["home"] in ABBRS for g in games)


def test_h2h_weeks():
    weeks = fetch.h2h_weeks(CFG)
    assert weeks[0] == (1, dt.date(2026, 10, 20), dt.date(2026, 10, 25))
    assert weeks[1] == (2, dt.date(2026, 10, 26), dt.date(2026, 11, 1))
    assert all(a.weekday() == 0 and (b - a).days == 6 for _, a, b in weeks[1:])
    assert [n for n, _, _ in fetch.visible_weeks(CFG, dt.date(2026, 10, 28))] == [2, 3, 4]
    assert [n for n, _, _ in fetch.visible_weeks(CFG, dt.date(2026, 10, 1))] == [1, 2, 3]


def test_build_schedule_doc():
    games = fetch.parse_bref_schedule(fixture("bref_october.html"))
    doc = fetch.build_schedule_doc(games, fetch.visible_weeks(CFG, dt.date(2026, 10, 19)))
    w1 = doc["weeks"][0]
    assert (w1["label"], w1["start"], w1["end"]) == ("Tjedan 1", "2026-10-20", "2026-10-25")
    assert w1["teams"]["BOS"] == {"n": 2, "dates": ["2026-10-20", "2026-10-23"]}
    assert w1["teams"]["PHI"]["n"] == 4
    assert set(w1["teams"]) == set(ABBRS)
    assert all(g["date"] <= doc["weeks"][-1]["end"] for g in doc["games"])


# ---------------------------------------------------------------- ESPN

def test_espn_build_league():
    lc = {"id": "legendi", "name": "Liga Legendi", "teamName": "DGJ", "espnLeagueId": 2023498222, "espnTeamId": 1}
    entry, pool = espn.build_league(lc, 2027, fixture("espn_rosters.json"), fixture("espn_freeagents.json"))
    assert entry["espnTeamId"] == 1 and len(entry["players"]) == 13
    by = {p["name"]: p for p in entry["players"]}
    assert by["Cooper Flagg"] == {"name": "Cooper Flagg", "team": "DAL", "pos": "SF/PF", "slot": "SF", "espnId": 5041939,
                                  "gp": 0, "cur": None, "proj": 45.4, "acq": "DRAFT", "draft": None}
    assert [p["slot"] for p in entry["players"]][:5] == ["PG", "SG", "SF", "PF", "C"]
    assert len(pool["rostered"]) == 14 * 13
    flagg = pool["players"]["5041939"]
    assert (flagg["fpts"], flagg["basis"]) == (39.7, "prošla sezona")
    fa = pool["players"]["6430"]  # Jimmy Butler III, on waivers
    assert "6430" not in pool["rostered"] and fa["team"] == "GSW" and fa["injury"] == "OUT"
    assert pool["waivers"]["6430"] == "2026-10-20"  # preseason snapshot: everyone clears on opening day
    assert entry["adds"] == {"period": 1, "used": 0}


def test_espn_my_players_carry_their_draft_pick():
    raw = fixture("espn_rosters.json")
    raw["draftDetail"]["picks"] = [
        {"playerId": 5041939, "overallPickNumber": 10, "roundId": 1, "roundPickNumber": 10, "teamId": 1},
        {"playerId": 1, "overallPickNumber": 1, "roundId": 1, "roundPickNumber": 1, "teamId": 10}]
    entry, _ = espn.build_league({"id": "legendi", "name": "L", "espnLeagueId": 1, "espnTeamId": 1}, 2027, raw,
                                 {"players": []})
    by = {p["name"]: p for p in entry["players"]}
    assert by["Cooper Flagg"]["draft"] == {"pick": 10, "round": 1, "roundPick": 10, "teamId": 1}
    assert sum(1 for p in entry["players"] if p["draft"]) == 1


def test_espn_projection_is_this_seasons_per_game():
    stats = [{"id": "102026", "appliedAverage": 30.0}, {"id": "102027", "appliedAverage": 41.26}]
    assert espn.projection(stats, 2027) == 41.3
    assert espn.projection(stats[:1], 2027) is None and espn.projection(None, 2027) is None


def test_espn_finds_my_team_by_name():
    lc = {"id": "x", "name": "X", "teamName": "Druzina gospodara Jokica", "espnLeagueId": 1, "espnTeamId": None}
    entry, pool = espn.build_league(lc, 2027, fixture("espn_rosters.json"), {"players": []})
    assert entry["espnTeamId"] == 1 and pool["myTeamId"] == 1


def test_espn_estimate_blends_this_season_with_prior():
    proj = {"id": "102027", "appliedAverage": 22.04, "averageStats": {"40": 28.0}}
    short = {"id": "002027", "appliedAverage": 50.0, "averageStats": {"42": 2, "40": 30.0}}
    # 2 games -> this season weighs 2 / (2 + 4) = 1/3
    assert espn.estimate([proj, short], 2027) == {"fpts": 31.4, "mpg": 28.7, "basis": "sezona+projekcija", "gp": 2,
                                                  "cur": 50.0, "prior": 22.0}
    last = {"id": "002026", "appliedAverage": 30.0, "averageStats": {"42": 60, "40": 32.0}}
    full = dict(short, averageStats={"42": 12, "40": 30.0})   # 12 games -> 3/4
    e = espn.estimate([proj, last, full], 2027)
    assert (e["fpts"], e["mpg"], e["basis"], e["prior"]) == (45.0, 30.5, "sezona+prošla sezona", 30.0)
    thin_last = dict(last, averageStats={"42": 6, "40": 32.0})  # under 10 games: projection is the prior
    assert espn.estimate([proj, thin_last], 2027)["basis"] == "projekcija"
    assert espn.estimate([short], 2027)["basis"] == "sezona" and espn.estimate([short], 2027)["fpts"] == 50.0
    assert espn.estimate([proj, short], 2027, blend_games=0)["fpts"] == 50.0
    assert espn.estimate([], 2027) == {"fpts": 0.0, "mpg": 0.0, "basis": None, "gp": 0, "cur": None, "prior": None}


def test_espn_waiver_date_is_the_et_claim_day():
    assert espn.waiver_date({"status": "WAIVERS", "waiverProcessDate": 1792911600000}) == "2026-10-25"  # 07:00Z
    assert espn.waiver_date({"status": "FREEAGENT", "waiverProcessDate": 1792911600000}) is None
    assert espn.waiver_date({"status": "WAIVERS"}) is None


def test_espn_private_league_falls_back_with_error(monkeypatch):
    denied = requests.Response()
    denied.status_code = 401

    def fake_fetch(season, league_id):
        if league_id == 465846171:
            raise requests.HTTPError("401", response=denied)
        return fixture("espn_rosters.json"), fixture("espn_freeagents.json")

    monkeypatch.setattr(espn, "fetch_raw", fake_fetch)
    monkeypatch.setattr(espn, "write_json", lambda *a, **k: None)
    monkeypatch.delenv("ESPN_S2", raising=False)
    banijska = {"id": "banijska", "name": "Banijska", "teamName": "Barcelona", "espnLeagueId": 465846171,
                "espnTeamId": 5, "manualPlayers": [{"name": "Cooper Flagg", "team": "DAL", "pos": "SF/PF"}]}
    cfg = {"season": 2027, "leagues": [
        {"id": "legendi", "name": "Liga Legendi", "teamName": "DGJ", "espnLeagueId": 2023498222, "espnTeamId": 1},
        banijska]}

    # previous snapshot was the manual list -> rebuilt from config, with the reason attached
    stale_manual = {"id": "banijska", "espnLeagueId": None, "updated": None, "players": [{"name": "Old Guy"}]}
    doc, pools = espn.run(cfg, {"leagues": [stale_manual]})
    legendi, b = doc["leagues"]
    assert "error" not in legendi and list(pools) == ["legendi"]
    assert b["error"].startswith("privatna liga") and [p["name"] for p in b["players"]] == ["Cooper Flagg"]

    # previous snapshot came from ESPN for the same league -> keep that roster
    old_espn = {"id": "banijska", "espnLeagueId": 465846171, "updated": "2026-10-19T06:21:00Z",
                "players": [{"name": "Alperen Sengun"}]}
    doc, _ = espn.run(cfg, {"leagues": [old_espn]})
    assert [p["name"] for p in doc["leagues"][1]["players"]] == ["Alperen Sengun"]
    assert doc["leagues"][1]["error"].startswith("privatna liga")
