"""Waiver recommendations -> site/data/waiver.json.

Heuristic always runs; when ANTHROPIC_API_KEY is set (and config.claude.enabled), Claude re-ranks and rewrites
the picks from the same context. Availability/ownership always comes from ESPN data, never from the model."""
import datetime as dt
import json
import os
import re
import sys

from common import DAY_ACC, data_path, load_config, norm_name, now_iso, read_json, today_et, write_json
from fetch import CBS_URL, is_preseason, is_start_news

POS_NUM = {"PG": 1, "SG": 2, "SF": 3, "PF": 4, "C": 5, "G": 1.5, "F": 3.5}
PRI_NAME = {1: "Uzmi odmah", 2: "Stream / stash", 3: "Špekulacija"}


INJ_HR = {"knee": "koljeno", "ankle": "gležanj", "hamstring": "stražnja loža", "foot": "stopalo", "back": "leđa",
          "hip": "kuk", "calf": "list", "shoulder": "rame", "achilles": "Ahilova tetiva", "groin": "prepone",
          "wrist": "zapešće", "hand": "šaka", "elbow": "lakat", "illness": "bolest", "concussion": "potres mozga",
          "toe": "nožni prst", "undisclosed": "neotkriveno", "thumb": "palac", "finger": "prst",
          "quadriceps": "kvadriceps", "oblique": "kosi trbušni mišić", "abdomen": "trbuh", "neck": "vrat",
          "rest": "odmor", "personal": "osobni razlozi", "suspension": "suspenzija", "head": "glava", "eye": "oko",
          "nose": "nos", "leg": "noga", "shin": "potkoljenica", "heel": "peta", "chest": "prsa", "rib": "rebra",
          "lower body": "donji dio tijela", "upper body": "gornji dio tijela", "pelvis": "zdjelica", "knee soreness": "koljeno"}


def injury_hr(text):
    t = (text or "").strip()
    return INJ_HR.get(t.lower(), t.lower() or "ozljeda")


def d_hr(iso):
    d = dt.date.fromisoformat(iso)
    return f"{d.day}.{d.month}."


def pos_set(pos):
    return {POS_NUM[p] for p in str(pos or "").replace(",", "/").split("/") if p in POS_NUM}


def pos_fit(a, b):
    """1.0 same position, 0.8 adjacent, 0 otherwise."""
    A, B = pos_set(a), pos_set(b)
    if not A or not B:
        return 0.0
    if A & B:
        return 1.0
    return 0.8 if min(abs(x - y) for x in A for y in B) <= 1 else 0.0


