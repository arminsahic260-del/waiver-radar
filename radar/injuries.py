"""'Out until' remembered across runs (from news), and the teammates who inherit the minutes."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from pydantic import BaseModel

from .espn.maps import PRO_TEAM
from .models import Player, short_name
from .news import NewsItem, OutInfo, about_player, out_info
from .value import Signal, value_now


class OutEntry(BaseModel):
    player_id: int
    name: str
    pro_team_id: int
    positions: list[str]
    value: float
    info: OutInfo
    headline: str = ""


def update_state(state: dict[int, OutEntry], players: dict[int, Player], news: dict[int, list[NewsItem]],
                 today: dt.date, lookback_days: int = 21) -> dict[int, OutEntry]:
    """Keep/refresh entries for players who are OUT; forget players who are back or long past their date."""
    out = {}
    for pid, e in state.items():
        p = players.get(pid)
        if p is not None and not p.is_out:
            continue            # back
        if e.info.max_date < today - dt.timedelta(days=7):
            continue            # stale
        out[pid] = e
    for pid, items in news.items():
        p = players.get(pid)
        if p is None or not p.is_out:
            continue
        for n in items:
            if (today - n.published.date()).days > lookback_days:
                break
            if not about_player(n, p.short):
                continue
            info = out_info(n)
            if info:
                old = out.get(pid)
                if old is None or info.since >= old.info.since:
                    out[pid] = OutEntry(player_id=pid, name=p.name, pro_team_id=p.pro_team_id,
                                        positions=sorted(p.pos_set), value=value_now(p), info=info,
                                        headline=n.headline)
                break
    return out


def is_long(e: OutEntry, today: dt.date, min_days: int = 7) -> bool:
    return e.info.kind in ("season", "indefinite") or (e.info.max_date - today).days >= min_days


@dataclass
class Opening:
    """Teammates of one of MY players who are hurt right now: their minutes/shots go to him (12.11.: Ziaire
    Williams was proposed as a drop while Reaves, Dončić, Grimes and Sexton were all DTD)."""
    fp: float               # extra FP/game of protection when he is a drop candidate
    text: str               # 'LAL: Reaves DTD, Dončić DTD'
    mates: list[int] = field(default_factory=list)


def opening(p: Player, pool: dict[int, Player], state: dict[int, OutEntry], today_sp: int,
            min_value: float = 18, out_fp: float = 3.0, dtd_fp: float = 1.5, cap: float = 6.0,
            fresh_days: int = 10) -> Opening | None:
    """Teammates worth `min_value`+ who are OUT or DTD and played in the last `fresh_days` (a long absence is
    already in his own numbers). OUT counts `out_fp`, DTD `dtd_fp`, together at most `cap` FP/game."""
    if p.pro_team_id not in PRO_TEAM:
        return None
    mates = [q for q in pool.values()
             if q.id != p.id and q.pro_team_id == p.pro_team_id and (q.is_out or q.is_dtd)
             and q.games and today_sp - q.games[-1].sp <= fresh_days and value_now(q) >= min_value]
    if not mates:
        return None
    mates.sort(key=lambda q: -value_now(q))
    fp = min(cap, sum(out_fp if q.is_out else dtd_fp for q in mates))
    bits = []
    for q in mates[:4]:
        e = state.get(q.id)
        bits.append(f"{q.short} OUT{' ' + e.info.label() if e else ''}" if q.is_out else f"{q.short} DTD")
    more = f" +{len(mates) - 4}" if len(mates) > 4 else ""
    return Opening(fp=round(fp, 1), text=f"{p.team}: {', '.join(bits)}{more}", mates=[q.id for q in mates])


def teammate_signals(free_agents: list[Player], state: dict[int, OutEntry], today: dt.date,
                     long_value: dict[int, float], min_injured_value: float = 18, per_injury: int = 2
                     ) -> dict[int, Signal]:
    """For each long-term OUT player who matters, flag the best 1-2 free teammates who share a position."""
    sig: dict[int, Signal] = {}
    for e in sorted(state.values(), key=lambda x: -x.value):
        if not is_long(e, today) or e.value < min_injured_value:
            continue
        mates = [p for p in free_agents if p.pro_team_id == e.pro_team_id and p.pos_set & set(e.positions)
                 and not p.is_out]
        mates.sort(key=lambda p: -long_value.get(p.id, 0))
        for p in mates[:per_injury]:
            last = short_name(e.name)
            sig.setdefault(p.id, Signal("teammate", f"{last} OUT {e.info.label()}", None, e.info.text))
    return sig
