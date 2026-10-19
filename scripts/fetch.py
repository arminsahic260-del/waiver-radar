"""Scrape injuries (CBS), news (RotoWire) and schedule (Basketball-Reference) into site/data/*.json."""
import datetime as dt
import re
import sys
import time

from bs4 import BeautifulSoup

from common import (ABBRS, data_path, get, load_config, now_iso, parse_month_day, read_json, team_abbr,
                    today_et, write_json)

CBS_URL = "https://www.cbssports.com/nba/injuries/"
ROTO_URL = "https://www.rotowire.com/basketball/news.php"
BREF_URL = "https://www.basketball-reference.com/leagues/NBA_{season}_games-{month}.html"

# ---------------------------------------------------------------- injuries (CBS)

KIND_ORDER = {"out_season": 0, "out": 1, "gtd": 2}


def classify_injury(status, season):
    """CBS status text -> (normalised status, kind, return date)."""
    s = (status or "").strip()
    low = s.lower()
    if "season" in low:
        return "Out for season", "out_season", None
    if "expected" in low or low.startswith("out"):
        ret = parse_month_day(s, season)
        m = re.search(r"\b([A-Z][a-z]{2})[a-z]*\.?\s+(\d{1,2})\b", s)
        if ret and m:
            return f"Expected return: {'at least ' if 'at least' in low else ''}{m.group(1)} {m.group(2)}", "out", ret
        return s or "Out", "out", None
    if any(k in low for k in ("game time", "gtd", "day-to-day", "questionable", "probable", "doubtful")):
        return "GTD", "gtd", None
    return s or "Out", "out", None


def parse_cbs_injuries(html, season):
    soup = BeautifulSoup(html, "html.parser")
    items = []
    for wrap in soup.select("div.TableBaseWrapper"):
        tn = wrap.select_one(".TeamName a") or wrap.select_one(".TeamName")
        team = team_abbr(tn.get_text(strip=True)) if tn else None
        if not team and tn is not None and tn.get("href"):
            m = re.search(r"/teams/([A-Z]+)/", tn["href"])
            team = team_abbr(m.group(1)) if m else None
        if not team:
            continue
        for tr in wrap.select("table tbody tr"):
            tds = tr.find_all("td")
            if len(tds) < 5:
                continue
            long_name = tds[0].select_one(".CellPlayerName--long")
            player = (long_name or tds[0]).get_text(" ", strip=True)
            status, kind, ret = classify_injury(tds[4].get_text(" ", strip=True), season)
            items.append({
                "team": team,
                "player": player,
                "pos": tds[1].get_text(strip=True),
                "updated": parse_month_day(tds[2].get_text(" ", strip=True), season),
                "injury": tds[3].get_text(" ", strip=True),
                "status": status,
                "kind": kind,
                "ret": ret,
            })
    items.sort(key=lambda x: (x["team"], KIND_ORDER[x["kind"]], x["player"]))
    return items


def fetch_injuries(cfg):
    items = parse_cbs_injuries(get(CBS_URL).text, cfg["season"])
    if not items:
        raise RuntimeError("CBS: no injury rows parsed (layout change?)")
    doc = {"updated": now_iso(), "source": "CBS Sports", "items": items}
    write_json(data_path("injuries.json"), doc)
    return doc

# ---------------------------------------------------------------- news (RotoWire)


TRANSACTION = re.compile(r"\b(extension|waive[sd]?|signs?|inks?|deal|trade[sd]?|release[sd]?|cut|claimed|"
                         r"two-way|contract|acquire[sd]?|re-signs?|exercise[sd]? option)\b", re.I)
INJURY_HEAD = re.compile(r"injur|out for|sidelined|surgery|tear|torn|sprain|strain|fracture|day-to-day|"
                         r"questionable|doubtful|uncertain|ruled out|won't play|will miss|misses|exits|leaves|hurt", re.I)
LINEUP = re.compile(r"will start|starting lineup|named (the )?starter|\bstart|lineup|minutes|rotation|role|bench|"
                    r"practice|ramp|workload|restriction", re.I)
INJURY_ANY = re.compile(r"injur|tear|torn|sprain|hurt|day-to-day|out for|surgery|fracture|strain", re.I)

