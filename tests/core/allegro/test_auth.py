import httpx
import pytest
import respx

from filament_assistant.config import Settings
from filament_assistant.core.allegro.auth import get_access_token, invalidate_token


def _settings() -> Settings:
    return Settings(
        allegro_client_id="test_id",
        allegro_client_secret="test_secret",
        allegro_env="sandbox",
    )


@pytest.mark.asyncio
async def test_get_access_token_fetches_and_caches():
    settings = _settings()
    token_url = f"{settings.auth_base}/auth/oauth/token"

    with respx.mock:
        respx.post(token_url).mock(
            return_value=httpx.Response(200, json={"access_token": "tok123", "expires_in": 3600})
        )
        token = await get_access_token(settings)
        assert token == "tok123"

        # Second call must use cache — the mock is not called again.
        token2 = await get_access_token(settings)
        assert token2 == "tok123"
        assert respx.calls.call_count == 1


@pytest.mark.asyncio
async def test_get_access_token_raises_on_http_error():
    settings = _settings()
    token_url = f"{settings.auth_base}/auth/oauth/token"

    with respx.mock:
        respx.post(token_url).mock(return_value=httpx.Response(401))
        with pytest.raises(httpx.HTTPStatusError):
            await get_access_token(settings)


@pytest.mark.asyncio
async def test_invalidate_token_clears_cache():
    settings = _settings()
    token_url = f"{settings.auth_base}/auth/oauth/token"

    with respx.mock:
        respx.post(token_url).mock(
            return_value=httpx.Response(200, json={"access_token": "tok_a", "expires_in": 3600})
        )
        await get_access_token(settings)

    await invalidate_token()

    with respx.mock:
        respx.post(token_url).mock(
            return_value=httpx.Response(200, json={"access_token": "tok_b", "expires_in": 3600})
        )
        token = await get_access_token(settings)
        assert token == "tok_b"
