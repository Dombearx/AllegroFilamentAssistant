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


_TOP_CATEGORIES = {
    "categories": [
        {"id": "druk3d", "name": "Druk 3D"},
        {"id": "other", "name": "Inne"},
    ]
}

_DRUK3D_CHILDREN = {
    "categories": [
        {"id": "filament_cat", "name": "Filamenty do drukarek 3D"},
        {"id": "printers", "name": "Drukarki 3D"},
    ]
}

_EMPTY_CATEGORIES = {"categories": []}

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


def _mock_category_tree(api: str):
    """Set up a single dispatcher for all /sale/categories calls."""
    _children = {
        None: _TOP_CATEGORIES,
        "druk3d": _DRUK3D_CHILDREN,
    }

    def dispatcher(request: httpx.Request) -> httpx.Response:
        parent_id = request.url.params.get("parent.id")
        return httpx.Response(200, json=_children.get(parent_id, _EMPTY_CATEGORIES))

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
        _mock_category_tree(api)
        respx.get(f"{api}/sale/categories/filament_cat/parameters").mock(
            return_value=httpx.Response(200, json=_PARAMETERS)
        )

        async with AllegroClient(settings) as client:
            filters = await get_filament_filters(client)

    assert len(filters.brands) == 2
    assert filters.brands[0].name == "Bambu Lab"
    assert len(filters.types) == 2
    assert filters.types[1].name == "PETG"


@pytest.mark.asyncio
async def test_search_offers_returns_parsed_offers():
    settings = _settings()
    api = settings.api_base
    auth = settings.auth_base

    with respx.mock:
        respx.post(f"{auth}/auth/oauth/token").mock(
            return_value=httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        )
        _mock_category_tree(api)
        respx.get(f"{api}/sale/categories/filament_cat/parameters").mock(
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
        _mock_category_tree(api)
        respx.get(f"{api}/sale/categories/filament_cat/parameters").mock(
            return_value=httpx.Response(200, json=_PARAMETERS)
        )
        respx.get(f"{api}/offers/listing").mock(side_effect=listing_side_effect)

        async with AllegroClient(settings) as client:
            page = await search_offers(client, limit=10)

    assert call_count == 3
    assert len(page.offers) == 1