START = re.compile(r"\b(will start|to start|set to start|expected to start|slated to start|starting lineup|"
                   r"named (the )?starter|named starting|enters? (the )?starting|gets? the start|draws? (the )?start|"
                   r"(will|to) (draw|get) the start|start(s|ed)? (at|in place|for)|starting five|joins? (the )?starting|"
                   r"in the starting|moves? into (the )?starting|promoted to (the )?starting)", re.I)
NOT_START = re.compile(r"\b(won't start|will not start|not (expected to )?start|not starting|"
                       r"(isn't|is not|not|wasn't|was not) (listed )?in the starting|not listed as (a )?starter|"
                       r"out of the starting|off the bench|coming off|come off|bench role|demoted)", re.I)
TO_BENCH = re.compile(r"\b(to|back to) (the )?bench|bench\b", re.I)


def classify_news(headline, note):
    text = f"{headline} {note}"
    if TRANSACTION.search(headline):
        return "transaction"
    if INJURY_HEAD.search(headline) and not START.search(text):
        return "injury"
    if LINEUP.search(text):
        return "lineup"
    if INJURY_ANY.search(text):
        return "injury"
    return "other"


def is_start_news(item):
    """News saying the player moves into the starting five (negations checked in the headline and the note's
    first sentence, which is about the player; later sentences often mention whoever loses the spot)."""
    head = item.get("headline", "")
    first = re.split(r"(?<=[.!?])\s", item.get("note", ""), maxsplit=1)[0]
    if NOT_START.search(f"{head} {first}") or TO_BENCH.search(head):
        return False
    return bool(START.search(f"{head} {first}"))


def is_preseason(item):
    return "preseason" in f"{item.get('headline', '')} {item.get('note', '')}".lower()


def parse_rotowire_news(html):
    soup = BeautifulSoup(html, "html.parser")
    items = []
    for it in soup.select(".news-update"):
        p = it.select_one(".news-update__player-link")
        h = it.select_one(".news-update__headline")
        ts = it.select_one(".news-update__timestamp")
        if not (p and h and ts):
            continue
        meta = it.select_one(".news-update__meta")
        team = None
        if meta:
            first = meta.find("div")
            if first:
                pos = first.select_one(".news-update__pos")
                if pos:
                    pos.extract()
                team = team_abbr(first.get_text(" ", strip=True))
        if not team:
            logo = it.select_one(".news-update__logo")
            team = team_abbr(logo.get("alt")) if logo else None
        date = parse_month_day(ts.get_text(" ", strip=True), 0)
        body = it.select_one(".news-update__news")
        note = re.sub(r"\s+", " ", body.get_text(" ", strip=True)) if body else ""
        note = re.sub(r"\s+([,.;:!?])", r"\1", note)
        headline = h.get_text(" ", strip=True)
        href = h.get("href") or ""
        items.append({
            "date": date,
            "team": team or "FA",
            "player": p.get_text(" ", strip=True),
            "headline": headline,
            "note": note,
            "kind": classify_news(headline, note),
            "source": "RotoWire",
            "url": ("https://www.rotowire.com" + href) if href.startswith("/") else href,
        })
    return items


def news_key(i):
    return (i.get("date"), i.get("player"), i.get("headline"))


def merge_news(new_items, old_items, keep):
    """Newest first, dedupe on date+player+headline, keep `keep` items."""
    seen, out = set(), []
    for i in list(new_items) + list(old_items or []):
        k = news_key(i)
        if k in seen or not i.get("date"):
            continue
        seen.add(k)
        out.append(i)
    out.sort(key=lambda i: i["date"], reverse=True)  # stable: page order kept within a day
    return out[:keep]


def fetch_news(cfg, prev):
    urls = [ROTO_URL, ROTO_URL + "?view=injuries"]
    if cfg.get("rotowireTeamPages"):
        urls += [f"{ROTO_URL}?team={t}" for t in ABBRS]
    fresh, errors = [], 0
    for u in urls:
        try:
            fresh += parse_rotowire_news(get(u).text)
        except Exception as e:  # one bad team page must not sink the run
            errors += 1
            print(f"  ! {u}: {e}", file=sys.stderr)
        time.sleep(0.5)
    if not fresh:
        raise RuntimeError(f"RotoWire: nothing parsed ({errors} failed requests)")
    fresh = merge_news(fresh, [], 10_000)
    doc = {"updated": now_iso(), "items": merge_news(fresh, (prev or {}).get("items"), cfg.get("newsKeep", 60))}
    write_json(data_path("news.json"), doc)
    return doc, fresh