class Ctx:
    """Everything the rules need, with lookups across sources."""

    def __init__(self, cfg, injuries, prev_injuries, news, fresh_news, schedule, rosters, pools, today):
        self.cfg, self.today = cfg, today
        self.injuries = (injuries or {}).get("items", [])
        self.prev_out = {norm_name(i["player"]) for i in (prev_injuries or {}).get("items", []) if i["kind"] != "gtd"}
        self.inj_by_name = {norm_name(i["player"]): i for i in self.injuries}
        seen, self.news = set(), []
        for n in list(fresh_news or []) + list((news or {}).get("items", [])):
            k = (n.get("date"), n.get("player"), n.get("headline"))
            if k not in seen:
                seen.add(k)
                self.news.append(n)
        self.schedule = schedule or {}
        self.rosters = (rosters or {}).get("leagues", [])
        self.pools = pools or {}
        self.leagues = cfg["leagues"]
        # one player table; the first league with ESPN data supplies fantasy values
        self.players = {}
        for lc in self.leagues:
            for k, p in self.pools.get(lc["id"], {}).get("players", {}).items():
                self.players.setdefault(int(k), p)
        self.by_name = {}
        for p in self.players.values():
            self.by_name.setdefault(norm_name(p["name"]), []).append(p)
        self.mine = {}  # league id -> set of norm names on my roster
        for L in self.rosters:
            self.mine[L["id"]] = {norm_name(p["name"]) for p in L.get("players", [])}
        self.week = self._current_week()

    # -- lookups
    def lookup(self, name, team=None):
        c = self.by_name.get(norm_name(name), [])
        if team:
            c = [p for p in c if p["team"] == team] or c
        return c[0] if c else None

    def injury(self, name):
        return self.inj_by_name.get(norm_name(name))

    def is_out(self, p):
        inj = self.injury(p["name"])
        if inj:
            return inj["kind"] != "gtd"
        return p.get("injury") in ("OUT", "INJURY_RESERVE", "SUSPENSION")

    def is_gtd(self, p):
        inj = self.injury(p["name"])
        return (inj and inj["kind"] == "gtd") or p.get("injury") == "DAY_TO_DAY"

    def avail(self, name, espn_id):
        out = {}
        for lc in self.leagues:
            pool = self.pools.get(lc["id"])
            if norm_name(name) in self.mine.get(lc["id"], set()):
                out[lc["short"]] = False
            elif pool and espn_id is not None:
                out[lc["short"]] = str(espn_id) not in pool["rostered"]
            else:
                out[lc["short"]] = None
        return out

    def waiver_until(self, league_id, espn_id):
        """Day ESPN processes claims for a player on waivers in this league, if that is after today. He can't be
        added before then and plays for you from that day."""
        d = (self.pools.get(league_id) or {}).get("waivers", {}).get(str(espn_id))
        return d if d and d > self.today.isoformat() else None

    def waivers(self, espn_id):
        out = {}
        for lc in self.leagues:
            d = self.waiver_until(lc["id"], espn_id)
            if d:
                out[lc["short"]] = d
        return out

    def my_leagues(self, name):
        n = norm_name(name)
        return [lc["short"] for lc in self.leagues if n in self.mine.get(lc["id"], set())]

    def free_somewhere(self, p):
        return any(self.pools.get(lc["id"]) and str(p["espnId"]) not in self.pools[lc["id"]]["rostered"]
                   for lc in self.leagues)

    def _current_week(self):
        weeks = self.schedule.get("weeks") or []
        t = self.today.isoformat()
        for w in weeks:
            if w["start"] <= t <= w["end"]:
                return w
        return weeks[0] if weeks and t < weeks[0]["start"] else None

    def team_dates(self, team):
        return (self.week or {}).get("teams", {}).get(team, {}).get("dates", [])

    def team_games(self, team):
        return (self.week or {}).get("teams", {}).get(team, {}).get("n", 0)

    def matchup(self, team, date):
        for g in self.schedule.get("games", []):
            if g["date"] == date and team in (g["visitor"], g["home"]):
                return f"{g['visitor']} @ {g['home']}"
        return team

    def recent_news(self, name, days):
        cut = (self.today - dt.timedelta(days=days)).isoformat()
        n = norm_name(name)
        return [x for x in self.news if norm_name(x["player"]) == n and (x.get("date") or "") >= cut]


def make_pick(ctx, p, name, team, pos, priority, reason, role, horizon, risk, source, note=None):
    espn_id = p["espnId"] if p else None
    pick = {"player": p["name"] if p else name, "team": team or (p or {}).get("team"), "pos": pos or (p or {}).get("pos", ""),
            "priority": priority, "reason": reason, "role": role, "horizon": horizon, "risk": risk,
            "avail": ctx.avail(name, espn_id), "source": source}
    mine = ctx.my_leagues(name)
    if mine:
        pick["note"] = "na tvom rosteru" + ("" if len(mine) == len(ctx.leagues) else f" ({', '.join(mine)})")
    elif note:
        pick["note"] = note
    if espn_id:
        pick["espnId"] = espn_id
        if ctx.waivers(espn_id):
            pick["waivers"] = ctx.waivers(espn_id)
    if p and p.get("fpts"):
        pick["fpts"] = p["fpts"]
        if p.get("gp"):
            pick["gp"], pick["cur"] = p["gp"], p["cur"]
    return pick


PRIOR_HR = {"prošla sezona": "prošle sezone", "projekcija": "ESPN projekcija"}


def fp_text(p):
    """'~25.1 FP po utakmici (ove sezone 28.0 u 2 utk., prošle sezone 23.0; 27 min)'"""
    if not p or not p.get("fpts"):
        return ""
    basis = p.get("basis") or ""
    if basis.startswith("sezona+"):
        prior = PRIOR_HR.get(basis[len("sezona+"):], "prije")
        src = f"ove sezone {p['cur']:.1f} u {p['gp']} utk., {prior} {p['prior']:.1f};"
    elif basis == "sezona":
        src = f"ove sezone{', ' + str(p['gp']) + ' utk.' if p.get('gp') else ''},"
    else:
        src = PRIOR_HR.get(basis, "") + ","
    return f"~{p['fpts']:.1f} FP po utakmici ({src} {p['mpg']:.0f} min)"


