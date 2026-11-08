"""Daily lineup optimizer with positions."""
import itertools
import random

from radar.lineup import expand_slots, optimize

SLOTS = {0: 1, 1: 1, 2: 1, 3: 1, 4: 1, 5: 1, 6: 1, 11: 3}
PG, SG, SF, PF, C = [0, 5, 11], [1, 5, 11], [2, 6, 11], [3, 6, 11], [4, 11]


def test_ten_players_fill_ten_slots():
    cands = [(i, e, 30 - i) for i, e in enumerate([PG, SG, SF, PF, C, PG, SF, C, SG, PF])]
    lu = optimize(cands, SLOTS)
    assert lu.filled == 10 and not lu.bench


def test_five_centers_cannot_all_play():
    """C fits only C and 3x UTIL: five centers -> four play, ten names don't mean ten slots."""
    cands = [(f"c{i}", C, 40 - i) for i in range(5)] + [(f"g{i}", PG, 20) for i in range(5)]
    lu = optimize(cands, SLOTS)
    assert sum(1 for k in lu.starters if k.startswith("c")) == 4
    assert "c4" in lu.bench
    assert lu.filled == 6      # 4 C (C + 3x UTIL) + 2 PG (PG, G); SG, SF, PF and F stay empty


def test_flexible_player_moves_to_make_room():
    # a PG/SG sits in PG; a pure PG arriving later must push him to SG
    cands = [("combo", [0, 1, 5, 11], 50), ("pg", [0], 10), ("sg_only", [1], 5)]
    lu = optimize(cands, {0: 1, 1: 1})
    assert set(lu.starters) == {"combo", "pg"}


def _brute(cands, slots):
    inst = expand_slots(slots)
    best = 0.0
    for r in range(min(len(cands), len(inst)), 0, -1):
        for combo in itertools.combinations(cands, r):
            for perm in itertools.permutations(range(len(inst)), r):
                if all(inst[s] in set(c[1]) for c, s in zip(combo, perm)):
                    best = max(best, sum(c[2] for c in combo))
                    break
    return round(best, 2)


def test_matches_brute_force_on_random_rosters():
    rnd = random.Random(7)
    slots = {0: 1, 1: 1, 4: 1, 5: 1, 11: 1}
    pool = [PG, SG, SF, PF, C, [0, 1, 5, 11], [3, 4, 6, 11]]
    for _ in range(40):
        cands = [(i, rnd.choice(pool), round(rnd.uniform(5, 50), 1)) for i in range(rnd.randint(3, 7))]
        assert optimize(cands, slots).total == _brute(cands, slots)
