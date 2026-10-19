"""Shared helpers: paths, config, HTTP, team maps, name/date normalisation."""
import datetime as dt
import json
import os
import re
import time
import unicodedata
from zoneinfo import ZoneInfo

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "site", "data")
CACHE = os.path.join(ROOT, "cache")

ET = ZoneInfo("America/New_York")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/141.0 Safari/537.36")

ABBRS = ["ATL", "BOS", "BKN", "CHA", "CHI", "CLE", "DAL", "DEN", "DET", "GSW", "HOU", "IND", "LAC", "LAL", "MEM",
         "MIA", "MIL", "MIN", "NOP", "NYK", "OKC", "ORL", "PHI", "PHX", "POR", "SAC", "SAS", "TOR", "UTA", "WAS"]

# Every spelling the sources use (CBS short names, full names, B-Ref/RotoWire/CBS URL codes) -> our abbreviation.
_TEAM_ALIASES = {
    "ATL": ["Atlanta", "Atlanta Hawks"],
    "BOS": ["Boston", "Boston Celtics"],
    "BKN": ["Brooklyn", "Brooklyn Nets", "BRK", "BKN", "BK"],
    "CHA": ["Charlotte", "Charlotte Hornets", "CHO", "CHA"],
    "CHI": ["Chicago", "Chicago Bulls"],
    "CLE": ["Cleveland", "Cleveland Cavaliers"],
    "DAL": ["Dallas", "Dallas Mavericks"],
    "DEN": ["Denver", "Denver Nuggets"],
    "DET": ["Detroit", "Detroit Pistons"],
    "GSW": ["Golden St.", "Golden State", "Golden State Warriors", "GS", "GSW"],
    "HOU": ["Houston", "Houston Rockets"],
    "IND": ["Indiana", "Indiana Pacers"],
    "LAC": ["L.A. Clippers", "LA Clippers", "Los Angeles Clippers"],
    "LAL": ["L.A. Lakers", "LA Lakers", "Los Angeles Lakers"],
    "MEM": ["Memphis", "Memphis Grizzlies"],
    "MIA": ["Miami", "Miami Heat"],
    "MIL": ["Milwaukee", "Milwaukee Bucks"],
    "MIN": ["Minnesota", "Minnesota Timberwolves"],
    "NOP": ["New Orleans", "New Orleans Pelicans", "NO", "NOR", "NOP"],
    "NYK": ["New York", "New York Knicks", "NY", "NYK"],
    "OKC": ["Oklahoma City", "Oklahoma City Thunder"],
    "ORL": ["Orlando", "Orlando Magic"],
    "PHI": ["Philadelphia", "Philadelphia 76ers"],
    "PHX": ["Phoenix", "Phoenix Suns", "PHO", "PHX"],
    "POR": ["Portland", "Portland Trail Blazers"],
    "SAC": ["Sacramento", "Sacramento Kings"],
    "SAS": ["San Antonio", "San Antonio Spurs", "SA", "SAS"],
    "TOR": ["Toronto", "Toronto Raptors"],
    "UTA": ["Utah", "Utah Jazz", "UTAH"],
    "WAS": ["Washington", "Washington Wizards", "WSH"],
}
TEAM = {a: a for a in ABBRS}
for _abbr, _names in _TEAM_ALIASES.items():
    for _n in _names:
        TEAM[_n] = _abbr
for _n in ("FA", "Free Agent", "Free Agents"):
    TEAM[_n] = "FA"


def team_abbr(name):
    """Map any source spelling of a team to our abbreviation (None if unknown)."""
    if not name:
        return None
    name = name.strip()
    return TEAM.get(name) or TEAM.get(name.upper())


# ESPN fantasy id maps
ESPN_TEAM = {1: "ATL", 2: "BOS", 3: "NOP", 4: "CHI", 5: "CLE", 6: "DAL", 7: "DEN", 8: "DET", 9: "GSW", 10: "HOU",
             11: "IND", 12: "LAC", 13: "LAL", 14: "MIA", 15: "MIL", 16: "MIN", 17: "BKN", 18: "NYK", 19: "ORL",
             20: "PHI", 21: "PHX", 22: "POR", 23: "SAC", 24: "SAS", 25: "OKC", 26: "UTA", 27: "WAS", 28: "TOR",
             29: "MEM", 30: "CHA"}
ESPN_POS = {1: "PG", 2: "SG", 3: "SF", 4: "PF", 5: "C"}
ESPN_SLOT = {0: "PG", 1: "SG", 2: "SF", 3: "PF", 4: "C", 5: "G", 6: "F", 7: "SG/SF", 8: "G/F", 9: "PF/C",
             10: "F/C", 11: "UTIL", 12: "BE", 13: "IR"}

# Croatian weekday names (Python weekday(): Mon=0); accusative for "igra u <dan>".
DAY_ACC = ["ponedjeljak", "utorak", "srijedu", "četvrtak", "petak", "subotu", "nedjelju"]

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def season_date(month, day, season):
    """NBA season `season` (e.g. 2027) runs Oct 2026 - Jun 2027: Jul-Dec belong to season-1."""
    year = season - 1 if month >= 7 else season
    try:
        return dt.date(year, month, day).isoformat()
    except ValueError:
        return None


def parse_month_day(text, season):
    """'Mon, Oct 5' / 'at least Nov 8' / 'October 18, 2026' -> ISO date."""
    if not text:
        return None
    m = re.search(r"\b([A-Z][a-z]{2})[a-z]*\.?\s+(\d{1,2})(?:,\s*(\d{4}))?", text)
    if not m or m.group(1).lower() not in MONTHS:
        return None
    mo, d = MONTHS[m.group(1).lower()], int(m.group(2))
    if m.group(3):
        try:
            return dt.date(int(m.group(3)), mo, d).isoformat()
        except ValueError:
            return None
    return season_date(mo, d, season)


_SUFFIX = re.compile(r"\b(jr|sr|ii|iii|iv)\b")


def norm_name(name):
    """Accent/punctuation/suffix-insensitive key for matching player names across sources."""
    s = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9 ]", "", s.replace("-", " "))
    s = _SUFFIX.sub("", s)
    return re.sub(r"\s+", " ", s).strip()


def now_iso():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def today_et():
    """NBA game dates are US Eastern; a game 'on' a date is over by the next ET morning."""
    return dt.datetime.now(ET).date()


def load_config():
    with open(os.path.join(ROOT, "config.json"), encoding="utf-8") as f:
        return json.load(f)


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
        f.write("\n")
    os.replace(tmp, path)


def data_path(name):
    return os.path.join(DATA, name)


_session = None


def http():
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
    return _session


def get(url, headers=None, cookies=None, tries=3, timeout=30):
    """GET with small backoff; raises the last error."""
    err = None
    for i in range(tries):
        try:
            r = http().get(url, headers=headers, cookies=cookies, timeout=timeout)
            if r.status_code == 429 or r.status_code >= 500:
                raise requests.HTTPError(f"{r.status_code} for {url}", response=r)
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            err = e
            time.sleep(2 * (i + 1))
    raise err
