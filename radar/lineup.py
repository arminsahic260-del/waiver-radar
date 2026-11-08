"""Best daily lineup with real positions.

Weights sit on players, not on slots, so the sets of players that can be seated together form a transversal
matroid: taking players by descending value and keeping each one an augmenting path can seat is optimal. Ten
players with games therefore don't always mean ten filled slots (three centers can't all fit PG)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Hashable, Iterable

# fill specific slots before flexible ones so UTIL stays open for whoever needs it
_SLOT_PREF = {0: 0, 1: 0, 2: 0, 3: 0, 4: 0, 7: 1, 8: 1, 9: 1, 10: 1, 5: 2, 6: 2, 11: 3}


@dataclass
class Lineup:
    starters: dict[Hashable, int] = field(default_factory=dict)   # player key -> lineupSlotId
    total: float = 0.0
    bench: list[Hashable] = field(default_factory=list)            # had a game but no slot

    @property
    def filled(self) -> int:
        return len(self.starters)


def expand_slots(slot_counts: dict[int, int]) -> list[int]:
    out = []
    for sid in sorted(slot_counts, key=lambda s: (_SLOT_PREF.get(s, 9), s)):
        out += [sid] * slot_counts[sid]
    return out


def optimize(cands: Iterable[tuple[Hashable, Iterable[int], float]], slot_counts: dict[int, int]) -> Lineup:
    slots = expand_slots(slot_counts)
    items = []
    for key, elig, value in cands:
        e = set(elig)
        adj = [i for i, s in enumerate(slots) if s in e]
        items.append((key, adj, float(value)))
    # most valuable first; on ties the least flexible first
    items.sort(key=lambda x: (-x[2], len(x[1])))
    owner: list[int | None] = [None] * len(slots)

    def augment(pi: int, seen: set[int]) -> bool:
        for si in items[pi][1]:
            if si in seen:
                continue
            seen.add(si)
            if owner[si] is None or augment(owner[si], seen):
                owner[si] = pi
                return True
        return False

    placed = set()
    for pi in range(len(items)):
        if augment(pi, set()):
            placed.add(pi)
        if len(placed) == len(slots):
            # every slot is taken; later (cheaper) players can only displace via augmenting paths that end in a
            # free slot, and there is none left
            break
    res = Lineup()
    for si, pi in enumerate(owner):
        if pi is not None:
            res.starters[items[pi][0]] = slots[si]
            res.total += items[pi][2]
    res.bench = [items[pi][0] for pi in range(len(items)) if pi not in placed]
    res.total = round(res.total, 2)
    return res
