"""The one place that talks to ESPN: retry, cache-busting, cookies for private leagues, fixture record/replay.

Cookies (ESPN_S2, ESPN_SWID) are read from the environment, sent only to the fantasy API host, and never logged.
"""
from __future__ import annotations

import gzip
import json
import logging
import os
import re
import time
from pathlib import Path

import requests

log = logging.getLogger("radar.espn")

FANTASY = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/fba/seasons/{season}"
NEWS = "https://site.api.espn.com/apis/fantasy/v2/games/fba/news/players"
SEARCH = "https://site.web.api.espn.com/apis/search/v2"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36"

LEAGUE_VIEWS = ("mRoster", "mMatchupScore", "mLiveScoring", "mTeam", "mSettings")


class EspnError(RuntimeError):
    """ESPN request failed. `status` is the HTTP code (None for network errors)."""

    def __init__(self, msg, status=None):
        super().__init__(msg)
        self.status = status


class EspnAuthError(EspnError):
    pass


def cookies_from_env() -> dict | None:
    s2, swid = os.environ.get("ESPN_S2", "").strip(), os.environ.get("ESPN_SWID", "").strip()
    if not (s2 and swid):
        return None
    if not swid.startswith("{"):
        swid = "{" + swid.strip("{}") + "}"
    return {"espn_s2": s2, "SWID": swid}


def _redact(url: str) -> str:
    return re.sub(r"([?&]_=)\d+", r"\1…", url)


class EspnClient:
    """Live client. Every fantasy call gets `_=<ms>` so the CDN can't hand back a stale roster."""

    def __init__(self, season: int, cookies: dict | None = None, tries: int = 3, timeout: int = 45,
                 record_dir: str | Path | None = None):
        self.season = season
        self.base = FANTASY.format(season=season)
        self._cookies = cookies
        self.tries, self.timeout = tries, timeout
        self.record_dir = Path(record_dir) if record_dir else None
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA, "Accept": "application/json", "Accept-Language": "en-US,en;q=0.9"})

    @property
    def has_cookies(self) -> bool:
        return bool(self._cookies)

    # -- transport -------------------------------------------------------------------------------------------
    def _get(self, url: str, *, params=None, headers=None, private=False, bust=True):
        params = dict(params or {})
        if bust:
            params["_"] = str(int(time.time() * 1000))
        hdrs = dict(headers or {})
        if private and self._cookies:
            hdrs["Cookie"] = "; ".join(f"{k}={v}" for k, v in self._cookies.items())
        err: Exception | None = None
        for i in range(self.tries):
            try:
                r = self.s.get(url, params=params, headers=hdrs, timeout=self.timeout)
            except requests.RequestException as e:  # network: retry
                err = EspnError(f"mreža: {type(e).__name__}")
                time.sleep(1.5 * (i + 1))
                continue
            if r.status_code in (401, 403):
                raise EspnAuthError(f"ESPN {r.status_code} za {_redact(r.url.split('?')[0])}", r.status_code)
            if r.status_code == 429 or r.status_code >= 500:
                err = EspnError(f"ESPN {r.status_code}", r.status_code)
                time.sleep(2.0 * (i + 1))
                continue
            if r.status_code >= 400:
                raise EspnError(f"ESPN {r.status_code} za {r.url.split('?')[0]}", r.status_code)
            try:
                return r.json()
            except ValueError:
                err = EspnError("ESPN je vratio neispravan JSON")
                time.sleep(1.0)
        raise err or EspnError("ESPN: nepoznata greška")

    def _record(self, name: str, data):
        if self.record_dir:
            self.record_dir.mkdir(parents=True, exist_ok=True)
            with gzip.open(self.record_dir / f"{name}.json.gz", "wt", encoding="utf-8") as f:
                json.dump(sanitize(data), f, ensure_ascii=False, separators=(",", ":"))
        return data

    # -- endpoints -------------------------------------------------------------------------------------------
    def pro_schedule(self) -> dict:
        return self._record("pro_schedule", self._get(self.base, params={"view": "proTeamSchedules_wl"}))

    def league(self, league_id: int, private: bool, key: str = "") -> dict:
        params = [("view", v) for v in LEAGUE_VIEWS]
        url = f"{self.base}/segments/0/leagues/{league_id}"
        data = self._get(url + "?" + "&".join(f"{k}={v}" for k, v in params), private=private)
        return self._record(f"{key or league_id}_league", data)

    def player_pool(self, league_id: int, private: bool, key: str = "", limit: int = 1000, games: int = 8,
                    statuses=("FREEAGENT", "WAIVERS", "ONTEAM"), ids: list[int] | None = None) -> dict:
        """kona_player_info: everyone (or `ids`) with onTeamId/status/waiverProcessDate and the last `games` games.

        Note: never combine filterProTeamIds with status ONTEAM (ESPN returns nothing); filter locally instead."""
        p: dict = {"filterStatsForTopScoringPeriodIds": {
            "value": games, "additionalValue": [f"00{self.season}", f"10{self.season}", f"01{self.season}"]}}
        if ids:
            p["filterIds"] = {"value": list(ids)}
        else:
            p.update({"filterStatus": {"value": list(statuses)}, "limit": limit,
                      "sortPercOwned": {"sortAsc": False, "sortPriority": 1}})
        data = self._get(f"{self.base}/segments/0/leagues/{league_id}", params={"view": "kona_player_info"},
                         headers={"X-Fantasy-Filter": json.dumps({"players": p})}, private=private)
        return self._record(f"{key or league_id}_pool", data)

    def transactions(self, league_id: int, private: bool, key: str = "", sp: int | None = None) -> dict:
        params = {"view": "mTransactions2"}
        if sp:
            params["scoringPeriodId"] = sp
        data = self._get(f"{self.base}/segments/0/leagues/{league_id}", params=params, private=private)
        return self._record(f"{key or league_id}_tx", data)

    def news(self, player_id: int, limit: int = 6) -> dict:
        data = self._get(NEWS, params={"limit": limit, "playerId": player_id}, bust=False)
        return self._record(f"news_{player_id}", data)

    def search_player(self, name: str) -> list[tuple[int, str]]:
        data = self._get(SEARCH, params={"query": name, "limit": 5}, bust=False)
        out = []
        for res in data.get("results", []):
            if res.get("type") != "player":
                continue
            for c in res.get("contents", []):
                m = re.search(r"a:(\d+)", c.get("uid") or "")
                if m:
                    out.append((int(m.group(1)), c.get("displayName", "")))
        return out