# ---------------------------------------------------------------- rule 1: injuries open a role

def cap(s):
    return s[:1].upper() + s[1:]


def sent(s):
    return s if s.endswith((".", "!", "?", "…")) else s + "."


def injury_window(inj):
    """(horizon text, 'is out until ...' phrase)."""
    if inj["kind"] == "out_season":
        return "Do kraja sezone", "out za sezonu"
    if inj.get("ret"):
        return f"Do ~{d_hr(inj['ret'])} (najraniji povratak)", f"out najmanje do {d_hr(inj['ret'])}"
    if inj["kind"] == "out":
        return "Neodređeno — prati status ozljede", "out bez datuma povratka"
    return "Dan-dva, ovisi o statusu", "upitan (GTD)"


def start_news(ctx, name, days=5):
    return [n for n in ctx.recent_news(name, days) if n.get("kind") == "lineup" and is_start_news(n)]


def injury_picks(ctx):
    """Out starters -> best free teammates at the same/adjacent position. One pick per teammate, even when
    several injuries point at him."""
    cut = (ctx.today - dt.timedelta(days=10)).isoformat()
    gtd_cut = (ctx.today - dt.timedelta(days=3)).isoformat()
    cands = {}  # espnId -> {"p", "injs": [(inj, new)], "best": rank among free replacements}
    for inj in ctx.injuries:
        new = norm_name(inj["player"]) not in ctx.prev_out
        ip = ctx.lookup(inj["player"], inj["team"])
        if not ip:
            continue
        if inj["kind"] == "gtd":
            # a doubtful star: his backup is a speculative add while the news is fresh
            if ip["fpts"] < 35 or (inj.get("updated") or "") < gtd_cut:
                continue
        elif not (ip["mpg"] >= 24 or ip["fpts"] >= 28) or (not new and (inj.get("updated") or "") < cut):
            continue
        mates = []
        for p in ctx.players.values():
            if p["team"] != inj["team"] or p["espnId"] == ip["espnId"] or ctx.is_out(p) or p["fpts"] < 12:
                continue
            fit = pos_fit(ip["pos"] or inj["pos"], p["pos"])
            if fit and (ctx.free_somewhere(p) or ctx.my_leagues(p["name"])):
                mates.append((p["fpts"] * fit, p))
        mates.sort(key=lambda x: -x[0])
        for i, (_, p) in enumerate(mates[:2]):
            c = cands.setdefault(p["espnId"], {"p": p, "injs": [], "best": 9})
            c["injs"].append((inj, new))
            c["best"] = min(c["best"], i if inj["kind"] != "gtd" else 2)
    picks = []
    for c in cands.values():
        p, injs = c["p"], sorted(c["injs"], key=lambda x: (x[0]["kind"] == "gtd", x[0].get("ret") or "9999"))
        news = start_news(ctx, p["name"])
        start = [n for n in news if not is_preseason(n)]
        lead = injs[0][0]
        if lead["kind"] == "gtd":
            pri = 3
        else:
            pri = 1 if start else 2 if c["best"] == 0 or news else 3
        start = start or news
        parts = [f"{inj['player']} ({injury_hr(inj['injury'])}) je {injury_window(inj)[1]}"
                 + (" — nova ozljeda" if new and inj["kind"] != "gtd" else "") for inj, new in injs]
        reason = sent("; ".join(parts)) + " " + sent(
            f"{p['name']} je {'prvi' if c['best'] == 0 else 'sljedeći'} {p['pos']} u rotaciji {p['team']}-a "
            f"za te minute; {fp_text(p)}")
        if start:
            reason += " " + sent(f"RotoWire {d_hr(start[0]['date'])}: {start[0]['headline']}")
        names = " i ".join(i["player"] for i, _ in injs[:2])
        verb = "su" if len(injs) > 1 else "je"
        risk = []
        if ctx.is_gtd(p):
            risk.append("i sam je upitan (GTD)")
        risk.append(f"uloga pada kad se vrati {lead['player']}" if lead["kind"] != "out_season"
                    else "klub može dovesti zamjenu ili podijeliti minute")
        if p["fpts"] < 18:
            risk.append("nizak bodovni plafon")
        role = (f"Zamjena ako {names} ne igra" if lead["kind"] == "gtd"
                else ("Starter" if pri == 1 else "Mogući starter" if start else "Veća minutaža")
                + f" dok {verb} out {names}")
        picks.append(make_pick(ctx, p, p["name"], p["team"], p["pos"], pri, reason, role, injury_window(lead)[0],
                               cap("; ".join(risk)), start[0].get("url") if start else CBS_URL))
    return picks

