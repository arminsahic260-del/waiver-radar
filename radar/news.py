"""ESPN player news: fetch, clean, and read two things out of it — "he's starting now" and "out until"."""
from __future__ import annotations

import datetime as dt
import html
import logging
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor

from pydantic import BaseModel

log = logging.getLogger("radar.news")


class NewsItem(BaseModel):
    id: str
    player_id: int
    published: dt.datetime
    headline: str
    story: str = ""
    summary_hr: str | None = None    # Claude (optional): one Croatian sentence
    starter: bool | None = None      # Claude (optional): reports a new starter / bigger role

    @property
    def text(self) -> str:
        return f"{self.headline} {self.story}".strip()


def clean_html(s: str | None) -> str:
    s = re.sub(r"<br\s*/?>|</p>", " ", s or "", flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def parse_feed(raw: dict, player_id: int) -> list[NewsItem]:
    out = []
    for f in (raw or {}).get("feed") or []:
        if f.get("type") == "Media" or not f.get("headline"):  # highlight clips
            continue
        try:
            pub = dt.datetime.fromisoformat(str(f.get("published")).replace("Z", "+00:00"))
        except ValueError:
            continue
        out.append(NewsItem(id=str(f.get("id") or f.get("nowId") or pub.timestamp()), player_id=player_id,
                            published=pub, headline=clean_html(f.get("headline")),
                            story=clean_html(f.get("story") or f.get("description"))))
    out.sort(key=lambda n: n.published, reverse=True)
    return out


def fetch_news(client, player_ids, workers: int = 8) -> dict[int, list[NewsItem]]:
    ids = sorted(set(player_ids))

    def one(pid):
        try:
            return pid, parse_feed(client.news(pid), pid)
        except Exception as e:  # news is a bonus; never fail the run for it
            log.warning("vijesti za %s: %s", pid, e)
            return pid, []

    if not ids:
        return {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        return dict(ex.map(one, ids))


# ---------------------------------------------------------------------------------------------------------------
def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9' ]", " ", s)).strip()


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", text) if s]


STARTER_PATTERNS = [
    r"will start\b", r"starting lineup", r"enter(?:s|ed|ing)? the starting", r"named (?:the )?starter",
    r"move(?:s|d)? into the starting", r"in the first five", r"increased role", r"expanded role",
    r"(?:get|got|draw|drew|earn|earned|receive|received) the (?:start|starting nod)", r"start in place of",
    r"(?:first|second|third) (?:career |consecutive )?start\b", r"starting role",
]
WITH_OUT = re.compile(r"\bwith ([A-Z][\w'.-]+(?: [A-Z][\w'.-]+){0,2}) (?:out|sidelined|unavailable|injured|sitting)\b")
NEGATIONS = [
    r"won't start", r"will not start", r"not (?:be )?start", r"come off the bench", r"coming off the bench",
    r"(?:out of|removed from|dropped from|lose|lost) (?:his spot in )?the starting", r"(?:moved|move|back) to the bench",
    r"return(?:s|ed)? to (?:a|the) bench", r"bench role", r"reserve role",
]
_POS = re.compile("|".join(STARTER_PATTERNS), re.I)
_NEG = re.compile("|".join(NEGATIONS), re.I)


def about_player(item: NewsItem, last_name: str) -> bool:
    """Generic articles ('rookie watch', highlights) are tagged to many players; keep items about him."""
    ln = re.sub(r"\s+(jr|sr|ii|iii|iv)$", "", norm(last_name))
    return bool(ln) and ln in norm(item.headline)


def starter_hit(item: NewsItem, last_name: str) -> str | None:
    """The sentence that says he is starting / getting a bigger role (None if nothing, or it's negated)."""
    if item.starter is not None:          # Claude read it: trust that over keywords
        return (item.summary_hr or item.headline) if item.starter else None
    if not about_player(item, last_name):
        return None
    for sent in _sentences(item.text):
        if _NEG.search(sent):
            continue
        if _POS.search(sent) or WITH_OUT.search(sent):
            return sent.strip()
    return None


# ---------------------------------------------------------------------------------------------------------------
_NUMS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve".split())}
_NUM = r"(\d+|zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)"
_RANGE = _NUM + r"(?:\s*(?:-?to-?|-|–|or)\s*" + _NUM + r")?"
_UNIT = r"[\s-]*(weeks?|months?|games?|days?)"
_DUR = [
    re.compile(r"\b(?:out|sidelined|miss|missing|absent|shelved)\b[^.]{0,40}?\b" + _RANGE + _UNIT, re.I),
    re.compile(r"\bre-?evaluat(?:ed|ion) in\s+(?:about |approximately |roughly |around )?" + _RANGE + _UNIT, re.I),
    re.compile(r"\b(?:timeline|recovery timeline) of\s+(?:about |approximately |roughly )?" + _RANGE + _UNIT, re.I),
    re.compile(r"\b" + _RANGE + _UNIT + r"\s+(?:recovery|absence|timeline)", re.I),
]
_SEASON = re.compile(r"season-ending|out for the (?:rest of the |remainder of the )?season|"
                     r"(?:rest|remainder) of the (?:regular )?season|miss the season", re.I)
_INDEF = re.compile(r"\bout indefinitely\b|\bno timetable\b|\bwithout a timetable\b", re.I)
SEASON_END = dt.date(2027, 4, 11)


def _n(s: str | None) -> int | None:
    if s is None:
        return None
    s = s.lower()
    return int(s) if s.isdigit() else _NUMS.get(s)


def _days(n: int, unit: str) -> int:
    unit = unit.lower()
    if unit.startswith("week"):
        return 7 * n
    if unit.startswith("month"):
        return 30 * n
    if unit.startswith("game"):
        return 2 * n
    return n


class OutInfo(BaseModel):
    since: dt.date
    min_date: dt.date
    max_date: dt.date
    text: str               # the sentence it came from
    kind: str = "weeks"     # weeks | season | indefinite

    def label(self) -> str:
        if self.kind == "season":
            return "do kraja sezone"
        if self.kind == "indefinite":
            return "neodređeno"
        a, b = self.min_date, self.max_date
        if a == b:
            return f"do ~{a.day}.{a.month}."
        return f"do ~{a.day}.{a.month}.–{b.day}.{b.month}."


def out_info(item: NewsItem) -> OutInfo | None:
    pub = item.published.date()
    for sent in _sentences(item.text):
        if _SEASON.search(sent):
            return OutInfo(since=pub, min_date=SEASON_END, max_date=SEASON_END, text=sent, kind="season")
        for rx in _DUR:
            m = rx.search(sent)
            if not m:
                continue
            lo, hi, unit = _n(m.group(1)), _n(m.group(2)), m.group(3)
            if lo is None:
                continue
            hi = hi if hi is not None and hi >= lo else lo
            return OutInfo(since=pub, min_date=pub + dt.timedelta(days=_days(lo, unit)),
                           max_date=pub + dt.timedelta(days=_days(hi, unit)), text=sent)
        if _INDEF.search(sent):
            return OutInfo(since=pub, min_date=pub + dt.timedelta(days=14), max_date=pub + dt.timedelta(days=42),
                           text=sent, kind="indefinite")
    return None


# ---------------------------------------------------------------------------------------------------------------
_QUESTIONABLE = re.compile(r"\bquestionable\b|game-time decision|\bgtd\b|\bdoubtful\b|uncertain to play|"
                           r"\bis uncertain\b|listed as (?:questionable|doubtful)|true game-time", re.I)
_PLAYING = re.compile(r"\b(?:will play|is playing|plans to play|cleared to play|available to play|"
                      r"will be available|is available|upgraded to (?:probable|available)|will suit up|"
                      r"will start|in the starting lineup|will return|is set to return|has been cleared)\b", re.I)
_RULED_OUT = re.compile(r"\b(?:ruled out|won't play|will not play|will miss|won't suit up|"
                        r"has been downgraded to out|out for (?:tonight|today|wednesday|thursday|friday|saturday|"
                        r"sunday|monday|tuesday)|will sit out|won't be available|will not be available)\b", re.I)


def availability_flag(items: list[NewsItem], last_name: str, now: dt.datetime, hours: float = 24
                      ) -> tuple[str, str] | None:
    """('upitan' | 'out', sentence) from his newest news in the last `hours`; None if nothing or he's playing."""
    cutoff = now - dt.timedelta(hours=hours)
    for n in items:                      # newest first
        if n.published < cutoff:
            break
        if n.published > now or not about_player(n, last_name):
            continue
        for sent in _sentences(n.text):
            if _RULED_OUT.search(sent):
                return "out", sent.strip()
            if _PLAYING.search(sent) and not _QUESTIONABLE.search(sent):
                return None
            if _QUESTIONABLE.search(sent):
                return "upitan", sent.strip()
    return None
