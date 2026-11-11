"""`python -m radar serve`: the always-on process on the PC (started at Windows logon, see scripts/).

One thread, one loop:
- every 15 min (10 while NBA games are on or about to start): ESPN for both leagues -> alarms, plan tracking,
  history, dashboard data
- 60 and 30 min before each game with one of my players: a fresh pass + the pre-game check
- 08:30 and 21:30: the full report; Sunday 22:00: the weekly review
- in between: Telegram long polling, so commands are answered within seconds
State lives in data/local/radar.db (SQLite), so a restart (or a night with the PC off) loses nothing."""
from __future__ import annotations

import datetime as dt
import json
import logging
import logging.handlers
import os
import sys
import threading
import time
from pathlib import Path
from zoneinfo import ZoneInfo

from . import env, history, pregame, telegram
from .alarms import Event, diff, snapshot
from .bot import Ctx, handle
from .build import run
from .config import Config
from .plantrack import plan_targets, reconcile
from .report import data_time, full_report, hhmm
from .scoreboard import busy, et_today, from_schedule, games_for
from .store import Store

log = logging.getLogger("radar.serve")
STALE_FOR_BOT = dt.timedelta(minutes=10)

COOKIE_MSG = """🔑 {label}: ESPN kolačići za privatnu ligu su {why}. Upiši nove espn_s2 i SWID:
1) Chrome → espn.com, prijavi se
2) F12 → Application → Cookies → https://www.espn.com
3) kopiraj vrijednosti espn_s2 i SWID u .env u mapi waiver-radar-2:
   ESPN_S2=…
   ESPN_SWID={{…}}
Radar ih sam učita za par minuta (za rezervni GitHub prolaz još: gh secret set ESPN_S2 i gh secret set ESPN_SWID).
Ostale lige rade normalno."""


def _parse_weekly(s: str) -> tuple[int, int, int]:
    days = {"pon": 0, "uto": 1, "sri": 2, "cet": 3, "čet": 3, "pet": 4, "sub": 5, "ned": 6}
    d, _, hm = s.strip().partition(" ")
    h, m = (int(x) for x in (hm or "22:00").split(":"))
    return days.get(d.lower()[:3], 6), h, m