# ---------------------------------------------------------------- rule 2: lineup news "will start"


def lineup_picks(ctx):
    """Start news for a free player. Regular-season starts with real fantasy value are must-adds; preseason
    starts and low-ceiling starters drop to stream/stash or speculation."""
    picks, cut, seen = [], (ctx.today - dt.timedelta(days=4)).isoformat(), set()
    for n in ctx.news:
        if (n.get("date") or "") < cut or n.get("kind") != "lineup" or not is_start_news(n):
            continue
        p = ctx.lookup(n["player"], n.get("team"))
        if not p or p["team"] == "FA" or p["espnId"] in seen:
            continue  # unknown to ESPN, or since released
        if not ctx.free_somewhere(p) and not ctx.my_leagues(p["name"]):
            continue  # rostered by someone else in every league we can see
        pre, fp = is_preseason(n), p["fpts"]
        if pre:
            pri = 2 if fp >= 20 else 3 if fp >= 14 else None
        else:
            pri = 1 if fp >= 18 else 2 if fp >= 12 else 3 if fp >= 8 else None
        if pri is None:
            continue
        seen.add(p["espnId"])
        note = n.get("note", "")
        reason = sent(f"RotoWire {d_hr(n['date'])}: {n['headline']}") + " " + (note[:220] + ("…" if len(note) > 220 else ""))
        if fp:
            reason += " " + sent(cap(fp_text(p)))
        if pre:
            reason += " Predsezonski start — naznaka za postavu na otvaranju, ne garancija."
        risk = (f"I kao starter nizak plafon (~{fp:.0f} FP)" if fp < 18
                else "Starterska uloga ne znači velike minute — provjeri prve 2 utakmice")
        picks.append(make_pick(ctx, p, p["name"], p["team"], p["pos"], pri, reason,
                               "Mogući starter" if pre else "Novi starter", "Dok zadrži mjesto u prvoj petorci", risk,
                               n.get("url") or "https://www.rotowire.com/basketball/news.php"))
    return picks

# ---------------------------------------------------------------- rule 3: streamers for thin days


def active_players(ctx, roster):
    """My players who can actually fill a lineup slot: not on IR and not out."""
    return [p for p in roster["players"] if p.get("slot") != "IR"
            and not (ctx.injury(p["name"]) and ctx.injury(p["name"])["kind"] != "gtd")]


