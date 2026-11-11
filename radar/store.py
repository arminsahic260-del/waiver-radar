"""Durable state of the always-on server (SQLite, data/local/radar.db, never committed).

kv      snapshots of free agents/rosters, plans, watchlist, /cilj and /tisina, cached birth dates, flags
alerts  every alarm ever raised (dedupe by key), whether it was sent
recs    every recommendation (drop X / add Y), whether I did it, expected and actual gain
obs     model features per player and game day + the points he really scored (model accuracy, weight search)
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
import threading
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT NOT NULL, t TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS alerts (
  id INTEGER PRIMARY KEY, ts TEXT NOT NULL, key TEXT NOT NULL UNIQUE, league TEXT, kind TEXT, text TEXT,
  sent INTEGER NOT NULL DEFAULT 0, data_ts TEXT);
CREATE TABLE IF NOT EXISTS recs (
  id INTEGER PRIMARY KEY, ts TEXT NOT NULL, league TEXT NOT NULL, period INTEGER NOT NULL, kind TEXT,
  drop_id INTEGER, drop_name TEXT, add_id INTEGER, add_name TEXT, t_sp INTEGER, end_sp INTEGER,
  expected REAL, executed INTEGER NOT NULL DEFAULT 0, executed_ts TEXT, actual REAL, actual_add REAL,
  actual_drop REAL, final INTEGER NOT NULL DEFAULT 0, last_seen TEXT,
  UNIQUE(league, period, drop_id, add_id));
CREATE TABLE IF NOT EXISTS obs (
  sp INTEGER NOT NULL, pid INTEGER NOT NULL, period INTEGER, season REAL, last7 REAL, last15 REAL, proj REAL,
  mpg REAL, mpg3 REAL, mpg5 REAL, fppm REAL, value REAL, actual REAL, played INTEGER,
  PRIMARY KEY (sp, pid));
"""


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: str | Path = ":memory:"):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, check_same_thread=False, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            if self.path != ":memory:":
                self.db.execute("PRAGMA journal_mode=WAL")
            self.db.executescript(SCHEMA)
            self.db.commit()

    # ---- key/value ------------------------------------------------------------------------------------------
    def get(self, k: str, default=None):
        with self.lock:
            row = self.db.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        return json.loads(row["v"]) if row else default

    def set(self, k: str, v) -> None:
        with self.lock:
            self.db.execute("INSERT INTO kv(k, v, t) VALUES(?,?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v, "
                            "t=excluded.t", (k, json.dumps(v, ensure_ascii=False, default=str), _now()))
            self.db.commit()

    def delete(self, k: str) -> None:
        with self.lock:
            self.db.execute("DELETE FROM kv WHERE k=?", (k,))
            self.db.commit()

    def keys(self, prefix: str) -> list[str]:
        with self.lock:
            return [r["k"] for r in self.db.execute("SELECT k FROM kv WHERE k LIKE ?", (prefix + "%",))]

    # ---- alerts ---------------------------------------------------------------------------------------------
    def seen(self, key: str) -> bool:
        with self.lock:
            return self.db.execute("SELECT 1 FROM alerts WHERE key=?", (key,)).fetchone() is not None

    def add_alert(self, key: str, league: str, kind: str, text: str, sent: int, data_ts: str = "") -> bool:
        """False if an alert with this key exists already (= duplicate, don't send).
        sent: 0 = waiting to be sent, 1 = sent, 2 = suppressed (/tisina)."""
        with self.lock:
            try:
                self.db.execute("INSERT INTO alerts(ts, key, league, kind, text, sent, data_ts) VALUES(?,?,?,?,?,?,?)",
                                (_now(), key, league, kind, text, int(sent), data_ts))
                self.db.commit()
                return True
            except sqlite3.IntegrityError:
                return False

    def mark_sent(self, keys: list[str]) -> None:
        with self.lock:
            self.db.executemany("UPDATE alerts SET sent=1 WHERE key=?", [(k,) for k in keys])
            self.db.commit()

    def unsent(self, max_age_min: float = 60) -> list[sqlite3.Row]:
        cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=max_age_min)).isoformat(timespec="seconds")
        with self.lock:
            return list(self.db.execute("SELECT * FROM alerts WHERE sent=0 AND ts>=? ORDER BY id", (cutoff,)))

    def recent_alerts(self, n: int = 50) -> list[dict]:
        with self.lock:
            rows = self.db.execute("SELECT ts, league, kind, text, sent FROM alerts ORDER BY id DESC LIMIT ?", (n,))
            return [dict(r) for r in rows]

    # ---- recommendations ------------------------------------------------------------------------------------
    def add_rec(self, league: str, period: int, kind: str, drop_id, drop_name, add_id, add_name, t_sp: int,
                end_sp: int, expected: float) -> bool:
        with self.lock:
            cur = self.db.execute(
                "INSERT INTO recs(ts, league, period, kind, drop_id, drop_name, add_id, add_name, t_sp, end_sp, "
                "expected, last_seen) VALUES(?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(league, period, drop_id, add_id) DO UPDATE SET last_seen=excluded.last_seen",
                (_now(), league, period, kind, drop_id, drop_name, add_id, add_name, t_sp, end_sp, expected, _now()))
            self.db.commit()
            return cur.rowcount > 0

    def recs(self, league: str | None = None, period: int | None = None, where: str = "", limit: int = 500
             ) -> list[dict]:
        q, args = "SELECT * FROM recs WHERE 1=1", []
        if league:
            q += " AND league=?"
            args.append(league)
        if period is not None:
            q += " AND period=?"
            args.append(period)
        if where:
            q += " AND " + where
        q += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        with self.lock:
            return [dict(r) for r in self.db.execute(q, args)]

    def update_rec(self, rec_id: int, **cols) -> None:
        if not cols:
            return
        sets = ", ".join(f"{k}=?" for k in cols)
        with self.lock:
            self.db.execute(f"UPDATE recs SET {sets} WHERE id=?", (*cols.values(), rec_id))
            self.db.commit()

    # ---- observations (model accuracy) ---------------------------------------------------------------------
    def add_obs(self, rows: list[dict]) -> int:
        if not rows:
            return 0
        cols = ["sp", "pid", "period", "season", "last7", "last15", "proj", "mpg", "mpg3", "mpg5", "fppm", "value"]
        with self.lock:
            cur = self.db.executemany(
                f"INSERT OR IGNORE INTO obs({', '.join(cols)}) VALUES({', '.join('?' * len(cols))})",
                [tuple(r.get(c) for c in cols) for r in rows])
            self.db.commit()
            return cur.rowcount

    def open_obs(self, before_sp: int) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.db.execute("SELECT * FROM obs WHERE played IS NULL AND sp<?", (before_sp,))]

    def set_obs_actual(self, sp: int, pid: int, actual: float | None, played: bool) -> None:
        with self.lock:
            self.db.execute("UPDATE obs SET actual=?, played=? WHERE sp=? AND pid=?", (actual, int(played), sp, pid))
            self.db.commit()

    def obs(self, where: str = "played=1", args: tuple = ()) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.db.execute(f"SELECT * FROM obs WHERE {where}", args)]

    def copy(self) -> "Store":
        """In-memory copy (CLI `tick` / `bot` must not consume the running server's alarms)."""
        other = Store(":memory:")
        with self.lock:
            self.db.backup(other.db)
        return other

    def close(self) -> None:
        with self.lock:
            self.db.close()
