"""Waiver heuristics and Telegram message on small synthetic snapshots."""
import datetime as dt

import analyze
import notify

TODAY = dt.date(2026, 10, 20)  # Tuesday of week 1
CFG = {"season": 2027, "streamDayMaxMine": 2, "streamersPerDay": 3, "streamMinMinutes": 20, "maxPicks": 30,
       "claude": {"enabled": False},
       "leagues": [{"id": "L", "short": "Legendi", "name": "Liga"}, {"id": "B", "short": "Banijska", "name": "B"}]}


def player(pid, name, team, pos, fpts, mpg=25, injury="ACTIVE", basis="prošla sezona"):
    return {"espnId": pid, "name": name, "team": team, "pos": pos, "posId": 1, "injury": injury, "fpts": fpts,
            "mpg": mpg, "basis": basis, "owned": 1.0}


PLAYERS = [
    player(1, "Bradley Beal", "LAC", "SG", 30, 31),
    player(2, "Kris Dunn", "LAC", "PG/SG", 22, 27),
    player(3, "Keaton Wagler", "LAC", "PG/SG", 24, 28),      # rostered by another team
    player(4, "Ivica Zubac", "LAC", "C", 35, 32),            # mine
    player(5, "Bench Guy", "LAC", "SG", 9, 8),               # too small to matter
    player(10, "Stream Star", "NYK", "SF", 24, 29),
    player(11, "Stream Two", "PHI", "PG", 21, 26),
    player(12, "Deep Bench", "NYK", "C", 15, 12),            # under streamMinMinutes
    player(13, "Rostered Knick", "NYK", "PG", 40, 34),
    player(14, "Second Knick", "NYK", "SG", 22, 25),         # one-per-team cap keeps him out
    player(20, "Lineup Free", "MIA", "PF", 20, 24),
    player(21, "Lineup Low", "MIA", "SG", 15, 20),
    player(22, "Lineup Taken", "MIA", "C", 30, 30),
]
POOL = {"myTeamId": 1, "teams": {"1": "Me", "2": "Other"},
        "rostered": {"3": 2, "4": 1, "13": 2, "22": 2, "30": 1},
        "players": {str(p["espnId"]): p for p in PLAYERS}}
ROSTERS = {"leagues": [
    {"id": "L", "name": "Liga", "players": [
        {"name": "Ivica Zubac", "team": "LAC", "pos": "C", "slot": "C", "espnId": 4},
        {"name": "Injured Mine", "team": "BOS", "pos": "PG", "slot": "IR", "espnId": 30}]},
    {"id": "B", "name": "B", "players": [{"name": "Kris Dunn", "team": "LAC", "pos": "PG"}]},
]}
WEEK = {"label": "Tjedan 1", "start": "2026-10-20", "end": "2026-10-25", "teams": {
    "LAC": {"n": 3, "dates": ["2026-10-21", "2026-10-23", "2026-10-25"]},
    "BOS": {"n": 2, "dates": ["2026-10-20", "2026-10-23"]},
    "NYK": {"n": 3, "dates": ["2026-10-20", "2026-10-22", "2026-10-24"]},
    "PHI": {"n": 4, "dates": ["2026-10-20", "2026-10-22", "2026-10-24", "2026-10-25"]},
    "MIA": {"n": 3, "dates": ["2026-10-21", "2026-10-23", "2026-10-25"]}}}
SCHEDULE = {"weeks": [WEEK], "games": [{"date": "2026-10-20", "visitor": "PHI", "home": "NYK"}]}
BEAL_OUT = {"team": "LAC", "player": "Bradley Beal", "pos": "SG", "updated": "2026-10-19", "injury": "Knee",
            "status": "Expected return: at least Nov 8", "kind": "out", "ret": "2026-11-08"}


def ctx(injuries=(), news=(), prev_injuries=()):
    return analyze.Ctx(CFG, {"items": list(injuries)}, {"items": list(prev_injuries)}, {"items": []}, list(news),
                       SCHEDULE, ROSTERS, {"L": POOL}, TODAY)


def news(player, headline, note, date="2026-10-19", team="MIA"):
    return {"date": date, "team": team, "player": player, "headline": headline, "note": note, "kind": "lineup",
            "source": "RotoWire"}


def by_name(picks):
    return {p["player"]: p for p in picks}


def test_injury_opens_role_for_best_free_teammate():
    picks = by_name(analyze.injury_picks(ctx([BEAL_OUT])))
    assert "Keaton Wagler" not in picks          # rostered elsewhere in the only ESPN league
    assert "Bench Guy" not in picks
    dunn = picks["Kris Dunn"]
    assert dunn["priority"] == 2
    assert "Bradley Beal (koljeno) je out najmanje do 8.11. — nova ozljeda" in dunn["reason"]
    assert dunn["horizon"].startswith("Do ~8.11.")
    assert dunn["avail"] == {"Legendi": True, "Banijska": False}   # manual roster in the other league
    assert dunn["note"] == "na tvom rosteru (Banijska)"