# ---------------------------------------------------------------- schedule (Basketball-Reference)


MONTH_NAMES = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
               "november", "december"]


def parse_bref_schedule(html):
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", id="schedule")
    if not table:
        return []
    games = []
    for tr in table.select("tbody tr"):
        th = tr.find("th", attrs={"data-stat": "date_game"})
        v = tr.find("td", attrs={"data-stat": "visitor_team_name"})
        h = tr.find("td", attrs={"data-stat": "home_team_name"})
        if not (th and v and h):
            continue
        date = None
        m = re.match(r"(\d{4})(\d{2})(\d{2})", th.get("csk") or "")
        if m:
            date = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
        else:
            date = parse_month_day(th.get_text(" ", strip=True), 0)

        def abbr(td):
            a = td.find("a")
            code = re.search(r"/teams/([A-Z]{3})/", a["href"]).group(1) if a and a.get("href") else None
            return team_abbr(code) or team_abbr(td.get_text(strip=True))
        vis, home = abbr(v), abbr(h)
        if date and vis and home:
            games.append({"date": date, "visitor": vis, "home": home})
    return games


def h2h_weeks(cfg):
    """All ESPN H2H weeks: week 1 is the opening (Tue-Sun) week, then Mon-Sun."""
    start = dt.date.fromisoformat(cfg["week1"]["start"])
    end = dt.date.fromisoformat(cfg["week1"]["end"])
    weeks, n = [], 1
    season_end = dt.date(cfg["season"], 4, 30)
    while start <= season_end:
        weeks.append((n, start, end))
        n, start, end = n + 1, end + dt.timedelta(days=1), end + dt.timedelta(days=7)
    return weeks


def visible_weeks(cfg, today):
    weeks = h2h_weeks(cfg)
    idx = next((i for i, (_, a, b) in enumerate(weeks) if a <= today <= b), None)
    if idx is None:
        idx = 0 if today < weeks[0][1] else len(weeks) - 1
    return weeks[idx: idx + cfg.get("scheduleWeeks", 3)]


def build_schedule_doc(games, weeks):
    out, keep = [], []
    for n, a, b in weeks:
        a_s, b_s = a.isoformat(), b.isoformat()
        per = {t: [] for t in ABBRS}
        for g in games:
            if a_s <= g["date"] <= b_s:
                per[g["visitor"]].append(g["date"])
                per[g["home"]].append(g["date"])
                keep.append(g)
        out.append({"label": f"Tjedan {n}", "start": a_s, "end": b_s,
                    "teams": {t: {"n": len(d), "dates": sorted(d)} for t, d in per.items()}})
    keep.sort(key=lambda g: (g["date"], g["home"]))
    return {"updated": now_iso(), "source": "Basketball-Reference", "weeks": out, "games": keep}


def fetch_schedule(cfg, prev, today=None):
    today = today or today_et()
    weeks = visible_weeks(cfg, today)
    months = sorted({(d.year, d.month) for _, a, b in weeks for d in (a, b)})
    games, failed = [], []
    for i, (y, m) in enumerate(months):
        if i:
            time.sleep(3.5)  # B-Ref allows ~20 requests/minute
        try:
            g = parse_bref_schedule(get(BREF_URL.format(season=cfg["season"], month=MONTH_NAMES[m - 1])).text)
            if not g:
                raise RuntimeError("no games parsed")
            games += g
        except Exception as e:
            failed.append((y, m))
            print(f"  ! B-Ref {MONTH_NAMES[m - 1]}: {e}", file=sys.stderr)
    # fall back to the previous snapshot for months that failed
    for g in (prev or {}).get("games", []):
        if (int(g["date"][:4]), int(g["date"][5:7])) in failed:
            games.append(g)
    if not games:
        raise RuntimeError("Basketball-Reference: no games")
    doc = build_schedule_doc(games, weeks)
    write_json(data_path("schedule.json"), doc)
    return doc


if __name__ == "__main__":
    cfg = load_config()
    print("injuries:", len(fetch_injuries(cfg)["items"]))
    print("news:", len(fetch_news(cfg, read_json(data_path("news.json")))[0]["items"]))
    print("schedule weeks:", [w["label"] for w in fetch_schedule(cfg, read_json(data_path("schedule.json")))["weeks"]])
