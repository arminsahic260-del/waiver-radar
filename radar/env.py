"""Local secrets for the PC server: KEY=VALUE lines in .env (git-ignored). Real environment variables win unless
`override`. Never logs values."""
from __future__ import annotations

import os
from pathlib import Path

from .config import ROOT

ENV_FILE = ROOT / ".env"
KEYS = ("ESPN_S2", "ESPN_SWID", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "ANTHROPIC_API_KEY", "GITHUB_TOKEN")


def parse(text: str) -> dict[str, str]:
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip().removeprefix("export ").strip()
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        out[k] = v
    return out


def load(path: Path = ENV_FILE, override: bool = False) -> list[str]:
    """Loads .env into os.environ; returns the keys it set (names only)."""
    try:
        data = parse(path.read_text(encoding="utf-8-sig"))
    except OSError:
        return []
    done = []
    for k, v in data.items():
        if override or not os.environ.get(k):
            if v:
                os.environ[k] = v
                done.append(k)
            elif override and k in os.environ and k in KEYS:
                del os.environ[k]
    return done


def set_key(key: str, value: str, path: Path = ENV_FILE) -> None:
    """Writes/replaces one KEY=VALUE line (used by `radar telegram` to save the chat id)."""
    lines = path.read_text(encoding="utf-8-sig").splitlines() if path.exists() else []
    out, done = [], False
    for line in lines:
        if line.split("=", 1)[0].strip() == key:
            out.append(f"{key}={value}")
            done = True
        else:
            out.append(line)
    if not done:
        out.append(f"{key}={value}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    os.environ[key] = value


def mtime(path: Path = ENV_FILE) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0
