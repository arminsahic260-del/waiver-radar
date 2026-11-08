"""One full pass: ESPN -> news -> analysis per league -> report text (+ dashboard JSON)."""
from __future__ import annotations

import datetime as dt
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from .config import ROOT, Config, LeagueCfg
from .espn.client import EspnAuthError, EspnError
from .espn.parse import parse_league
from .injuries import OutEntry, update_state
from .models import LeagueState
from .news import NewsItem, fetch_news
from .schedule import ET, ProSchedule
from .value import role_signals, value_long
from .week import Analysis, analyze

log = logging.getLogger("radar")
STATE = ROOT / "data" / "state.json"


@dataclass
class RunResult:
    now: dt.datetime
    analyses: list[Analysis] = field(default_factory=list)
    errors: dict[str, tuple[str, str]] = field(default_factory=dict)    # key -> (label, message)
    news: dict[int, list[NewsItem]] = field(default_factory=dict)
    out_state: dict[int, OutEntry] = field(default_factory=dict)
    claude_items: int = 0                                                # news items Claude annotated


def load_state(path: Path = STATE) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(state: dict, path: Path = STATE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1, default=str) + "\n", encoding="utf-8")
    tmp.replace(path)


def league_error(lc: LeagueCfg, e: Exception, has_cookies: bool) -> str:
    if isinstance(e, EspnAuthError):
        if lc.privatna and not has_cookies:
            return "privatna liga: postavi ESPN_S2 i ESPN_SWID (env / GitHub secrets)"
        return "ESPN odbija prijavu: obnovi ESPN_S2 i ESPN_SWID"
    if isinstance(e, EspnError) and e.status == 404:
        return "liga nije pronađena (provjeri league_id)"
    return f"greška: {e}"[:200]


def fetch_league(client, lc: LeagueCfg, cfg: Config, now: dt.datetime) -> LeagueState:
    raw = client.league(lc.league_id, lc.privatna, key=lc.kljuc)
    pool = client.player_pool(lc.league_id, lc.privatna, key=lc.kljuc)
    try:
        tx = client.transactions(lc.league_id, lc.privatna, key=lc.kljuc)
    except EspnError as e:
        log.warning("%s transakcije: %s", lc.kljuc, e)
        tx = None
    return parse_league(lc.kljuc, raw, pool, tx, lc.team_id, cfg.sezona, name=lc.naziv, fetched_at=now)


def news_ids(states: list[LeagueState], schedule: ProSchedule, cfg: Config, now: dt.datetime) -> set[int]:
    """Whose news to read: my and my opponent's players, the free agents that could matter, injured regulars."""
    ms_now = now.timestamp() * 1000
    fresh = lambda p, h: p.last_news_ms and ms_now - p.last_news_ms <= h * 3600 * 1000  # noqa: E731
    ids = set()
    for lg in states:
        team_ids = {lg.me.id} | ({lg.opp.id} if lg.opp else set())
        for tid in team_ids:
            for r in lg.teams[tid].roster:
                if fresh(r.player, 24 * 10):
                    ids.add(r.player.id)
        fas = [f.player for f in lg.free_agents if not f.player.is_out]
        fas.sort(key=value_long, reverse=True)
        top = fas[:60] + [p for p in fas if role_signals(p, [], now, schedule)]
        ids |= {p.id for p in top if fresh(p, cfg.model.vijesti_sati)}
        for p in lg.players.values():
            if p.is_out and (p.season.avg or p.proj.avg or 0) >= 15 and fresh(p, 24 * 21):
                ids.add(p.id)
    return ids


def run(cfg: Config, client, now: dt.datetime | None = None, keys: list[str] | None = None,
        state_path: Path | None = STATE, use_claude: bool | None = None) -> RunResult:
    now = now or dt.datetime.now(dt.timezone.utc)
    res = RunResult(now=now)
    schedule = ProSchedule(client.pro_schedule(), cfg.prvi_dan)
    states, lcs = [], {}
    for lc in cfg.lige:
        if keys and lc.kljuc not in keys:
            continue
        try:
            states.append(fetch_league(client, lc, cfg, now))
            lcs[lc.kljuc] = lc
        except Exception as e:  # one league failing must not sink the other
            log.warning("%s: %s", lc.kljuc, e)
            res.errors[lc.kljuc] = (lc.oznaka, league_error(lc, e, getattr(client, "has_cookies", False)))

    res.news = fetch_news(client, news_ids(states, schedule, cfg, now))
    if use_claude if use_claude is not None else cfg.claude.ukljuceno:
        from .llm import annotate
        names = {pid: p.name for lg in states for pid, p in lg.players.items() if pid in res.news}
        try:
            res.claude_items = annotate(res.news, names, cfg.claude.model, now)
        except Exception as e:  # optional feature: never break the report
            log.warning("Claude: %s", e)

    state = load_state(state_path) if state_path else {}
    old = {int(k): OutEntry.model_validate(v) for k, v in (state.get("out") or {}).items()}
    players = {}
    for lg in states:
        players.update(lg.players)
    res.out_state = update_state(old, players, res.news, now.astimezone(ET).date())
    if state_path:
        state["out"] = {str(k): v.model_dump(mode="json") for k, v in res.out_state.items()}
        save_state(state, state_path)

    for lg in states:
        lc = lcs[lg.key]
        try:
            res.analyses.append(analyze(lg, lc, cfg, schedule, now, res.news, res.out_state))
        except Exception as e:
            log.exception("analiza %s", lg.key)
            res.errors[lg.key] = (lc.oznaka, f"greška u analizi: {e}"[:200])
    return res
