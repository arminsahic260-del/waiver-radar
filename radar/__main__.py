"""CLI.

  python -m radar check [legendi|banijska|all] [--detalji] [--fixtures DIR] [--sada 2026-11-08T08:30]
  python -m radar build [--telegram auto|uvijek|ne]       (GitHub Actions: dashboard JSON + Telegram)
  python -m radar record DIR                              (snimi stvarne ESPN odgovore kao fixtures)
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


def main(argv=None) -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(prog="radar", description="Waiver Radar 2")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--config")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="izvještaj odmah u terminal")
    c.add_argument("liga", nargs="?", default="all")
    c.add_argument("-d", "--detalji", action="store_true", help="i rosteri, tablica po danima, liste")
    c.add_argument("--fixtures", help="čitaj snimljene odgovore iz mape umjesto ESPN-a")
    c.add_argument("--sada", help="ISO vrijeme (po config vremenskoj zoni) za koje se računa")
    b = sub.add_parser("build", help="puni prolaz: site/data/*.json (+ Telegram)")
    b.add_argument("--telegram", choices=["auto", "uvijek", "ne"], default="ne")
    b.add_argument("--fixtures")
    b.add_argument("--sada")
    r = sub.add_parser("record", help="snimi ESPN odgovore (bez cookieja i imena članova lige)")
    r.add_argument("dir")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(message)s")
    cfg = load_config(args.config)

    from .build import STATE, run
    if args.cmd == "check":
        from .report import detail_report, full_report
        res = run(cfg, _client(cfg, args.fixtures), _now(cfg, args.sada), _keys(cfg, args.liga),
                  state_path=None if args.fixtures else STATE)
        print(full_report(res.analyses, res.errors, tz=cfg.vremenska_zona))
        if args.detalji:
            for a in res.analyses:
                print("\n" + detail_report(a))
        return 0
    if args.cmd == "record":
        res = run(cfg, _client(cfg, record=args.dir), state_path=None)
        print(f"snimljeno u {args.dir}: {len(res.analyses)} lig(e), greške: {list(res.errors) or 'nema'}")
        return 0
    if args.cmd == "build":
        from .publish import publish
        return publish(cfg, _client(cfg, args.fixtures), _now(cfg, args.sada), args.telegram)
    return 1


if __name__ == "__main__":
    sys.exit(main())