class Server:
    def __init__(self, cfg: Config, store: Store, client_factory=None, clock=None, publish: bool | None = None,
                 send=None):
        self.cfg = cfg
        self.store = store
        self.tz = cfg.vremenska_zona
        if client_factory is None:
            from .espn.client import EspnClient, cookies_from_env
            client_factory = lambda: EspnClient(cfg.sezona, cookies=cookies_from_env())  # noqa: E731
        self.client_factory = client_factory
        self.clock = clock or (lambda: dt.datetime.now(dt.timezone.utc))
        self.publish_enabled = cfg.server.objavi_dashboard if publish is None else publish
        self.send = send or telegram.send
        self.res = None
        self.res_at: dt.datetime | None = None
        self.client = None
        self.tx_cache: dict = {}
        self.games: list = []
        self.next_tick: dt.datetime | None = None
        self.last_publish: dt.datetime | None = None
        self.last_retry: dt.datetime | None = None
        self.last_extra_eval: dt.datetime | None = None
        self.local_state = cfg.local_dir / "state.json"
        self._pub_thread: threading.Thread | None = None

    # ---- one pass ---------------------------------------------------------------------------------------------
    def refresh(self):
        now = self.clock()
        client = self.client_factory()
        self.client = client
        res = run(self.cfg, client, now, state_path=self.local_state, store=self.store, tx_cache=self.tx_cache)
        self.res, self.res_at = res, now
        games = games_for(client, et_today(now))
        if not games and res.schedule is not None:
            games = from_schedule(res.schedule, res.schedule.sp_of(et_today(now)))
        self.games = games
        return res

    def tick(self, why: str = "interval") -> list[Event]:
        now = self.clock()
        log.info("prolaz (%s)", why)
        try:
            res = self.refresh()
        except Exception as e:
            log.exception("prolaz nije uspio")
            return self._down(now, str(e))
        events = self._reliability(res, now)
        watch = {int(k): v for k, v in (self.store.get("watch") or {}).items()}
        a_cfg = self.cfg.alarmi
        for a in res.analyses:
            lg = res.state(a.key)
            try:
                prev = self.store.get(f"snap:{a.key}")
                cur = snapshot(lg, a, a_cfg.jaki_top)
                events += diff(prev, cur, a, lg, watch=watch, plan_targets=plan_targets(self.store, a.key),
                               strong_owned=a_cfg.jaki_vlasnistvo, prob_delta=a_cfg.prag_sanse_protivnik, tz=self.tz)
                events += reconcile(self.store, a, now, self.tz)
                history.record(self.store, a)
                history.mark_executed(self.store, a, now)
                self.store.set(f"snap:{a.key}", cur)
            except Exception:
                log.exception("alarmi %s", a.key)
        try:
            self._learn(res, now)
        except Exception:
            log.exception("povijest")
        self.publish(res, now)
        return events

    # ---- reliability ------------------------------------------------------------------------------------------
    def _reliability(self, res, now) -> list[Event]:
        out = []
        for lc in self.cfg.lige:
            flag = f"alarm:kolacici:{lc.kljuc}"
            if lc.kljuc in res.auth_failed:
                if not self.store.get(flag):
                    self.store.set(flag, now.isoformat())
                    why = "nisu postavljeni" if "postavi" in res.errors.get(lc.kljuc, ("", ""))[1] else "istekli"
                    out.append(Event(f"kolacici:{lc.kljuc}:{now.isoformat()}", lc.kljuc, "pouzdanost",
                                     COOKIE_MSG.format(label=lc.oznaka, why=why)))
            elif self.store.get(flag) and res.analysis(lc.kljuc):
                self.store.delete(flag)
                out.append(Event(f"kolacici-ok:{lc.kljuc}:{now.isoformat()}", lc.kljuc, "pouzdanost",
                                 f"✅ {lc.oznaka}: ESPN kolačići rade, liga se opet prati."))
        if res.net_failed and not res.analyses:
            out += self._down(now, "; ".join(m for _, m in res.errors.values()))
        else:
            out += self._up(now)
        return out

    def _down(self, now, why: str) -> list[Event]:
        since = self.store.get("espn_down_since")
        if not since:
            self.store.set("espn_down_since", now.isoformat())
            return []
        t0 = dt.datetime.fromisoformat(since)
        if now - t0 >= dt.timedelta(minutes=self.cfg.alarmi.espn_ne_radi_min) and not self.store.get("espn_down_alarm"):
            self.store.set("espn_down_alarm", now.isoformat())
            return [Event(f"espn-down:{since}", "", "pouzdanost",
                          f"⚠️ ESPN ne odgovara od {hhmm(t0, self.tz)} (više od "
                          f"{self.cfg.alarmi.espn_ne_radi_min:.0f} min). Radar pokušava dalje sam. ({why[:120]})")]
        return []

    def _up(self, now) -> list[Event]:
        since = self.store.get("espn_down_since")
        out = []
        if since and self.store.get("espn_down_alarm"):
            out.append(Event(f"espn-up:{since}", "", "pouzdanost",
                             f"✅ ESPN opet radi (nije odgovarao od {hhmm(dt.datetime.fromisoformat(since), self.tz)})."))
        if since:
            self.store.delete("espn_down_since")
            self.store.delete("espn_down_alarm")
        return out

    # ---- history / learning -----------------------------------------------------------------------------------
    def _learn(self, res, now) -> None:
        players = {}
        for lg in res.states:
            players.update(lg.players)
        for a in res.analyses:
            if not self.store.get(f"obs:{a.today_sp}:{a.key}"):
                rows = history.obs_rows(a)
                if rows:
                    self.store.add_obs(rows)
                    self.store.set(f"obs:{a.today_sp}:{a.key}", now.isoformat())
            history.fill_obs(self.store, players, a.today_sp)
            extra = {}
            missing = history.missing_players(self.store, a)
            if missing and self.client is not None and (
                    self.last_extra_eval is None or now - self.last_extra_eval > dt.timedelta(hours=6)):
                self.last_extra_eval = now
                try:
                    from .espn.parse import parse_player
                    lc = self.cfg.league(a.key)
                    raw = self.client.player_pool(lc.league_id, lc.privatna, key=lc.kljuc, ids=missing, games=16)
                    for x in raw.get("players") or []:
                        p = parse_player(x.get("player") or {}, self.cfg.sezona)
                        extra[p.id] = p
                except Exception as e:
                    log.info("povijest: dodatni igrači %s", e)
            history.evaluate(self.store, a, extra)

    # ---- dashboard --------------------------------------------------------------------------------------------
    def dashboard_data(self, res) -> dict:
        from .export import export
        text = full_report(res.analyses, res.errors, self.cfg.dashboard_url, self.tz, res.now)
        out = self.cfg.local_dir / "radar.json"
        try:
            previous = json.loads(out.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            previous = None
        return export(res, self.cfg, None, text, previous, store=self.store, source="pc")

    def publish(self, res, now, force: bool = False) -> None:
        try:
            data = self.dashboard_data(res)
        except Exception:
            log.exception("dashboard JSON")
            return
        body = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        out = self.cfg.local_dir / "radar.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(".tmp")
        tmp.write_bytes(body)
        tmp.replace(out)
        if not self.publish_enabled:
            return
        if not force and self.last_publish and now - self.last_publish < dt.timedelta(minutes=8):
            return
        if self._pub_thread and self._pub_thread.is_alive():
            return
        self.last_publish = now
        from .dashpub import publish as gh_publish
        s = self.cfg.server
        self._pub_thread = threading.Thread(target=gh_publish, args=(s.repo, s.podaci_grana, {"radar.json": body}),
                                            kwargs={"message": f"podaci {now:%Y-%m-%dT%H:%MZ}"}, daemon=True)
        self._pub_thread.start()

    # ---- alarms out -------------------------------------------------------------------------------------------
    def silenced(self, now) -> bool:
        until = self.store.get("tisina_do")
        return bool(until) and now < dt.datetime.fromisoformat(until)

    def emit(self, events: list[Event]) -> int:
        if not events:
            return 0
        now = self.clock()
        quiet = self.silenced(now)
        stamp = data_time(self.res_at or now, self.tz)
        fresh = [e for e in events if self.store.add_alert(e.key, e.league, e.kind, e.text, 2 if quiet else 0, stamp)]
        if quiet or not fresh:
            return 0
        return self._send([(e.key, e.text) for e in fresh], stamp)

    def _send(self, items: list[tuple[str, str]], stamp: str) -> int:
        if not telegram.configured():
            return 0
        text = f"🔔 Radar · {stamp}\n\n" + "\n\n".join(t for _, t in items)
        if self.send(text):
            self.store.mark_sent([k for k, _ in items])
            return len(items)
        return 0

    def retry_unsent(self, now) -> None:
        if self.last_retry and now - self.last_retry < dt.timedelta(minutes=5):
            return
        self.last_retry = now
        if self.silenced(now) or not telegram.configured():
            return
        rows = self.store.unsent(60)
        if rows:
            self._send([(r["key"], r["text"]) for r in rows], rows[-1]["data_ts"] or data_time(now, self.tz))

    # ---- scheduled jobs ---------------------------------------------------------------------------------------
    def interval(self, now) -> dt.timedelta:
        a = self.cfg.alarmi
        return dt.timedelta(minutes=a.interval_utakmice_min if busy(self.games, now) else a.interval_min)

    def run_due(self) -> None:
        now = self.clock()
        if self.next_tick is None or now >= self.next_tick:
            self.emit(self.tick())
            now = self.clock()
            self.next_tick = now + self.interval(now)
        self.emit(self.pregame(now))
        self.reports(now)
        self.weekly(now)
        self.retry_unsent(now)
        self.cleanup(now)

    def pregame(self, now) -> list[Event]:
        offs = self.cfg.alarmi.pred_utakmicu_min
        dues = pregame.due(self.games, offs, now, lambda k: bool(self.store.get(k)))
        if not dues:
            return []
        events = []
        if self.res_at is None or now - self.res_at > dt.timedelta(minutes=3):
            events += self.tick("prije utakmice")
            self.next_tick = self.clock() + self.interval(now)
        games = list({g.id: g for g, _ in dues}.values())
        for a in self.res.analyses if self.res else []:
            if pregame.involved(a, games):
                try:
                    events += pregame.checks(a, games, now, self.tz)
                except Exception:
                    log.exception("provjera prije utakmice %s", a.key)
        for g, off in dues:
            self.store.set(f"pg:{g.id}:{off}", now.isoformat())
        log.info("provjera prije utakmica: %s", ", ".join(f"{g.label} −{off}" for g, off in dues))
        return events

    def due_report(self, now) -> str | None:
        local = now.astimezone(ZoneInfo(self.tz))
        for name, hhmm_ in self.cfg.izvjestaji.items():
            h, m = (int(x) for x in hhmm_.split(":"))
            target = local.replace(hour=h, minute=m, second=0, microsecond=0)
            if target - dt.timedelta(minutes=2) <= local <= target + dt.timedelta(minutes=150):
                return name
        return None

    def reports(self, now) -> None:
        slot = self.due_report(now)
        if not slot or not telegram.configured():
            return
        day = now.astimezone(ZoneInfo(self.tz)).date().isoformat()
        if self.store.get(f"sent:{slot}") == day:
            return
        if self.res_at is None or now - self.res_at > dt.timedelta(minutes=5):
            self.emit(self.tick(f"izvještaj {slot}"))
        if self.res is None:
            return
        text = full_report(self.res.analyses, self.res.errors, self.cfg.dashboard_url, self.tz, self.res.now)
        if self.send(text):
            self.store.set(f"sent:{slot}", day)
            log.info("izvještaj %s poslan", slot)

    def weekly(self, now) -> None:
        wd, h, m = _parse_weekly(self.cfg.alarmi.tjedni_pregled)
        local = now.astimezone(ZoneInfo(self.tz))
        if local.weekday() != wd or (local.hour, local.minute) < (h, m) or not telegram.configured():
            return
        week = f"{local.isocalendar().year}-{local.isocalendar().week}"
        if self.store.get("weekly") == week or self.res is None or not self.res.analyses:
            return
        try:
            history.propose_weights(self.store, self.cfg)
        except Exception:
            log.exception("težine")
        text = "\n\n".join(history.weekly_review(self.store, a) for a in self.res.analyses)
        if self.send(text + f"\n🕒 {data_time(self.res.now, self.tz)}"):
            self.store.set("weekly", week)

    def cleanup(self, now) -> None:
        last = self.store.get("cleanup")
        if last and now - dt.datetime.fromisoformat(last) < dt.timedelta(hours=12):
            return
        self.store.set("cleanup", now.isoformat())
        cutoff = now - dt.timedelta(days=2)
        for k in self.store.keys("pg:") + self.store.keys("obs:"):
            v = self.store.get(k)
            try:
                if dt.datetime.fromisoformat(v) < cutoff:
                    self.store.delete(k)
            except (TypeError, ValueError):
                self.store.delete(k)

    # ---- Telegram commands ------------------------------------------------------------------------------------
    def _bot_refresh(self):
        self.emit(self.tick("bot"))
        return self.res

    def answer(self, text: str) -> str:
        now = self.clock()
        if self.res is None or self.res_at is None or now - self.res_at > STALE_FOR_BOT:
            self.emit(self.tick("bot"))
        ctx = Ctx(cfg=self.cfg, store=self.store, res=self.res, now=now, client=self.client,
                  refresh=self._bot_refresh)
        try:
            return handle(text, ctx)
        except Exception as e:
            log.exception("naredba %r", text)
            return f"Greška: {type(e).__name__}. Probaj ponovno ili /pomoc."

    def handle_updates(self, updates: list[dict]) -> None:
        mine = telegram.chat_id()
        for u in updates:
            self.store.set("tg_offset", u["update_id"] + 1)
            msg = u.get("message") or {}
            chat = str((msg.get("chat") or {}).get("id", ""))
            text = msg.get("text") or ""
            if not mine or chat != str(mine):
                log.info("poruka iz nepoznatog chata ignorirana")
                continue
            if not text:
                continue
            self.send(self.answer(text))

    # ---- main loop --------------------------------------------------------------------------------------------
    def wait(self) -> None:
        now = self.clock()
        targets = [self.next_tick] if self.next_tick else []
        nc = pregame.next_check(self.games, self.cfg.alarmi.pred_utakmicu_min, now, lambda k: bool(self.store.get(k)))
        if nc:
            targets.append(nc)
        secs = min([(t - now).total_seconds() for t in targets] + [25.0])
        secs = max(1, int(secs))
        if telegram.configured():
            ups = telegram.get_updates(self.store.get("tg_offset"), timeout=secs)
            if ups:
                self.handle_updates(ups)
        else:
            time.sleep(secs)

    def loop(self) -> None:
        env_m = env.mtime()
        last = time.time()
        while True:
            m = env.mtime()
            if m != env_m:
                env_m = m
                env.load(override=True)
                log.info(".env ponovno učitan")
                self.next_tick = None           # new cookies/token: try right away
            gap = time.time() - last
            if gap > 600:                       # PC was asleep / suspended
                log.info("nastavak nakon %.0f min pauze", gap / 60)
                self.next_tick = None
            last = time.time()
            try:
                self.run_due()
                self.wait()
            except KeyboardInterrupt:
                raise
            except Exception:
                log.exception("petlja")
                time.sleep(30)


# ---------------------------------------------------------------------------------------------------------------
def _single_instance(path: Path):
    """An exclusive lock on data/local/serve.lock (released by the OS when the process ends)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    f = open(path, "a+")
    try:
        if os.name == "nt":
            import msvcrt
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return f
    except OSError:
        f.close()
        return None


def setup_logging(cfg: Config) -> Path:
    d = cfg.local_dir
    d.mkdir(parents=True, exist_ok=True)
    path = d / "serve.log"
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fh = logging.handlers.RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(fh)
    if sys.stderr is not None and getattr(sys.stderr, "isatty", lambda: False)():
        sh = logging.StreamHandler()
        sh.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))
        root.addHandler(sh)
    return path


def serve(cfg: Config) -> int:
    lock = _single_instance(cfg.local_dir / "serve.lock")
    if lock is None:
        msg = "radar serve već radi (data/local/serve.lock je zaključan)"
        if sys.stderr is not None:
            print(msg, file=sys.stderr)
        return 1
    env.load()
    path = setup_logging(cfg)
    store = Store(cfg.local_dir / "radar.db")
    log.info("start · log %s · Telegram %s · dashboard %s", path, "da" if telegram.configured() else "NE",
             "da" if cfg.server.objavi_dashboard else "ne")
    if telegram.configured():
        telegram.delete_webhook()
    elif not telegram.chat_id() and telegram._api("getMe"):
        log.warning("TELEGRAM_CHAT_ID nije postavljen: pokreni `python -m radar telegram`")
    srv = Server(cfg, store)
    try:
        srv.loop()
    except KeyboardInterrupt:
        log.info("kraj (Ctrl+C)")
    finally:
        store.close()
        lock.close()
    return 0