def test_injury_with_start_news_is_must_add():
    n = news("Kris Dunn", "Will start Wednesday", "Dunn will start Wednesday with Beal out.", team="LAC")
    dunn = by_name(analyze.injury_picks(ctx([BEAL_OUT], [n])))["Kris Dunn"]
    assert dunn["priority"] == 1 and dunn["role"].startswith("Starter")


def test_old_known_injury_is_skipped():
    old = dict(BEAL_OUT, updated="2026-09-01")
    assert analyze.injury_picks(ctx([old], prev_injuries=[old])) == []
    assert analyze.injury_picks(ctx([old])) != []  # ...unless it is new since the last snapshot


def test_lineup_news_rules():
    items = [
        news("Lineup Free", "Will start Wednesday", "Free will start Wednesday's opener."),
        news("Lineup Low", "Starting in preseason finale", "Low will start Friday's preseason finale."),
        news("Lineup Taken", "Will start Wednesday", "Taken will start Wednesday."),
        news("Second Knick", "Not listed as starter", "Knick is not in the starting lineup.", team="NYK"),
        news("Old News", "Will start", "Old will start.", date="2026-10-01"),
    ]
    picks = by_name(analyze.lineup_picks(ctx(news=items)))
    assert set(picks) == {"Lineup Free", "Lineup Low"}
    assert picks["Lineup Free"]["priority"] == 1
    assert picks["Lineup Low"]["priority"] == 3 and picks["Lineup Low"]["role"] == "Mogući starter"


def test_streamers_fill_thin_days():
    picks, thin = analyze.stream_picks(ctx())
    # IR player ignored: on Tuesday nobody of mine plays; LAC days have 1 player (Zubac) -> also thin
    assert thin["Legendi"]["2026-10-20"] == 0
    p = by_name(picks)
    assert "Deep Bench" not in p and "Rostered Knick" not in p
    assert p["Stream Star"]["note"].startswith("igra u utorak")
    assert p["Stream Star"]["expires"] == "2026-10-24"
    assert p["Stream Star"]["priority"] == 2
    assert "PHI @ NYK" in p["Stream Star"]["reason"]
    # one per NBA team per day: the better Knick wins the Tuesday slot
    assert "Second Knick" not in p or "utorak" not in p["Second Knick"]["note"]


def test_heuristic_ranks_by_priority_then_points():
    n = news("Lineup Free", "Will start Wednesday", "Free will start Wednesday's opener.")
    picks, _ = analyze.heuristic(ctx([BEAL_OUT], [n]))
    assert [p["priority"] for p in picks] == sorted(p["priority"] for p in picks)
    assert picks[0]["player"] == "Lineup Free"
    names = [p["player"] for p in picks]
    assert len(names) == len(set(names))


def test_merge_combines_reasons_without_repeating():
    a = {"player": "X", "priority": 2, "reason": "Prvi razlog. Drugi.", "note": "igra u utorak", "expires": "2026-10-20"}
    b = {"player": "X", "priority": 1, "reason": "Prvi razlog. Treći."}
    (m,) = analyze.merge([a, b])
    assert m["priority"] == 1 and m["reason"] == "Prvi razlog. Treći. Drugi."
    assert m["note"] == "igra u utorak" and m["expires"] == "2026-10-20"


def test_notify_compose():
    pick = {"player": "Kris Dunn", "team": "LAC", "priority": 1, "role": "Starter dok je out Bradley Beal",
            "reason": "...", "note": ""}
    prev = {"waiver.json": {"picks": []}, "injuries.json": {"items": []}}
    cur = {"waiver.json": {"picks": [pick]}, "rosters.json": ROSTERS,
           "injuries.json": {"items": [dict(BEAL_OUT, player="Ivica Zubac", injury="Ankle")]}}
    text = notify.compose({"siteUrl": "https://x.github.io/waiver-radar/"}, prev, cur)
    assert "Novi starteri / prilike:\n• Kris Dunn (LAC)" in text
    assert "Moji igrači:\n• Ivica Zubac (LAC): out do ~8.11. (gležanj)" in text
    assert "Top waiver:\n1. Kris Dunn (LAC) · Uzmi odmah" in text
    assert text.endswith("https://x.github.io/waiver-radar/")
    # nothing new -> no message; first run -> no message
    assert notify.compose({}, {"waiver.json": {"picks": [pick]}, "injuries.json": cur["injuries.json"]}, cur) is None
    assert notify.compose({}, {}, cur) is None


def test_rank_drops_players_with_no_league_to_add_them_in():
    def mk(name, avail):
        return {"player": name, "priority": 2, "avail": avail, "fpts": 20}
    picks = [mk("Mine Or Taken Everywhere", {"Legendi": False, "Banijska": False}),
             mk("Unknown In One", {"Legendi": False, "Banijska": None}),
             mk("Free", {"Legendi": True, "Banijska": False})]
    assert [p["player"] for p in analyze.rank(picks, 30)] == ["Unknown In One", "Free"]