def stream_picks(ctx):
    if not ctx.week:
        return [], {}
    t = ctx.today.isoformat()
    start = max(t, ctx.week["start"])
    days, d = [], dt.date.fromisoformat(start)
    while d.isoformat() <= ctx.week["end"]:
        days.append(d.isoformat())
        d += dt.timedelta(days=1)
    chosen = {}  # espnId -> {"p", "days", "leagues", "counts"}
    thin_by_league = {}
    for lc in ctx.leagues:
        pool = ctx.pools.get(lc["id"])
        roster = next((L for L in ctx.rosters if L["id"] == lc["id"]), None)
        if not pool or not roster:
            continue
        active = active_players(ctx, roster)
        count = {day: sum(1 for p in active if day in ctx.team_dates(p["team"])) for day in days}
        thin = [day for day in days if count[day] <= ctx.cfg.get("streamDayMaxMine", 2)]
        thin_by_league[lc["short"]] = {day: count[day] for day in thin}
        for day in thin:
            cands = []
            for k, p in pool["players"].items():
                if k in pool["rostered"] or p["team"] == "FA" or day not in ctx.team_dates(p["team"]):
                    continue
                if ctx.is_out(p) or p["mpg"] < ctx.cfg.get("streamMinMinutes", 20) or p["fpts"] <= 0:
                    continue
                wd = ctx.waiver_until(lc["id"], p["espnId"]) or ""
                if wd > day:
                    continue  # still on waivers that day
                other = sum(1 for x in thin if x != day and x >= wd and x in ctx.team_dates(p["team"]))
                score = p["fpts"] * (1 + 0.5 * other) * (1.15 if ctx.team_games(p["team"]) >= 4 else 1)
                cands.append((score, p))
            cands.sort(key=lambda x: -x[0])
            per_team, top = set(), []
            for _, p in cands:  # one per NBA team, so a single 4-game team doesn't fill the day
                if p["team"] not in per_team:
                    per_team.add(p["team"])
                    top.append(p)
            for p in top[:ctx.cfg.get("streamersPerDay", 3)]:
                c = chosen.setdefault(p["espnId"], {"p": p, "days": set(), "leagues": set(), "counts": {}})
                c["leagues"].add(lc["short"])
                for x in thin:  # every thin day this player covers, not only the one that picked him
                    if x in ctx.team_dates(p["team"]) and x >= (ctx.waiver_until(lc["id"], p["espnId"]) or ""):
                        c["days"].add(x)
                        c["counts"][x] = min(c["counts"].get(x, 99), count[x])
    picks = []
    for c in chosen.values():
        p, ds = c["p"], sorted(c["days"])
        names = [DAY_ACC[dt.date.fromisoformat(x).weekday()] for x in ds]
        when = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " i " + names[-1]
        games = [f"{DAY_ACC[dt.date.fromisoformat(x).weekday()]} ({ctx.matchup(p['team'], x)}, "
                 f"tvojih igrača: {c['counts'][x]})" for x in ds]
        games = games[0] if len(games) == 1 else ", ".join(games[:-1]) + " i " + games[-1]
        reason = (f"Stream za {games}. {p['name']} je rotacijski {p['pos']}, {fp_text(p)}"
                  + (f"; {p['team']} ima {ctx.team_games(p['team'])} utakmice ovaj tjedan" if ctx.team_games(p["team"]) >= 4 else "")
                  + f". Liga: {', '.join(sorted(c['leagues']))}.")
        risk = ("Upitan (GTD) — provjeri status prije utakmice" if ctx.is_gtd(p)
                else "Nizak plafon, čisti kratkoročni stream" if p["fpts"] < 20
                else "Kratkoročno — nakon utakmice ga možeš pustiti")
        pick = make_pick(ctx, p, p["name"], p["team"], p["pos"], 2, reason, f"Streamer ({p['mpg']:.0f} min)",
                         d_hr(ds[0]) if len(ds) == 1 else f"{d_hr(ds[0])}–{d_hr(ds[-1])}", risk,
                         f"https://www.basketball-reference.com/leagues/NBA_{ctx.cfg['season']}_games.html",
                         note="igra u " + when)
        pick["expires"] = ds[-1]
        picks.append(pick)
    return picks, thin_by_league


def idle(ctx, p):
    """No game this season although the season is a week old: out of the rotation, two-way or G League."""
    start = (ctx.cfg.get("week1") or {}).get("start")
    return not p.get("gp") and bool(start) and (ctx.today - dt.date.fromisoformat(start)).days >= 7


