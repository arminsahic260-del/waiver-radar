"""End-to-end on the recorded Sunday 8.11.2026 (Liga Legendi, 16:19 Zagreb)."""
from radar.report import full_report, league_report


def test_today_counts_and_score(legendi):
    a = legendi
    assert (a.period, a.start_sp, a.end_sp, a.today_sp) == (3, 14, 20, 20)
    today = a.row(20)
    assert not today.past
    assert (today.my.n, today.opp.n) == (4, 7)
    assert {a.values[p].player.short for p, _ in today.my.starters} == {"Irving", "Flagg", "Reed", "Acuff Jr."}
    assert a.my_proj == round(a.my_live + today.my.total, 1)
    assert a.row(15).past and a.row(15).my_actual == 0.0      # 3.11.: no games


def test_out_player_in_lineup_is_flagged(legendi):
    assert "⚠️ Brown Jr. OUT u postavi (UTIL)" in legendi.alerts


def test_plan_uses_todays_games_and_remaining_adds(legendi):
    a = legendi
    assert a.adds_used == 5 and a.adds_limit == 7
    assert 1 <= len(a.plan) <= 2
    for m in a.plan:
        assert m.t == 20 and m.gain >= 6
        assert a.schedule.plays(a.values[m.add].player.pro_team_id, 20)


def test_report_text(legendi, run_result):
    text = league_report(legendi)
    assert text.startswith("🏀 LEGENDI — Druzina gospodara Flagga vs Bajka Basna: 1129 : 1201 (proj. ")
    assert "Danas: ti 4 | oni 7" in text
    assert text.endswith("Preuzimanja: 5/7 (protivnik 2/7)")
    full = full_report(run_result.analyses, run_result.errors)
    assert "🏀 BANIJSKA — ⚠️" in full                         # no recorded data for the private league


def test_hot_free_agents_have_reasons(legendi):
    hot = [f for f in legendi.free if legendi.values[f.pid].hot]
    assert hot
    assert all(legendi.values[f.pid].reason() for f in hot)
    assert all(not legendi.values[f.pid].player.is_out for f in legendi.free)
