"""The always-on server on recorded data: dedupe and grouping, the cookie alarm, ESPN down, the bot."""
import datetime as dt

import pytest
from conftest import FIX11, NOW11

from radar.alarms import Event
from radar.espn.client import EspnAuthError, EspnError, FixtureClient
from radar.serve import Server
from radar.store import Store


class Flaky(FixtureClient):
    """Fixture client whose private league can fail with 401 and whose whole ESPN can go down."""

    def __init__(self, *a, auth=False, down=False, **kw):
        super().__init__(*a, **kw)
        self.auth, self.down = auth, down

    def pro_schedule(self):
        if self.down:
            raise EspnError("mreža: ConnectionError")
        return super().pro_schedule()

    def league(self, league_id, private, key=""):
        if key == "banijska":
            if self.auth:
                raise EspnAuthError("ESPN 401", 401)
            return super().league(league_id, private, "legendi")       # stand-in so both leagues work
        return super().league(league_id, private, key)

    def player_pool(self, league_id, private, key="", **kw):
        return super().player_pool(league_id, private, "legendi", **kw)

    def transactions(self, league_id, private, key="", sp=None):
        return super().transactions(league_id, private, "legendi", sp)


@pytest.fixture()
def env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")


def _server(cfg, tmp_path, clock, client):
    c = cfg.model_copy(deep=True)
    c.server.lokalno = str(tmp_path)
    sent = []
    srv = Server(c, Store(), client_factory=lambda: client, clock=lambda: clock[0], publish=False,
                 send=lambda text: sent.append(text) or True)
    return srv, sent


def test_alarms_are_grouped_and_never_sent_twice(cfg, tmp_path, env):
    clock = [NOW11]
    srv, sent = _server(cfg, tmp_path, clock, FixtureClient(FIX11))
    e1 = Event("drop:LEGENDI:1:23", "legendi", "bacen", "🚨 jedan")
    e2 = Event("hot:LEGENDI:2:4", "legendi", "vatra", "🔥 drugi")
    assert srv.emit([e1, e2]) == 2
    assert len(sent) == 1 and "🚨 jedan" in sent[0] and "🔥 drugi" in sent[0]
    assert sent[0].startswith("🔔 Radar · podaci od ")
    assert srv.emit([e1]) == 0 and len(sent) == 1                    # same key: never again
    assert [a["kind"] for a in srv.store.recent_alerts()] == ["vatra", "bacen"]


def test_silence_records_but_does_not_send(cfg, tmp_path, env):
    clock = [NOW11]
    srv, sent = _server(cfg, tmp_path, clock, FixtureClient(FIX11))
    srv.store.set("tisina_do", (NOW11 + dt.timedelta(hours=2)).isoformat())
    assert srv.emit([Event("k1", "legendi", "vatra", "x")]) == 0 and sent == []
    assert srv.store.recent_alerts()[0]["sent"] == 2
    clock[0] = NOW11 + dt.timedelta(hours=3)
    srv.retry_unsent(clock[0])
    assert sent == []                                                 # silenced alarms don't come back later


def test_two_passes_on_same_data_raise_no_alarms(cfg, tmp_path, env):
    clock = [NOW11]
    srv, sent = _server(cfg, tmp_path, clock, Flaky(FIX11))
    first = srv.tick()
    assert [e for e in first if e.kind != "pouzdanost"] == []
    assert srv.res.analysis("legendi").win_prob is not None
    assert srv.store.get("plan:legendi")["steps"]
    clock[0] += dt.timedelta(minutes=15)
    assert srv.tick() == []
    assert (tmp_path / "radar.json").exists()


def test_cookie_alarm_once_until_fixed(cfg, tmp_path, env):
    clock = [NOW11]
    client = Flaky(FIX11, auth=True)
    srv, sent = _server(cfg, tmp_path, clock, client)
    ev = srv.tick()
    cookie = [e for e in ev if e.kind == "pouzdanost"]
    assert len(cookie) == 1 and "BANIJSKA: ESPN kolačići" in cookie[0].text and "F12" in cookie[0].text
    assert srv.res.analysis("legendi") is not None                    # the public league keeps working
    clock[0] += dt.timedelta(minutes=15)
    assert [e for e in srv.tick() if e.kind == "pouzdanost"] == []    # not again while broken
    client.auth = False
    clock[0] += dt.timedelta(minutes=15)
    ok = [e for e in srv.tick() if e.kind == "pouzdanost"]
    assert len(ok) == 1 and ok[0].text.startswith("✅ BANIJSKA")


def test_espn_down_alarm_after_30_minutes_once(cfg, tmp_path, env):
    clock = [NOW11]
    client = Flaky(FIX11, down=True)
    srv, sent = _server(cfg, tmp_path, clock, client)
    assert srv.tick() == []
    clock[0] += dt.timedelta(minutes=20)
    assert srv.tick() == []
    clock[0] += dt.timedelta(minutes=15)
    ev = srv.tick()
    assert len(ev) == 1 and ev[0].text.startswith("⚠️ ESPN ne odgovara od 20:40")
    clock[0] += dt.timedelta(minutes=15)
    assert srv.tick() == []
    client.down = False
    clock[0] += dt.timedelta(minutes=15)
    back = [e for e in srv.tick() if e.kind == "pouzdanost"]
    assert len(back) == 1 and back[0].text.startswith("✅ ESPN opet radi")


def test_bot_answers_only_my_chat(cfg, tmp_path, env):
    clock = [NOW11]
    srv, sent = _server(cfg, tmp_path, clock, Flaky(FIX11))
    srv.tick()
    srv.handle_updates([{"update_id": 5, "message": {"chat": {"id": 999}, "text": "/legendi"}}])
    assert sent == []
    srv.handle_updates([{"update_id": 6, "message": {"chat": {"id": 42}, "text": "/legendi"}}])
    assert len(sent) == 1 and sent[0].startswith("🏀 LEGENDI — ") and "Šansa za pobjedu" in sent[0]
    assert srv.store.get("tg_offset") == 7


@pytest.mark.parametrize("cmd,expect", [
    ("/pomoc", "/legendi, /banijska, /sve"),
    ("/danas", "📋 Danas (sri 11.11.)"),
    ("/plan", "🗒 LEGENDI — plan (tjedan 4"),
    ("/igrac Stirtz", "👤 Bennett Stirtz (OKC, PG)"),
    ("/usporedi Stirtz Clayton", "⚖️ Bennett Stirtz vs Walter Clayton Jr."),
    ("/pratim Wiggins", "👀 Pratim Andrew Wiggins"),
    ("/tisina 2h", "🔕 Alarmi utišani do 22:40"),
    ("/cilj legendi dugorocno", "LEGENDI: cilj do kraja tjedna 4 → dugoročno"),
    ("/nepostoji", "Ne znam tu naredbu"),
])
def test_bot_commands(cfg, tmp_path, env, cmd, expect):
    clock = [NOW11]
    srv, sent = _server(cfg, tmp_path, clock, Flaky(FIX11))
    srv.tick()
    out = srv.answer(cmd)
    assert expect in out, out
    if cmd.startswith("/cilj"):
        assert srv.res.analysis("legendi").mode == "dugorocno"
        assert srv.store.get("cilj:legendi:4") == "dugorocno"
    if cmd.startswith("/pratim"):
        assert srv.store.get("watch") == {"3059319": "Andrew Wiggins"}
        assert "waiver do" in out