def day_plan(ctx):
    """Who to add for each of the next days, per league.

    A day has room for a streamer when fewer of my healthy players play than there are active lineup slots
    (PG, SG, SF, PF, C, G, F, 3 UTIL = 10). Candidates are free in that league, play that day and are ranked by
    what they score that day alone (FP per game, GTD x 0.75). A player on waivers only counts from the day his claim
    is processed. The week's adds (`weeklyAdds`, minus what ESPN says is already used this week) go one per day to the
    days with the most open slots: that day's first choice joins the plan, fills a slot on his later game days that
    week and is not suggested on later days. Days left without an add still list their best candidates."""
    weeks = ctx.schedule.get("weeks") or []
    plays = {(d, team) for w in weeks for team, x in w["teams"].items() for d in x["dates"]}
    game_days = sorted({d for d, _ in plays})
    first = ctx.today.isoformat()
    last = (ctx.today + dt.timedelta(days=ctx.cfg.get("planDays", 7) - 1)).isoformat()
    days = [d for d in game_days if first <= d <= last]
    week_end = {d: w["end"] for w in weeks for d in game_days if w["start"] <= d <= w["end"]}
    slots, per_day = ctx.cfg.get("activeSlots", 10), ctx.cfg.get("planPerDay", 3)
    out = {d: {"date": d, "games": len({t for x, t in plays if x == d}) // 2, "leagues": []} for d in days}
    for lc in ctx.leagues:
        pool = ctx.pools.get(lc["id"])
        roster = next((L for L in ctx.rosters if L["id"] == lc["id"]), None)
        if not pool or not roster:
            continue
        active = active_players(ctx, roster)
        horizon = [d for d in game_days if first <= d <= max(week_end.get(x, x) for x in days or [first])]
        count = {d: sum(1 for p in active if (d, p["team"]) in plays) for d in horizon}
        open_ = {d: max(0, slots - count[d]) for d in horizon}
        limit = lc.get("weeklyAdds", ctx.cfg.get("weeklyAdds", 7))
        used = (roster.get("adds") or {}).get("used", 0)  # ESPN's count for the week we're in

        def cands(d, planned):
            out_ = []
            for k, p in pool["players"].items():
                if k in pool["rostered"] or p["team"] == "FA" or (d, p["team"]) not in plays:
                    continue
                if (p["espnId"] in planned and planned[p["espnId"]][0] < d) or ctx.is_out(p) or p["fpts"] <= 0:
                    continue  # planned adds are mine from their day on
                if p["mpg"] < ctx.cfg.get("streamMinMinutes", 20) or idle(ctx, p):
                    continue
                wd = ctx.waiver_until(lc["id"], p["espnId"])
                if wd and wd > d:
                    continue  # on waivers until a later day
                rest = [x for x in horizon if d <= x <= week_end[d] and (x, p["team"]) in plays]
                out_.append((bool(ctx.is_gtd(p)), p, rest, wd))
            # a questionable (GTD) player may not suit up: rank him as if worth a quarter less
            out_.sort(key=lambda c: (-c[1]["fpts"] * (0.75 if c[0] else 1), -c[1]["fpts"]))
            return out_

        def simulate(wdays, add_days):
            """One H2H week in order: each add day takes its best candidate, who then fills a slot on his later game
            days. Returns the open slots left and the plan, espnId -> (day added, player)."""
            open_ = {d: max(0, slots - count[d]) for d in horizon}
            planned = {}
            for d in wdays:
                c = cands(d, planned) if d in add_days and open_[d] else []
                if c:
                    planned[c[0][1]["espnId"]] = (d, c[0][1])
                    for x in c[0][2][1:]:
                        open_[x] = max(0, open_[x] - 1)
            return open_, planned

        for wk in sorted({week_end[d] for d in days}):
            wdays = [d for d in days if week_end[d] == wk]
            budget = max(0, limit - used) if ctx.week and wk == ctx.week["end"] else limit
            add_days = []
            open_, planned = simulate(wdays, add_days)
            todo = list(wdays)
            while len(add_days) < budget:
                todo = [d for d in todo if open_[d]]
                if not todo:
                    break
                d = max(todo, key=lambda x: (open_[x], -wdays.index(x)))  # most open slots, then the earlier day
                todo.remove(d)
                o, pl = simulate(wdays, add_days + [d])
                got = {since for since, _ in pl.values()}
                if d in got:
                    add_days, open_, planned = sorted(got), o, pl
            for d in wdays:
                adds = budget - sum(1 for x in add_days if x < d)
                entry = {"league": lc["short"], "mine": count[d], "open": open_[d],
                         "sitting": max(0, count[d] - slots), "adds": adds, "addsLimit": limit,
                         "add": d in add_days, "addDays": add_days,
                         "planned": [{"player": p["name"], "team": p["team"], "from": since}
                                     for since, p in planned.values() if since < d and (d, p["team"]) in plays],
                         "picks": []}
                out[d]["leagues"].append(entry)
                if not open_[d] or not adds:
                    continue
                for gtd, p, rest, wd in cands(d, planned)[:per_day]:
                    pick = {"player": p["name"], "team": p["team"], "pos": p["pos"], "espnId": p["espnId"],
                            "fpts": p["fpts"], "mpg": p["mpg"], "basis": p["basis"], "gp": p.get("gp", 0),
                            "cur": p.get("cur"), "matchup": ctx.matchup(p["team"], d), "dates": rest,
                            "value": round(p["fpts"] * (0.75 if gtd else 1), 1), "gtd": gtd}
                    if wd:
                        pick["waiver"] = wd
                    entry["picks"].append(pick)
    return [out[d] for d in days]

# ---------------------------------------------------------------- combine


def merge(picks):
    out = {}
    for p in picks:
        k = norm_name(p["player"])
        if k not in out:
            out[k] = p
            continue
        q = out[k]
        if p["priority"] < q["priority"]:
            p, q = q, p
            out[k] = q
        for s in re.split(r"(?<=[.!?…])\s+(?=[A-ZČĆŠŽĐ~])", p["reason"]):
            if s and s not in q["reason"]:
                q["reason"] += " " + s
        if p.get("expires") and not q.get("expires"):
            q["expires"] = p["expires"]
        if p.get("note") and not q.get("note"):
            q["note"] = p["note"]
    return list(out.values())


def actionable(pick):
    """Free (or unknown) in at least one league. A player who is mine or taken in every league is no waiver move;
    his news already shows under Moji igrači."""
    avail = pick.get("avail") or {}
    return not avail or any(v is not False for v in avail.values())


def rank(picks, limit):
    picks = [p for p in picks if actionable(p)]
    picks.sort(key=lambda p: (p["priority"], -(p.get("fpts") or 0)))
    return picks[:limit]


def heuristic(ctx):
    streams, thin = stream_picks(ctx)
    return rank(merge(lineup_picks(ctx) + injury_picks(ctx) + streams), ctx.cfg.get("maxPicks", 30)), thin

# ---------------------------------------------------------------- Claude (optional)


PICK_SCHEMA = {
    "type": "object",
    "properties": {"picks": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "player": {"type": "string"}, "team": {"type": "string"}, "pos": {"type": "string"},
            "priority": {"type": "integer", "enum": [1, 2, 3]},
            "reason": {"type": "string"}, "role": {"type": "string"}, "horizon": {"type": "string"},
            "risk": {"type": "string"}, "note": {"type": "string"}, "espnId": {"type": "integer"}},
        "required": ["player", "team", "pos", "priority", "reason", "role", "horizon", "risk", "note", "espnId"],
        "additionalProperties": False}}},
    "required": ["picks"],
    "additionalProperties": False,
}

