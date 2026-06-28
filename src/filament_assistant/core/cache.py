import diskcache

from filament_assistant.config import get_settings

_cache: diskcache.Cache | None = None


def get_cache() -> diskcache.Cache:
    global _cache
    if _cache is None:
        _cache = diskcache.Cache(get_settings().cache_dir)
    return _cache


def cache_get(key: str):
    return get_cache().get(key)


def cache_set(key: str, value, ttl: int | None = None) -> None:
    get_cache().set(key, value, expire=ttl)


def cache_delete(key: str) -> None:
    get_cache().delete(key)
