import httpx
import pytest
import respx

from filament_assistant.config import Settings
from filament_assistant.core.allegro.categories import get_filament_filters, search_offers
from filament_assistant.core.allegro.client import AllegroClient


def _settings() -> Settings:
    return Settings(
        allegro_client_id="test_id",
        allegro_client_secret="test_secret",
        allegro_env="sandbox",
    )


# Mirrors the known path: elektronika→komputer→drukark→3d→filament
_PATH_RESPONSES = [
    {"categories": [{"id": "el", "name": "Elektronika"}, {"id": "x", "name": "Inne"}]},
    {"categories": [{"id": "kom", "name": "Komputery"}, {"id": "y", "name": "Inne"}]},
    {"categories": [{"id": "ds", "name": "Drukarki i skanery"}, {"id": "z", "name": "Inne"}]},
    {"categories": [{"id": "d3d", "name": "Drukarki 3D"}, {"id": "w", "name": "Inne"}]},
    {"categories": [{"id": "fil", "name": "Filamenty"}, {"id": "v", "name": "Inne"}]},
]

_PARAMETERS = {
    "parameters": [
        {
            "id": "brand_param",
            "name": "Marka",
            "dictionary": [
                {"id": "bambu", "name": "Bambu Lab"},
                {"id": "prusa", "name": "Prusa"},
            ],
        },
        {
            "id": "type_param",
            "name": "Materiał",
            "dictionary": [
                {"id": "pla", "name": "PLA"},
                {"id": "petg", "name": "PETG"},
            ],
        },
    ]
}

_LISTING = {
    "items": {
        "promoted": [],
        "regular": [
            {
                "id": "offer1",
                "name": "Bambu PLA Red 1kg",
                "images": [{"url": "https://img.example.com/1.jpg"}],
                "sellingMode": {"price": {"amount": "89.99", "currency": "PLN"}},
            }
        ],
    },
    "searchMeta": {"totalCount": 1},
}


def _mock_category_path(api: str) -> None:
    """Return each path step's children in order, keyed by parent.id."""
    _by_parent = {
        None:  _PATH_RESPONSES[0],
        "el":  _PATH_RESPONSES[1],
        "kom": _PATH_RESPONSES[2],
        "ds":  _PATH_RESPONSES[3],
        "d3d": _PATH_RESPONSES[4],
    }

    def dispatcher(request: httpx.Request) -> httpx.Response:
        parent_id = request.url.params.get("parent.id")
        return httpx.Response(200, json=_by_parent[parent_id])

    respx.get(f"{api}/sale/categories").mock(side_effect=dispatcher)


@pytest.mark.asyncio
async def test_get_filament_filters():
    settings = _settings()
    api = settings.api_base
    auth = settings.auth_base

    with respx.mock:
        respx.post(f"{auth}/auth/oauth/token").mock(
            return_value=httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        )
        _mock_category_path(api)
        respx.get(f"{api}/sale/categories/fil/parameters").mock(
            return_value=httpx.Response(200, json=_PARAMETERS)
        )

        async with AllegroClient(settings) as client:
            filters = await get_filament_filters(client)

    assert len(filters.brands) == 2
    assert filters.brands[0].name == "Bambu Lab"
    assert len(filters.types) == 2
    assert filters.types[1].name == "PETG"


@pytest.mark.asyncio
async def test_category_id_cached_permanently():
    """Second call must not hit /sale/categories at all — served from disk cache."""
    settings = _settings()
    api = settings.api_base
    auth = settings.auth_base

    with respx.mock:
        respx.post(f"{auth}/auth/oauth/token").mock(
            return_value=httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        )
        _mock_category_path(api)
        respx.get(f"{api}/sale/categories/fil/parameters").mock(
            return_value=httpx.Response(200, json=_PARAMETERS)
        )

        async with AllegroClient(settings) as client:
            await get_filament_filters(client)
            categories_call_count = sum(
                1 for c in respx.calls if "/sale/categories" in str(c.request.url)
                and "/parameters" not in str(c.request.url)
            )

            # Second call: must use cache, so no more /sale/categories calls.
            await get_filament_filters(client)
            categories_call_count_after = sum(
                1 for c in respx.calls if "/sale/categories" in str(c.request.url)
                and "/parameters" not in str(c.request.url)
            )

    assert categories_call_count == 5  # one per path step
    assert categories_call_count_after == 5  # no new calls on second lookup


@pytest.mark.asyncio
async def test_search_offers_returns_parsed_offers():
    settings = _settings()
    api = settings.api_base
    auth = settings.auth_base

    with respx.mock:
        respx.post(f"{auth}/auth/oauth/token").mock(
            return_value=httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        )
        _mock_category_path(api)
        respx.get(f"{api}/sale/categories/fil/parameters").mock(
            return_value=httpx.Response(200, json=_PARAMETERS)
        )
        respx.get(f"{api}/offers/listing").mock(
            return_value=httpx.Response(200, json=_LISTING)
        )

        async with AllegroClient(settings) as client:
            page = await search_offers(client, limit=10)

    assert page.total_count == 1
    assert len(page.offers) == 1
    offer = page.offers[0]
    assert offer.id == "offer1"
    assert offer.name == "Bambu PLA Red 1kg"
    assert offer.price is not None
    assert offer.price.amount == "89.99"
    assert offer.price.currency == "PLN"
    assert len(offer.images) == 1
    assert offer.images[0].url == "https://img.example.com/1.jpg"
    assert "allegro.pl/oferta/offer1" in offer.url


@pytest.mark.asyncio
async def test_client_retries_on_429():
    settings = _settings()
    api = settings.api_base
    auth = settings.auth_base

    call_count = 0

    def listing_side_effect(request):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, json=_LISTING)

    with respx.mock:
        respx.post(f"{auth}/auth/oauth/token").mock(
            return_value=httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        )
        _mock_category_path(api)
        respx.get(f"{api}/sale/categories/fil/parameters").mock(
            return_value=httpx.Response(200, json=_PARAMETERS)
        )
        respx.get(f"{api}/offers/listing").mock(side_effect=listing_side_effect)

        async with AllegroClient(settings) as client:
            page = await search_offers(client, limit=10)

    assert call_count == 3
    assert len(page.offers) == 1