SYSTEM = """Ti si analitičar za ESPN fantasy NBA (head-to-head POINTS: PTS 1, REB 1, AST 2, STL 4, BLK 4, 3PM 1, \
FGM 2, FGA -1, FTM 1, FTA -1, TO -2; roster PG, SG, SF, PF, C, G, F, 3 UTIL, 3 klupa, 1 IR; max 4 centra).
Iz priloženih podataka (ozljede CBS, vijesti RotoWire, raspored, moji rosteri, slobodni igrači s ESPN procjenom \
fantasy bodova po utakmici, heuristički prijedlozi) sastavi konačnu listu waiver preporuka.
Pravila:
- Predlaži samo igrače iz `freeAgents`, `heuristic` ili vijesti; espnId prepiši iz podataka (0 ako ga nema).
- `fpts` je procjena koja miješa ovu sezonu (`fptsThisSeason` u `gamesThisSeason` utakmica) s prošlom sezonom ili \
ESPN projekcijom (`fptsPrior`); što više utakmica, to više vrijedi ova sezona. Nagli skok ili pad minuta ove sezone \
je važan signal uloge.
- `waivers` {liga: datum}: igrač je u toj ligi na waiversu, može se samo claimati i igra za mene tek od tog datuma — \
to napiši u risk. Svaka liga ima `weeklyAdds` preuzimanja po H2H tjednu, `addsUsed` je već iskorišteno.
- priority 1 = uzmi odmah (novi starter, velika uloga zbog ozljede), 2 = stream/stash (kratkoročno ili prazni dani \
u tjednu), 3 = špekulacija.
- Streameri: dani kad imam ≤ 2 igrača u akciji (`thinDays`) — prednost igračima čiji timovi igraju više takvih dana \
i timovima s 4 utakmice; za njih note = "igra u <dan>" (npr. "igra u utorak"), inače note = "".
- reason, role, horizon i risk piši kratko i konkretno na hrvatskom; navedi ozlijeđenog suigrača, datum povratka i \
procjenu bodova kad je bitno. Ne izmišljaj činjenice kojih nema u podacima.
- Najviše {limit} preporuka, poredanih po važnosti."""


