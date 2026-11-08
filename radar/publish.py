"""`radar build`: run, write site/data/radar.json, send Telegram in the morning/evening slot (once per slot/day)."""
from __future__ import annotations

import datetime as dt
import json
import logging
from pathlib import Path
from zoneinfo import ZoneInfo

from .build import STATE, load_state, run, save_state
from .config import ROOT, Config
from .export import export
from .report import full_report
from . import telegram

log = logging.getLogger("radar")
SITE_DATA = ROOT / "site" / "data"
WINDOW_BEFORE = dt.timedelta(minutes=15)
WINDOW_AFTER = dt.timedelta(minutes=100)   # GitHub's cron can start late


def due_slot(cfg: Config, now: dt.datetime) -> str | None:
    """'jutro' / 'vecer' when `now` (local time) is in that report's window, else None."""
    local = now.astimezone(ZoneInfo(cfg.vremenska_zona))
    for name, hhmm in cfg.izvjestaji.items():
        h, m = (int(x) for x in hhmm.split(":"))
        target = local.replace(hour=h, minute=m, second=0, microsecond=0)
        if target - WINDOW_BEFORE <= local <= target + WINDOW_AFTER:
            return name
    return None


def publish(cfg: Config, client, now: dt.datetime, telegram_mode: str = "auto", out_dir: Path = SITE_DATA,
            state_path: Path = STATE) -> int:
    res = run(cfg, client, now, state_path=state_path)
    slot = due_slot(cfg, now)
    text = full_report(res.analyses, res.errors, cfg.dashboard_url, cfg.vremenska_zona)
    out = out_dir / "radar.json"
    try:
        previous = json.loads(out.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        previous = None
    data = export(res, cfg, slot, text, previous)
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(out)
    print(text)

    state = load_state(state_path)
    sent = state.setdefault("sent", {})
    local_day = now.astimezone(ZoneInfo(cfg.vremenska_zona)).date().isoformat()
    send = telegram_mode == "uvijek" or (telegram_mode == "auto" and slot and sent.get(slot) != local_day)
    if send:
        if telegram.send(text):
            if slot:
                sent[slot] = local_day
                save_state(state, state_path)
            print(f"\n[Telegram poslan{' (' + slot + ')' if slot else ''}]")
        else:
            print("\n[Telegram nije poslan]")
    elif telegram_mode == "auto":
        print(f"\n[Telegram: {'već poslan za ' + slot if slot else 'nije vrijeme izvještaja'}]")
    # a run where every league failed should fail the job (so GitHub e-mails you)
    return 1 if not res.analyses and res.errors else 0
