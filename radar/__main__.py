"""CLI.

  python -m radar serve                                   stalni proces na PC-u: alarmi, bot, dashboard
  python -m radar telegram                                spremi TELEGRAM_CHAT_ID (pošalji botu poruku)
  python -m radar check [legendi|banijska|all] [--detalji] [--fixtures DIR] [--sada 2026-11-08T08:30]
  python -m radar tick [--telegram]                       jedan prolaz kao server: alarmi u terminal
  python -m radar bot "/legendi"                          odgovor bota u terminal (bez Telegrama)
  python -m radar build [--telegram auto|uvijek|ne]       GitHub Actions: dashboard JSON + Telegram (rezerva)
  python -m radar record DIR                              snimi stvarne ESPN odgovore kao fixtures
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys
from zoneinfo import ZoneInfo

from .config import load_config


def _client(cfg, fixtures=None, record=None):
    from .espn.client import EspnClient, FixtureClient, cookies_from_env
    if fixtures:
        return FixtureClient(fixtures, cfg.sezona)
    return EspnClient(cfg.sezona, cookies=cookies_from_env(), record_dir=record)


def _now(cfg, s: str | None) -> dt.datetime:
    if not s:
        return dt.datetime.now(dt.timezone.utc)
    t = dt.datetime.fromisoformat(s)
    return t if t.tzinfo else t.replace(tzinfo=ZoneInfo(cfg.vremenska_zona))


def _keys(cfg, which: str) -> list[str] | None:
    if which in ("all", "sve", ""):
        return None
    keys = [lc.kljuc for lc in cfg.lige]
    if which not in keys:
        raise SystemExit(f"nepoznata liga '{which}' (imam: {', '.join(keys)}, all)")
    return [which]


def _telegram_setup(cfg) -> int:
    import time
    from . import env, telegram
    if not telegram._api("getMe"):
        print("Nema TELEGRAM_BOT_TOKEN. U Telegramu: @BotFather → /newbot, pa token upiši u .env:\n"
              "  TELEGRAM_BOT_TOKEN=123456:ABC...")
        return 1
    me = telegram.get_me()
    if not me:
        print("Telegram ne prihvaća token (provjeri TELEGRAM_BOT_TOKEN u .env).")
        return 1
    telegram.delete_webhook()
    print(f"Bot @{me.get('username')} radi. Pošalji mu bilo koju poruku u Telegramu (npr. /start); čekam 3 min…")
    offset, deadline = None, time.time() + 180
    while time.time() < deadline:
        for u in telegram.get_updates(offset, timeout=20):
            offset = u["update_id"] + 1
            chat = (u.get("message") or {}).get("chat") or {}
            if chat.get("type") == "private" and chat.get("id"):
                env.set_key("TELEGRAM_CHAT_ID", str(chat["id"]))
                telegram.get_updates(offset, timeout=0)          # acknowledge
                telegram.send("✅ Waiver Radar je spojen. /pomoc za naredbe.")
                print(f"Spremljeno u .env: TELEGRAM_CHAT_ID={chat['id']} ({chat.get('first_name', '')})")
                return 0
    print("Nije stigla poruka. Pokreni ponovno i pošalji botu poruku.")
    return 1


def main(argv=None) -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(prog="radar", description="Waiver Radar 2.1")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--config")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve", help="stalni proces: alarmi, provjere prije utakmica, bot, dashboard")
    sub.add_parser("telegram", help="spoji Telegram bota (spremi chat id u .env)")
    c = sub.add_parser("check", help="izvještaj odmah u terminal")
    c.add_argument("liga", nargs="?", default="all")
    c.add_argument("-d", "--detalji", action="store_true", help="i rosteri, tablica po danima, liste")
    c.add_argument("--fixtures", help="čitaj snimljene odgovore iz mape umjesto ESPN-a")
    c.add_argument("--sada", help="ISO vrijeme (po config vremenskoj zoni) za koje se računa")
    t = sub.add_parser("tick", help="jedan prolaz kao server (alarmi u terminal)")
    t.add_argument("--telegram", action="store_true", help="i pošalji alarme na Telegram")
    bt = sub.add_parser("bot", help="odgovor na naredbu bota u terminal")
    bt.add_argument("naredba", nargs="+")
    b = sub.add_parser("build", help="puni prolaz: site/data/*.json (+ Telegram)")
    b.add_argument("--telegram", choices=["auto", "uvijek", "ne"], default="ne")
    b.add_argument("--fixtures")
    b.add_argument("--sada")
    b.add_argument("--preskoci-ako-pc", action="store_true",
                   help="ne radi ništa ako je PC nedavno objavio podatke (GitHub Actions rezerva)")
    r = sub.add_parser("record", help="snimi ESPN odgovore (bez cookieja i imena članova lige)")
    r.add_argument("dir")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(message)s")
    cfg = load_config(args.config)

    from . import env
    env.load()
    if args.cmd == "serve":
        from .serve import serve
        return serve(cfg)
    if args.cmd == "telegram":
        return _telegram_setup(cfg)

    from .build import run
    if args.cmd == "check":
        from .report import detail_report, full_report
        from .store import Store
        store = None if args.fixtures else Store(cfg.local_dir / "radar.db")
        res = run(cfg, _client(cfg, args.fixtures), _now(cfg, args.sada), _keys(cfg, args.liga),
                  state_path=None if args.fixtures else cfg.local_dir / "state.json", store=store)
        print(full_report(res.analyses, res.errors, tz=cfg.vremenska_zona, now=res.now))
        if args.detalji:
            for a in res.analyses:
                print("\n" + detail_report(a))
        return 0
    if args.cmd in ("tick", "bot"):
        from .serve import Server
        from .store import Store
        store = Store(cfg.local_dir / "radar.db").copy()       # never eat the running server's alarms
        srv = Server(cfg, store, publish=False, send=(lambda text: print("\n[Telegram]\n" + text) or True)
                     if not getattr(args, "telegram", False) else None)
        if args.cmd == "bot":
            srv.tick("bot")
            print(srv.answer(" ".join(args.naredba)))
            return 0
        events = srv.tick("ručno")
        if args.telegram:
            n = srv.emit(events)
            print(f"{len(events)} alarm(a), poslano novih: {n}")
        for e in events:
            print(f"[{e.kind}] {e.text}")
        if not events:
            print("Nema novih alarma (prvi prolaz samo pamti stanje).")
        return 0
    if args.cmd == "record":
        res = run(cfg, _client(cfg, record=args.dir), state_path=None)
        print(f"snimljeno u {args.dir}: {len(res.analyses)} lig(e), greške: {list(res.errors) or 'nema'}")
        return 0
    if args.cmd == "build":
        from .publish import publish, pc_is_fresh
        if args.preskoci_ako_pc:
            fresh = pc_is_fresh(cfg)
            if fresh:
                print(f"PC radar radi (podaci od {fresh}), GitHub prolaz se preskače.")
                return 0
        return publish(cfg, _client(cfg, args.fixtures), _now(cfg, args.sada), args.telegram)
    return 1


if __name__ == "__main__":
    sys.exit(main())