class FixtureClient:
    """Replays responses saved by `EspnClient(record_dir=...)` (or `python -m radar record`)."""

    def __init__(self, directory: str | Path, season: int = 2027):
        self.dir = Path(directory)
        self.season = season
        self.has_cookies = True

    def _load(self, name: str):
        for p in (self.dir / f"{name}.json.gz", self.dir / f"{name}.json"):
            if p.exists():
                opener = gzip.open if p.suffix == ".gz" else open
                with opener(p, "rt", encoding="utf-8") as f:
                    return json.load(f)
        return None

    def _need(self, name: str):
        data = self._load(name)
        if data is None:
            raise EspnError(f"nema fixturea {name}", 404)
        return data

    def pro_schedule(self):
        return self._need("pro_schedule")

    def league(self, league_id, private, key=""):
        data = self._load(f"{key}_league")
        if data is None:
            raise EspnError(f"nema snimljenih podataka za {key}")
        return data

    def player_pool(self, league_id, private, key="", **kw):
        return self._need(f"{key}_pool")

    def transactions(self, league_id, private, key="", sp=None):
        return self._load(f"{key}_tx") or {"transactions": []}

    def news(self, player_id, limit=6):
        return self._load(f"news_{player_id}") or {"feed": []}

    def search_player(self, name):
        return []


def sanitize(data):
    """Strip league members' personal data (names, ids) before a response is written to disk."""
    if isinstance(data, dict):
        out = {}
        for k, v in data.items():
            if k == "members":
                continue
            if k in ("owners", "primaryOwner", "memberId", "creatorId"):
                out[k] = [] if isinstance(v, list) else ""
                continue
            out[k] = sanitize(v)
        return out
    if isinstance(data, list):
        return [sanitize(x) for x in data]
    return data
