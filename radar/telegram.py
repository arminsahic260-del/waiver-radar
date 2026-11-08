"""Telegram: plain-text message (no parse_mode, so player names never break Markdown). Token/chat id from env only."""
from __future__ import annotations

import logging
import os

import requests

log = logging.getLogger("radar.telegram")
LIMIT = 4000   # Telegram allows 4096 characters per message


def configured() -> bool:
    return bool(os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"))


def chunks(text: str, limit: int = LIMIT) -> list[str]:
    """Split on blank lines (one league per message if needed), then on lines."""
    out, cur = [], ""
    for block in text.split("\n\n"):
        cand = f"{cur}\n\n{block}" if cur else block
        if len(cand) <= limit:
            cur = cand
            continue
        if cur:
            out.append(cur)
        while len(block) > limit:
            cut = block.rfind("\n", 0, limit)
            cut = cut if cut > 0 else limit
            out.append(block[:cut])
            block = block[cut:].lstrip("\n")
        cur = block
    if cur:
        out.append(cur)
    return out


def send(text: str) -> bool:
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not (token and chat):
        log.warning("Telegram: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID nisu postavljeni")
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    ok = True
    for part in chunks(text):
        try:
            r = requests.post(url, json={"chat_id": chat, "text": part, "disable_web_page_preview": True}, timeout=30)
            if r.status_code != 200:
                # never echo the URL: it contains the token
                try:
                    why = str(r.json().get("description", ""))[:120]
                except ValueError:
                    why = ""
                log.warning("Telegram: HTTP %s %s", r.status_code, why)
                ok = False
        except requests.RequestException as e:
            log.warning("Telegram: %s", type(e).__name__)
            ok = False
    return ok
