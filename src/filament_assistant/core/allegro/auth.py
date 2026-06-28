import base64
import logging

import httpx

from filament_assistant.config import Settings
from filament_assistant.core.cache import cache_get, cache_set

logger = logging.getLogger(__name__)

_CACHE_KEY = "allegro_token"
# Refresh the token this many seconds before it actually expires.
_REFRESH_MARGIN_S = 60


def _encode_credentials(client_id: str, client_secret: str) -> str:
    raw = f"{client_id}:{client_secret}"
    return base64.b64encode(raw.encode()).decode()


async def get_access_token(settings: Settings) -> str:
    cached = cache_get(_CACHE_KEY)
    if cached is not None:
        return cached

    token = await _fetch_token(settings)
    return token


async def _fetch_token(settings: Settings) -> str:
    url = f"{settings.auth_base}/auth/oauth/token"
    headers = {
        "Authorization": f"Basic {_encode_credentials(settings.allegro_client_id, settings.allegro_client_secret)}",  # noqa: E501
        "Content-Type": "application/x-www-form-urlencoded",
    }
    data = {"grant_type": "client_credentials"}

    async with httpx.AsyncClient() as client:
        response = await client.post(url, headers=headers, data=data)
        response.raise_for_status()

    body = response.json()
    token: str = body["access_token"]
    expires_in: int = body.get("expires_in", 3600)

    ttl = max(expires_in - _REFRESH_MARGIN_S, 60)
    cache_set(_CACHE_KEY, token, ttl=ttl)
    logger.debug("Fetched new Allegro token, expires in %ds (cached for %ds)", expires_in, ttl)
    return token


async def invalidate_token() -> None:
    from filament_assistant.core.cache import cache_delete
    cache_delete(_CACHE_KEY)
    logger.debug("Allegro token invalidated")
