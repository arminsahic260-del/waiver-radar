"""Telegram push after a refresh: new starters / opportunities and new Out injuries of my players."""
import os
import sys

import requests

from analyze import d_hr, injury_hr
from common import norm_name

PRI = {1: "Uzmi odmah", 2: "Stream / stash", 3: "Špekulacija"}


def compose(cfg, prev, cur):
    """Message text, or None when nothing new happened since the previous snapshot."""
    if not prev.get("waiver.json"):
        return None  # first run: everything would look "new"
    prev_p1 = {norm_name(p["player"]) for p in prev["waiver.json"].get("picks", []) if p["priority"] == 1}
    picks = (cur.get("waiver.json") or {}).get("picks", [])
    starters = [p for p in picks if p["priority"] == 1 and norm_name(p["player"]) not in prev_p1]

    mine = {norm_name(p["name"]) for L in (cur.get("rosters.json") or {}).get("leagues", []) for p in L["players"]}
    prev_out = {norm_name(i["player"]) for i in (prev.get("injuries.json") or {}).get("items", []) if i["kind"] != "gtd"}
    my_out = [i for i in (cur.get("injuries.json") or {}).get("items", [])
              if i["kind"] != "gtd" and norm_name(i["player"]) in mine and norm_name(i["player"]) not in prev_out]
    if not starters and not my_out:
        return None

    lines = ["🏀 Waiver Radar"]
    if starters:
        lines += ["", "Novi starteri / prilike:"]
        lines += [f"• {p['player']} ({p['team']}) — {p.get('role') or p['reason'][:80]}" for p in starters[:6]]
    if my_out:
        lines += ["", "Moji igrači:"]
        for i in my_out:
            when = "za sezonu" if i["kind"] == "out_season" else (f"do ~{d_hr(i['ret'])}" if i.get("ret") else "")
            lines.append(f"• {i['player']} ({i['team']}): out {when} ({injury_hr(i['injury'])})".replace("  ", " "))
    if picks:
        lines += ["", "Top waiver:"]
        lines += [f"{n}. {p['player']} ({p['team']}) · {PRI[p['priority']]}" + (f" · {p['note']}" if p.get("note") else "")
                  for n, p in enumerate(picks[:3], 1)]
    if cfg.get("siteUrl"):
        lines += ["", cfg["siteUrl"]]
    return "\n".join(lines)


def send(text):
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        print("  (Telegram nije postavljen — preskačem)", file=sys.stderr)
        return False
    r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", timeout=20,
                      json={"chat_id": chat, "text": text, "disable_web_page_preview": True})
    r.raise_for_status()
    return True


def run(cfg, prev, cur):
    text = compose(cfg, prev, cur)
    if text:
        print(text)
        return send(text)
    return False
