"""Report timing (Zagreb, summer/winter time), Telegram splitting, dashboard JSON."""
import datetime as dt
import json

from conftest import NOW

from radar.publish import due_slot, publish
from radar.telegram import chunks

UTC = dt.timezone.utc


def test_due_slot_winter_and_summer(cfg):
    # winter (CET, UTC+1): 08:30 Zagreb = 07:30 UTC, 21:30 = 20:30 UTC
    assert due_slot(cfg, dt.datetime(2026, 11, 9, 7, 30, tzinfo=UTC)) == "jutro"
    assert due_slot(cfg, dt.datetime(2026, 11, 9, 6, 30, tzinfo=UTC)) is None
    assert due_slot(cfg, dt.datetime(2026, 11, 9, 20, 30, tzinfo=UTC)) == "vecer"
    assert due_slot(cfg, dt.datetime(2026, 11, 9, 7, 58, tzinfo=UTC)) == "jutro"     # GitHub ran late
    # summer (CEST, UTC+2): 08:30 Zagreb = 06:30 UTC
    assert due_slot(cfg, dt.datetime(2027, 4, 5, 6, 30, tzinfo=UTC)) == "jutro"
    assert due_slot(cfg, dt.datetime(2027, 4, 5, 19, 30, tzinfo=UTC)) == "vecer"
    assert due_slot(cfg, dt.datetime(2027, 4, 5, 12, 0, tzinfo=UTC)) is None


def test_telegram_chunks():
    text = "\n\n".join(["A" * 3000, "B" * 3000, "C" * 100])
    parts = chunks(text)
    assert len(parts) == 2 and all(len(p) <= 4000 for p in parts)
    assert parts[1].endswith("C" * 100)
    assert chunks("x\n" * 5000)[0].count("x") <= 2000


def test_publish_writes_dashboard_json_and_sends_once(cfg, client, tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr("radar.telegram.send", lambda text: sent.append(text) or True)
    morning = dt.datetime(2026, 11, 8, 7, 35, tzinfo=UTC)
    state = tmp_path / "state.json"
    assert publish(cfg, client, morning, "auto", out_dir=tmp_path, state_path=state) == 0
    assert publish(cfg, client, morning, "auto", out_dir=tmp_path, state_path=state) == 0
    assert len(sent) == 1                                        # same slot, same day: only once
    data = json.loads((tmp_path / "radar.json").read_text(encoding="utf-8"))
    L = data["leagues"][0]
    assert data["slot"] == "jutro" and L["key"] == "legendi"
    assert {"days", "roster_me", "roster_opp", "drops", "pick_week", "pick_long", "plan", "injuries"} <= set(L)
    assert all({"acq", "draft", "avg", "proj", "gp", "slot", "news"} <= set(p) for p in L["roster_me"])
    assert data["errors"][0]["key"] == "banijska"
