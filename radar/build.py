"""One full pass: ESPN -> news -> analysis per league -> report text (+ dashboard JSON)."""
from __future__ import annotations

import datetime as dt
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from concurrent.futures import ThreadPoolExecutor

from .availability import age_from_bio
from .config import ROOT, Config, LeagueCfg
from .espn.client import EspnAuthError, EspnError
from .espn.parse import parse_league, parse_transactions
from . import lineups
from .injuries import OutEntry, update_state
from .models import LeagueState, Transaction
from .news import NewsItem, fetch_news
from .schedule import ET, ProSchedule
from .value import configure_from, role_signals, value_long
from .week import Analysis, Extras, analyze

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
    states: list[LeagueState] = field(default_factory=list)
    auth_failed: set[str] = field(default_factory=set)                   # private league answered 401/403
    net_failed: set[str] = field(default_factory=set)                    # ESPN unreachable / 5xx
    schedule: ProSchedule | None = None
    lineup_games: int = 0                                                # games read from RotoWire this pass

    def analysis(self, key: str) -> Analysis | None:
        return next((a for a in self.analyses if a.key == key), None)

    def state(self, key: str) -> LeagueState | None:
        return next((s for s in self.states if s.key == key), None)


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
            return "privatna liga: postavi ESPN_S2 i ESPN_SWID (.env na PC-u / GitHub secrets)"
        return "ESPN odbija prijavu: obnovi ESPN_S2 i ESPN_SWID (.env na PC-u / GitHub secrets)"
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


def week_transactions(client, lc: LeagueCfg, lg: LeagueState, schedule: ProSchedule,
                      cache: dict | None = None) -> list[Transaction]:
    """Transactions of the current H2H week (Monday..today): one call per day, past days cached."""
    out = list(lg.transactions)
    d = schedule.date(lg.sp)
    monday_sp = max(1, schedule.sp_of(d - dt.timedelta(days=d.weekday())))
    for sp in range(monday_sp, lg.sp):
        key = (lc.kljuc, sp)
        if cache is not None and key in cache:
            out += cache[key]
            continue
        try:
            txs = parse_transactions(client.transactions(lc.league_id, lc.privatna, key=lc.kljuc, sp=sp))
        except EspnError as e:
            log.warning("%s transakcije sp %s: %s", lc.kljuc, sp, e)
            continue
        if cache is not None:
            cache[key] = txs
        out += txs
    seen, uniq = set(), []
    for t in out:
        if t.id not in seen:
            seen.add(t.id)
            uniq.append(t)
    return uniq


def ages_for(client, states: list[LeagueState], store=None, today: dt.date | None = None,
             workers: int = 8) -> dict[int, int]:
    """Ages of rostered players (mine, opponent) and the best free agents; birth dates cached in the store."""
    today = today or dt.date.today()
    want: set[int] = set()
    for lg in states:
        for t in (lg.me, lg.opp):
            for r in t.roster if t else []:
                want.add(r.player.id)
        fas = sorted((f.player for f in lg.free_agents), key=value_long, reverse=True)[:40]
        want |= {p.id for p in fas}
    if not hasattr(client, "athlete"):
        return {}
    bios: dict[int, dict | None] = {}
    missing = []
    for pid in want:
        hit = store.get(f"bio:{pid}") if store is not None else None
        if hit is not None:
            bios[pid] = hit
        else:
            missing.append(pid)

    def one(pid):
        try:
            b = client.athlete(pid)
            return pid, ({"dateOfBirth": b.get("dateOfBirth"), "age": b.get("age")} if b else {})
        except Exception as e:  # ages only refine p_play; never fail the run
            log.info("dob %s: %s", pid, e)
            return pid, None

    if missing:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for pid, b in ex.map(one, sorted(missing)):
                if b is None:
                    continue
                bios[pid] = b
                if store is not None:
                    store.set(f"bio:{pid}", b)
    out = {}
    for pid, b in bios.items():
        a = age_from_bio(b, today)
        if a is not None:
            out[pid] = a
    return out


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
        state_path: Path | None = STATE, use_claude: bool | None = None, store=None,
        tx_cache: dict | None = None, simulate: bool = True, schedule: ProSchedule | None = None) -> RunResult:
    """One pass. `store` (radar.store.Store) adds ages, /cilj overrides and accepted weights; `tx_cache` keeps
    past days' transactions between passes of the always-on server."""
    now = now or dt.datetime.now(dt.timezone.utc)
    res = RunResult(now=now)
    configure_from(cfg)
    if store is not None:
        w = store.get("tezine")
        if w:
            from .value import configure
            configure(now=w.get("sada"), long=w.get("dugo"), trend_k=w.get("trend"))
    schedule = schedule or ProSchedule(client.pro_schedule(), cfg.prvi_dan)
    res.schedule = schedule
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
            if isinstance(e, EspnAuthError):
                res.auth_failed.add(lc.kljuc)
            elif isinstance(e, EspnError) and (e.status is None or e.status >= 500 or e.status == 429):
                res.net_failed.add(lc.kljuc)
            elif not isinstance(e, (EspnError, ValueError)):
                res.net_failed.add(lc.kljuc)
    res.states = states

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

    ages = ages_for(client, states, store, now.astimezone(ET).date()) if store is not None else {}
    rw_games = []
    if cfg.rotowire and hasattr(client, "rotowire_lineups"):
        try:
            page = client.rotowire_lineups()
            rw_games = lineups.parse(page) if page else []
        except Exception as e:  # a second source: never break the run
            log.warning("RotoWire: %s", e)
    res.lineup_games = len(rw_games)
    today_sp = schedule.sp_of(now.astimezone(ET).date())
    for lg in states:
        lc = lcs[lg.key]
        try:
            week_tx = week_transactions(client, lc, lg, schedule, tx_cache) if store is not None else []
            goals = {}
            if store is not None:
                for per in (lg.matchup_period, lg.matchup_period + 1):
                    g = store.get(f"cilj:{lg.key}:{per}")
                    if g:
                        goals[per] = g
            pool = {**lg.players, **{r.player.id: r.player for t in lg.teams.values() for r in t.roster}}
            ex = Extras(ages=ages, week_tx=week_tx, goals=goals, simulate=simulate,
                        lineups=lineups.match(rw_games, pool, schedule, today_sp) if rw_games else {})
            res.analyses.append(analyze(lg, lc, cfg, schedule, now, res.news, res.out_state, ex))
        except Exception as e:
            log.exception("analiza %s", lg.key)
            res.errors[lg.key] = (lc.oznaka, f"greška u analizi: {e}"[:200])
    return res
