"""config `ne_bacaj`: players the user never wants dropped (Liga Legendi, 11.11.2026)."""
from radar.alarms import drop_for
from radar.build import run
from radar.config import name_key

from conftest import NOW


def _legendi(cfg, client, names):
    c = cfg.model_copy(deep=True)
    c.league("legendi").ne_bacaj = names
    return next(a for a in run(c, client, NOW, keys=["legendi"], state_path=None).analyses if a.key == "legendi")


def test_name_key():
    assert name_key("Darius Acuff Jr.") == name_key("darius  acuff jr") == "darius acuff jr"
    assert name_key("Luka Dončić") == "luka doncic" and name_key("Kel'el Ware") == "kelel ware"


def test_protected_players_are_never_dropped(cfg, client):
    a0 = _legendi(cfg, client, [])
    victims = {m.drop for m in a0.plan if m.drop} | {a0.drops[0].pid, drop_for(a0, a0.free[0].pid)[0]}
    victims.discard(None)
    assert victims
    a = _legendi(cfg, client, [a0.values[p].player.name for p in victims])
    assert a.keep == victims
    assert not victims & {d.pid for d in a.drops}
    assert not victims & {m.drop for m in a.plan}
    assert drop_for(a, a.free[0].pid)[0] not in victims


def test_config_protects_legendi_core(cfg):
    lc = cfg.league("legendi")
    assert all(lc.keeps(n) for n in ("Cooper Flagg", "Kyrie Irving", "Darius Acuff Jr.", "Trae Young",
                                      "Yaxel Lendeborg", "Fred VanVleet", "Donovan Clingan", "Paolo Banchero", "Rudy Gobert"))
    assert not lc.keeps("Ivica Zubac") and not cfg.league("banijska").ne_bacaj
