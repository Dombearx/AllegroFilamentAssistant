import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from filament_assistant.core.cache import cache_get, cache_set

_HISTORY_KEY = "search_history"
_MAX_ENTRIES = 10


@dataclass
class SearchHistoryEntry:
    timestamp: str          # ISO-8601 UTC
    target_hex: str
    brand_ids: list[str]
    brand_names: list[str]
    type_ids: list[str]
    type_names: list[str]
    threshold: float
    result_count: int


def save_search(entry: SearchHistoryEntry) -> None:
    history = load_history()
    history.insert(0, entry)
    cache_set(_HISTORY_KEY, json.dumps([asdict(e) for e in history[:_MAX_ENTRIES]]))


def load_history() -> list[SearchHistoryEntry]:
    raw = cache_get(_HISTORY_KEY)
    if not raw:
        return []
    return [SearchHistoryEntry(**e) for e in json.loads(raw)]


def time_ago(iso_ts: str) -> str:
    then = datetime.fromisoformat(iso_ts)
    diff = int((datetime.now(UTC) - then).total_seconds())
    if diff < 60:
        return "just now"
    if diff < 3600:
        return f"{diff // 60}m ago"
    if diff < 86400:
        return f"{diff // 3600}h ago"
    return f"{diff // 86400}d ago"
