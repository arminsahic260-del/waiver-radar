"""p_play: how likely a player really plays a given game (status, fresh news, back-to-back, age)."""
from __future__ import annotations

import datetime as dt

from .config import PPlayCfg
from .models import Player
from .schedule import ProSchedule


def back_to_back(schedule: ProSchedule, pro_team_id: int, sp: int) -> bool:
    """His team plays on day `sp` and also played the day before."""
    return schedule.plays(pro_team_id, sp) and schedule.plays(pro_team_id, sp - 1)


def p_play(p: Player, sp: int, today_sp: int, schedule: ProSchedule, cfg: PPlayCfg,
           flag: str | None = None, back: dt.date | None = None, age: int | None = None) -> float:
    """Probability he plays on day `sp`.

    `flag`: 'upitan' / 'out' from news of the last 24 h (only for today and tomorrow); `back`: expected return date
    of an OUT player (from news); `age`: years, None when unknown."""
    soon = sp <= today_sp + 1
    if p.is_out:
        if back is None or schedule.date(sp) <= back:
            return 0.0
        base = cfg.povratak
    elif flag == "out" and sp <= today_sp:
        return 0.0
    elif p.is_dtd:
        base = cfg.dtd if soon else cfg.dtd_kasnije
    elif flag == "upitan" and soon:
        base = cfg.upitan
    else:
        base = cfg.active
    if back_to_back(schedule, p.pro_team_id, sp):
        risky = p.is_dtd or p.is_out or (flag in ("upitan", "out") and soon) or (age or 0) >= cfg.b2b_dob
        base -= cfg.b2b_rizik if risky else cfg.b2b
    return round(max(0.0, min(1.0, base)), 3)


def age_from_bio(bio: dict | None, today: dt.date) -> int | None:
    if not bio:
        return None
    if bio.get("dateOfBirth"):
        try:
            dob = dt.date.fromisoformat(str(bio["dateOfBirth"])[:10])
            return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
        except ValueError:
            pass
    a = bio.get("age")
    return int(a) if isinstance(a, (int, float)) else None
