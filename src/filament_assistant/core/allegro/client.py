import asyncio
import logging
from typing import Any

import httpx

from filament_assistant.config import Settings
from filament_assistant.core.allegro.auth import get_access_token, invalidate_token

logger = logging.getLogger(__name__)

_ALLEGRO_ACCEPT = "application/vnd.allegro.public.v1+json"
_MAX_RETRIES = 4
_RETRY_STATUSES = {429, 500, 502, 503, 504}


class AllegroClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = httpx.AsyncClient(timeout=30.0)

    async def _headers(self) -> dict[str, str]:
        token = await get_access_token(self._settings)
        return {
            "Authorization": f"Bearer {token}",
            "Accept": _ALLEGRO_ACCEPT,
        }

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{self._settings.api_base}{path}"
        for attempt in range(_MAX_RETRIES):
            headers = await self._headers()
            response = await self._client.get(url, headers=headers, params=params)

            if response.status_code == 401:
                # Token may have been revoked server-side; invalidate and retry once.
                await invalidate_token()
                if attempt == 0:
                    continue
                response.raise_for_status()

            if response.status_code in _RETRY_STATUSES:
                retry_after = int(response.headers.get("Retry-After", 2 ** (attempt + 1)))
                logger.warning(
                    "Allegro %s on %s, waiting %ds (attempt %d/%d)",
                    response.status_code, path, retry_after, attempt + 1, _MAX_RETRIES,
                )
                await asyncio.sleep(retry_after)
                continue

            response.raise_for_status()
            return response.json()

        raise RuntimeError(f"Allegro API request to {path} failed after {_MAX_RETRIES} attempts")

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "AllegroClient":
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.aclose()
