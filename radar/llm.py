"""Optional (config `claude.ukljuceno` + ANTHROPIC_API_KEY): one Claude call that writes a one-sentence Croatian
summary of each fresh news item and says whether it reports a new starter / bigger role. Without it the keyword
heuristic in news.py decides."""
from __future__ import annotations

import datetime as dt
import json
import logging
import os

from .news import NewsItem

log = logging.getLogger("radar.llm")

MAX_ITEMS = 60

SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "sazetak": {"type": "string"},
                    "novi_starter": {"type": "boolean"},
                },
                "required": ["id", "sazetak", "novi_starter"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}

SYSTEM = (
    "You help a fantasy NBA manager (ESPN head-to-head points league) read player news. For every item write one "
    "short sentence in Croatian (ijekavica, plain language, keep player and team names as they are) with what "
    "matters for fantasy: role, minutes, injury and expected return. Set novi_starter to true only when the item "
    "says the player himself is starting now, moved into the starting lineup, or gets a clearly bigger role or "
    "minutes because of a teammate's absence; false for routine box scores, bench roles and injury updates."
)


def available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def pick_items(news: dict[int, list[NewsItem]], names: dict[int, str], now: dt.datetime,
               days: int = 4) -> list[NewsItem]:
    cutoff = now - dt.timedelta(days=days)
    items = [n for pid, ns in news.items() if pid in names for n in ns if n.published >= cutoff]
    items.sort(key=lambda n: n.published, reverse=True)
    return items[:MAX_ITEMS]


def annotate(news: dict[int, list[NewsItem]], names: dict[int, str], model: str, now: dt.datetime) -> int:
    """Fills NewsItem.summary_hr / .starter in place. Returns how many items were annotated."""
    if not available():
        log.info("Claude: nema ANTHROPIC_API_KEY, radi heuristika")
        return 0
    items = pick_items(news, names, now)
    if not items:
        return 0
    import anthropic

    payload = [{"id": n.id, "player": names.get(n.player_id, ""), "published": n.published.date().isoformat(),
                "headline": n.headline, "story": n.story[:900]} for n in items]
    client = anthropic.Anthropic()
    try:
        resp = client.beta.messages.create(
            model=model,
            max_tokens=16000,
            system=SYSTEM,
            messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except anthropic.RateLimitError as e:
        log.warning("Claude: rate limit (%s)", e.status_code)
        return 0
    except anthropic.APIStatusError as e:
        log.warning("Claude: API %s", e.status_code)
        return 0
    except anthropic.APIConnectionError:
        log.warning("Claude: mreža")
        return 0
    if resp.stop_reason == "refusal":
        log.warning("Claude: odbijeno")
        return 0
    text = next((b.text for b in resp.content if b.type == "text"), "")
    try:
        out = json.loads(text)["items"]
    except (ValueError, KeyError, TypeError):
        log.warning("Claude: neispravan JSON")
        return 0
    by_id = {n.id: n for n in items}
    done = 0
    for o in out:
        n = by_id.get(str(o.get("id")))
        if n is None:
            continue
        n.summary_hr = (o.get("sazetak") or "").strip() or None
        n.starter = bool(o.get("novi_starter"))
        done += 1
    return done
