"""One refresh pass: scrape -> ESPN -> analyse -> meta -> Telegram. Each source fails independently and keeps its
previous snapshot, so a flaky site never blanks the dashboard.

    python scripts/build.py [--no-notify]
"""
import sys
import traceback

import analyze
import espn
import fetch
import notify
from common import data_path, load_config, now_iso, read_json, write_json

FILES = ["injuries.json", "news.json", "schedule.json", "rosters.json", "waiver.json", "meta.json"]


def main(argv):
    cfg = load_config()
    prev = {f: read_json(data_path(f)) for f in FILES}
    prev_sources = (prev["meta.json"] or {}).get("sources", {})
    sources = {}

    def step(name, fn):
        at = now_iso()
        print(f"[{name}]", flush=True)
        try:
            out = fn()
            sources[name] = {"ok": True, "lastOk": at}
            return out
        except Exception as e:
            traceback.print_exc()
            sources[name] = {"ok": False, "lastOk": prev_sources.get(name, {}).get("lastOk"), "error": str(e)[:300]}
            return None

    injuries = step("cbs", lambda: fetch.fetch_injuries(cfg)) or prev["injuries.json"]
    news, fresh = step("rotowire", lambda: fetch.fetch_news(cfg, prev["news.json"])) or (prev["news.json"], [])
    schedule = step("bref", lambda: fetch.fetch_schedule(cfg, prev["schedule.json"])) or prev["schedule.json"]
    rosters, pools = step("espn", lambda: espn.run(cfg, prev["rosters.json"])) or (prev["rosters.json"], espn.load_pools())
    waiver = step("analyze", lambda: analyze.run(cfg, injuries, prev["injuries.json"], news, fresh, schedule,
                                                 rosters, pools)) or prev["waiver.json"]

    if "--no-notify" not in argv:
        cur = {"injuries.json": injuries, "rosters.json": rosters, "waiver.json": waiver}
        step("telegram", lambda: notify.run(cfg, prev, cur))
    write_json(data_path("meta.json"), {"updated": now_iso(), "engine": (waiver or {}).get("engine"),
                                        "sources": sources})

    for k, v in sources.items():
        print(f"  {k:9} {'ok' if v['ok'] else 'GREŠKA: ' + v['error']}")
    data_ok = [sources[k]["ok"] for k in ("cbs", "rotowire", "bref", "espn")]
    return 0 if any(data_ok) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