def claude_context(ctx, heur, thin):
    cut = (ctx.today - dt.timedelta(days=5)).isoformat()
    fa = []
    for p in sorted(ctx.players.values(), key=lambda p: -p["fpts"]):
        if p["team"] == "FA" or p["fpts"] < 12 or not ctx.free_somewhere(p):
            continue
        fa.append({"espnId": p["espnId"], "name": p["name"], "team": p["team"], "pos": p["pos"], "fpts": p["fpts"],
                   "mpg": p["mpg"], "basis": p["basis"], "gamesThisSeason": p.get("gp", 0),
                   "fptsThisSeason": p.get("cur"), "fptsPrior": p.get("prior"), "espnInjury": p["injury"],
                   "gamesThisWeek": ctx.team_games(p["team"]), "dates": ctx.team_dates(p["team"]),
                   "avail": ctx.avail(p["name"], p["espnId"]), "waivers": ctx.waivers(p["espnId"])})
        if len(fa) >= 90:
            break
    return {
        "today": ctx.today.isoformat(),
        "week": {k: (ctx.week or {}).get(k) for k in ("label", "start", "end")},
        "thinDays": thin,
        "myRosters": [{"league": L["name"], "addsUsed": (L.get("adds") or {}).get("used"),
                       "weeklyAdds": next((lc.get("weeklyAdds") for lc in ctx.leagues if lc["id"] == L["id"]), None)
                       or ctx.cfg.get("weeklyAdds", 7),
                       "players": [dict(p, injury=(ctx.injury(p["name"]) or {}).get("status"))
                                   for p in L.get("players", [])]}
                      for L in ctx.rosters],
        "injuries": [i for i in ctx.injuries if i["kind"] != "gtd" or (i.get("updated") or "") >= cut],
        "news": [{k: n.get(k) for k in ("date", "team", "player", "headline", "note", "kind")}
                 for n in ctx.news if (n.get("date") or "") >= cut][:120],
        "freeAgents": fa,
        "heuristic": heur,
    }


def claude_picks(ctx, heur, thin):
    cc = ctx.cfg.get("claude") or {}
    if not cc.get("enabled") or not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    import anthropic
    limit = ctx.cfg.get("maxPicks", 30)
    client = anthropic.Anthropic()
    try:
        msg = client.beta.messages.create(
            model=cc.get("model", "claude-sonnet-5-5"),
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=SYSTEM.replace("{limit}", str(limit)),
            messages=[{"role": "user", "content": json.dumps(claude_context(ctx, heur, thin), ensure_ascii=False)}],
            output_config={"format": {"type": "json_schema", "schema": PICK_SCHEMA}},
        )
    except anthropic.APIStatusError as e:
        print(f"  ! Claude API {e.status_code}: {e.message}", file=sys.stderr)
        return None
    except anthropic.APIConnectionError as e:
        print(f"  ! Claude API connection: {e}", file=sys.stderr)
        return None
    if msg.stop_reason != "end_turn":
        print(f"  ! Claude stop_reason={msg.stop_reason} ({msg._request_id})", file=sys.stderr)
        return None
    text = next((b.text for b in msg.content if b.type == "text"), "")
    raw = json.loads(text).get("picks", [])
    by_heur = {norm_name(p["player"]): p for p in heur}
    out = []
    for r in raw:
        p = ctx.players.get(r.get("espnId")) or ctx.lookup(r["player"], r.get("team"))
        h = by_heur.get(norm_name(r["player"]))
        if not p and not h and not ctx.recent_news(r["player"], 7):
            continue  # unknown player: don't trust it
        pick = make_pick(ctx, p, r["player"], r.get("team"), r.get("pos"), r["priority"], r["reason"], r["role"],
                         r["horizon"], r["risk"], (h or {}).get("source") or CBS_URL, note=r.get("note") or None)
        if h and h.get("expires"):
            pick["expires"] = h["expires"]
        out.append(pick)
    return rank(out, limit) if len(out) >= 3 else None


def run(cfg, injuries, prev_injuries, news, fresh_news, schedule, rosters, pools, today=None):
    ctx = Ctx(cfg, injuries, prev_injuries, news, fresh_news, schedule, rosters, pools, today or today_et())
    picks, thin = heuristic(ctx)
    engine = "heuristika"
    ai = claude_picks(ctx, picks, thin)
    if ai:
        picks, engine = ai, "claude"
    doc = {"updated": now_iso(), "engine": engine, "picks": picks, "days": day_plan(ctx)}
    write_json(data_path("waiver.json"), doc)
    return doc


if __name__ == "__main__":
    from espn import load_pools
    rd = lambda n: read_json(data_path(n))  # noqa: E731
    d = run(load_config(), rd("injuries.json"), None, rd("news.json"), [], rd("schedule.json"), rd("rosters.json"),
            load_pools())
    for p in d["picks"]:
        print(p["priority"], p["player"], p["team"], p.get("fpts"), p.get("note", ""), "|", p["reason"][:110])
