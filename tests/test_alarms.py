"""Fast alarms on the recorded Wednesday 11.11.2026 (Liga Legendi): the previous snapshot is edited to stage
what happened between two passes."""
import copy
import dataclasses

import pytest

from radar.alarms import diff, drop_for, snapshot

PORZINGIS, WIGGINS, BLACK, GREEN, MURRAY, GRANT = 3102531, 3059319, 4395650, 6589, 4594327, 2991070
STIRTZ = 5241364


@pytest.fixture()
def snaps(res11, leg11):
    lg = res11.states[0]
    cur = snapshot(lg, leg11)
    return lg, copy.deepcopy(cur), cur


def _kinds(events):
    return [e.kind for e in events]


def test_first_pass_only_remembers(res11, leg11):
    lg = res11.states[0]
    assert diff(None, snapshot(lg, leg11), leg11, lg) == []


def test_nothing_changed_nothing_sent(snaps, leg11):
    lg, prev, cur = snaps
    assert diff(prev, cur, leg11, lg) == []


def test_strong_center_dropped_suggests_a_center(snaps, leg11):
    """Porzingis (C, 34 % owned) dropped by team 5: FA, and with 4 centers on my roster the drop is a center."""
    lg, prev, cur = snaps
    a = dataclasses.replace(leg11, keep=set())                # without the user's ne_bacaj list
    assert cur["players"][str(PORZINGIS)][0] == "FA"
    prev["players"][str(PORZINGIS)] = ["T", 5, "OUT", 0, 34.0]
    ev = diff(prev, cur, a, lg)
    assert _kinds(ev) == ["bacen"]
    t = ev[0].text
    assert "Kristaps Porzingis" in t and "slobodan (FA), uzmi odmah" in t and lg.team_names[5] in t
    assert "imaš 4 centra, limit 4" in t
    drop, why = drop_for(a, PORZINGIS)
    assert a.values[drop].player.is_center
    assert f"baci {a.values[drop].player.short}" in t
    assert ev[0].key.startswith("drop:LEGENDI:3102531:")


def test_center_limit_with_all_centers_protected_suggests_nobody(snaps, leg11):
    """All my centers are on ne_bacaj: dropping a guard would break the 4-center limit, so no drop is suggested."""
    lg, prev, cur = snaps
    centers = {r.player.id for r in leg11.me.roster if r.player.is_center}
    a = dataclasses.replace(leg11, keep=centers)
    prev["players"][str(PORZINGIS)] = ["T", 5, "OUT", 0, 34.0]
    t = diff(prev, cur, a, lg)[0].text
    assert drop_for(a, PORZINGIS)[0] is None
    assert "Za njega baci" not in t and "Nemaš koga baciti" in t and "ne_bacaj" in t


def test_strong_player_on_waivers_says_when_the_claim_runs(snaps, leg11):
    lg, prev, cur = snaps
    assert cur["players"][str(WIGGINS)][0] == "W"
    prev["players"][str(WIGGINS)] = ["T", 9, "ACTIVE", 0, 47.0]
    ev = diff(prev, cur, leg11, lg)
    assert len(ev) == 1 and "Andrew Wiggins" in ev[0].text and "na waiveru, claim prolazi pet " in ev[0].text


def test_weak_drop_is_not_an_alarm(snaps, leg11):
    lg, prev, cur = snaps
    weak = next(int(k) for k, v in cur["players"].items() if v[0] == "FA" and v[4] < 5
                and int(k) not in set(cur["top"]) and not v[3])
    prev["players"][str(weak)] = ["T", 5, "ACTIVE", 0, 2.0]
    assert diff(prev, cur, leg11, lg) == []


def test_my_own_drop_is_not_an_alarm(snaps, leg11):
    lg, prev, cur = snaps
    prev["players"][str(PORZINGIS)] = ["T", lg.me.id, "OUT", 0, 34.0]
    assert diff(prev, cur, leg11, lg) == []


def test_plan_target_taken_proposes_the_reserve(snaps, leg11):
    lg, prev, cur = snaps
    cur["players"][str(BLACK)] = ["T", 7, "ACTIVE", 0, 2.0]
    step = {"kind": "FA", "t": 26, "drop": GREEN, "add": BLACK, "reserves": [MURRAY, GRANT], "days": "ned",
            "name": "Leaky Black"}
    ev = diff(prev, cur, leg11, lg, plan_targets={BLACK: step})
    assert _kinds(ev) == ["otisao"]
    assert ev[0].text.startswith(f"❌ LEGENDI: Leaky Black je otišao ({lg.team_names[7]}) → uzmi Murray (FA, baci Green;")


def test_watchlist_and_my_status(snaps, leg11):
    lg, prev, cur = snaps
    prev["players"][str(STIRTZ)][2] = "DAY_TO_DAY"
    weak = next(int(k) for k, v in cur["players"].items() if v[0] == "FA" and v[4] < 5
                and int(k) not in set(cur["top"]) and not v[3])
    prev["players"][str(weak)] = ["T", 3, cur["players"][str(weak)][2], 0, 2.0]   # small drop: only because I watch him
    ev = diff(prev, cur, leg11, lg, watch={weak: "X"})
    assert sorted(_kinds(ev)) == ["pracenje", "status"]
    st = next(e for e in ev if e.kind == "status")
    assert st.text == "🩹 LEGENDI: Bennett Stirtz (OKC) DTD → zdrav"
    w = next(e for e in ev if e.kind == "pracenje")
    assert w.text.startswith(f"👀 LEGENDI: {lg.players[weak].name} je slobodan (slobodan (FA)")


def test_new_hot_free_agent(snaps, leg11):
    lg, prev, cur = snaps
    hot = [int(k) for k, v in cur["players"].items() if v[0] == "FA" and v[3]]
    assert hot
    pid = max(hot, key=lambda x: leg11.values[x].now)
    prev["players"][str(pid)][3] = 0
    ev = diff(prev, cur, leg11, lg)
    assert _kinds(ev) == ["vatra"] and ev[0].text.startswith(f"🔥 LEGENDI: {leg11.values[pid].player.name}")
    assert ev[0].key == f"hot:LEGENDI:{pid}:4"


def test_opponent_move_that_shifts_the_chance(snaps, leg11):
    lg, prev, cur = snaps
    prev["opp_tx"] = []
    prev["prob"] = cur["prob"] + 0.08
    ev = diff(prev, cur, leg11, lg)
    assert _kinds(ev) == ["protivnik"]
    assert "šansa za pobjedu" in ev[0].text and "njegove izmjene 1/7" in ev[0].text
    prev["prob"] = cur["prob"] + 0.02                           # below 5 points: quiet
    assert diff(prev, cur, leg11, lg) == []