def test_day_plan_ranks_by_points_left_this_week_and_carries_the_plan():
    days = {d["date"]: d for d in analyze.day_plan(ctx())}
    assert list(days) == ["2026-10-20", "2026-10-21", "2026-10-22", "2026-10-23", "2026-10-24", "2026-10-25"]
    assert [x["league"] for x in days["2026-10-20"]["leagues"]] == ["Legendi"]   # Banijska has no ESPN pool here
    tue = days["2026-10-20"]["leagues"][0]
    assert (tue["mine"], tue["open"]) == (0, 10)
    # PHI plays 4 of the remaining days, NYK 3: 21 FP x 4 beats 24 FP x 3
    assert [(p["player"], p["value"]) for p in tue["picks"]] == [("Stream Two", 84), ("Stream Star", 72),
                                                               ("Second Knick", 66)]
    assert tue["picks"][0]["dates"] == ["2026-10-20", "2026-10-22", "2026-10-24", "2026-10-25"]
    assert tue["picks"][0]["matchup"] == "PHI @ NYK"
    wed = days["2026-10-21"]["leagues"][0]
    assert wed["mine"] == 1 and wed["picks"][0]["player"] == "Bradley Beal"    # rostered players never show up
    assert all(p["player"] != "Keaton Wagler" for d in days.values() for p in d["leagues"][0]["picks"])
    thu = days["2026-10-22"]["leagues"][0]
    assert thu["planned"] == [{"player": "Stream Two", "team": "PHI", "from": "2026-10-20"}]
    assert "Stream Two" not in [p["player"] for p in thu["picks"]] and thu["open"] == 9


def test_waiver_players_only_count_from_their_claim_day():
    pool = dict(POOL, waivers={"11": "2026-10-22", "10": "2026-10-20"})  # Stream Star's claim ran this morning
    c = analyze.Ctx(CFG, {"items": []}, {"items": []}, {"items": []}, [], SCHEDULE, ROSTERS, {"L": pool}, TODAY)
    days = {d["date"]: d["leagues"][0] for d in analyze.day_plan(c)}
    assert "Stream Two" not in [p["player"] for p in days["2026-10-20"]["picks"]]
    star = days["2026-10-20"]["picks"][0]
    assert star["player"] == "Stream Star" and "waiver" not in star
    two = next(p for p in days["2026-10-22"]["picks"] if p["player"] == "Stream Two")
    assert two["waiver"] == "2026-10-22" and two["dates"] == ["2026-10-22", "2026-10-24", "2026-10-25"]
    streams = by_name(analyze.stream_picks(c)[0])
    assert "utorak" not in streams.get("Stream Two", {}).get("note", "")
    pick = analyze.make_pick(c, POOL["players"]["11"], "Stream Two", "PHI", "PG", 2, "", "", "", "", "")
    assert pick["waivers"] == {"Legendi": "2026-10-22"} and pick["avail"]["Legendi"] is True


def test_day_plan_stops_when_the_weeks_adds_are_used():
    rosters = {"leagues": [dict(ROSTERS["leagues"][0], adds={"period": 1, "used": 5})]}
    c = analyze.Ctx(dict(CFG, weeklyAdds=7), {"items": []}, {"items": []}, {"items": []}, [], SCHEDULE, rosters,
                    {"L": POOL}, TODAY)
    plan = [(d["date"], d["leagues"][0]) for d in analyze.day_plan(c)]
    assert [L["adds"] for _, L in plan] == [2, 1, 0, 0, 0, 0]
    assert all(L["picks"] == [] for _, L in plan[2:])
    assert [x["player"] for x in plan[2][1]["planned"]] == ["Stream Two"]   # planned adds still play


def test_fp_text_shows_both_seasons():
    p = {"fpts": 25.1, "mpg": 27.4, "basis": "sezona+prošla sezona", "gp": 2, "cur": 28.0, "prior": 23.0}
    assert analyze.fp_text(p) == "~25.1 FP po utakmici (ove sezone 28.0 u 2 utk., prošle sezone 23.0; 27 min)"
    assert analyze.fp_text(dict(p, basis="prošla sezona", gp=0)) == "~25.1 FP po utakmici (prošle sezone, 27 min)"
    assert analyze.fp_text(dict(p, basis="sezona")) == "~25.1 FP po utakmici (ove sezone, 2 utk., 27 min)"


def test_day_plan_skips_full_days():
    full = analyze.Ctx(dict(CFG, activeSlots=1), {"items": []}, {"items": []}, {"items": []}, [], SCHEDULE, ROSTERS,
                       {"L": POOL}, TODAY)
    wed = next(d for d in analyze.day_plan(full) if d["date"] == "2026-10-21")["leagues"][0]
    assert (wed["mine"], wed["open"], wed["picks"]) == (1, 0, [])
