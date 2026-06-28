import json
import logging
from typing import Any

from filament_assistant.core.allegro.client import AllegroClient
from filament_assistant.core.allegro.models import (
    FilamentFilters,
    ListingPage,
    Offer,
    OfferImage,
    ParamValue,
    Price,
)
from filament_assistant.core.cache import cache_get, cache_set

logger = logging.getLogger(__name__)

_CATEGORY_CACHE_KEY = "allegro_filament_category"
_FILTERS_CACHE_KEY = "allegro_filament_filters"
_FILTERS_CACHE_TTL = 86400  # 1 day — brand/type values change occasionally

# Known path on Allegro prod (and mirrored on sandbox):
#   Elektronika → Komputery → Drukarki i skanery → Drukarki 3D → Filamenty
# Each entry is a substring matched case-insensitively against category names.
_CATEGORY_PATH = ["elektronika", "komputer", "drukark", "3d", "filament"]

_BRAND_KEYWORDS = {"marka", "brand", "producent", "manufacturer"}
_TYPE_KEYWORDS = {"materiał", "material", "rodzaj", "typ", "type"}


async def _find_filament_category(client: AllegroClient) -> str:
    # Stored permanently on first successful walk — never re-fetched.
    cached = cache_get(_CATEGORY_CACHE_KEY)
    if cached is not None:
        logger.debug("Filament category ID from disk cache: %s", cached)
        return cached

    category_id = await _walk_known_path(client)
    cache_set(_CATEGORY_CACHE_KEY, category_id)  # no TTL → permanent
    logger.info("Filament category ID saved to disk: %s", category_id)
    return category_id


async def _walk_known_path(client: AllegroClient) -> str:
    """
    Follow _CATEGORY_PATH one level at a time, matching each step by substring.
    Makes exactly len(_CATEGORY_PATH) API calls total.
    """
    current_id: str | None = None

    for step, keyword in enumerate(_CATEGORY_PATH):
        params: dict[str, Any] = {}
        if current_id is not None:
            params["parent.id"] = current_id

        data = await client.get("/sale/categories", params=params or None)
        children: list[dict[str, Any]] = data.get("categories", [])

        match = next(
            (c for c in children if keyword in c.get("name", "").lower()),
            None,
        )
        if match is None:
            names = [c.get("name", "") for c in children]
            raise RuntimeError(
                f"Category path walk failed at step {step} "
                f"(looking for '{keyword}' among {names})"
            )
        current_id = match["id"]
        logger.debug("Path step %d: '%s' → [%s] %s", step, keyword, current_id, match["name"])

    assert current_id is not None
    return current_id


def _keyword_score(name: str, keywords: set[str]) -> int:
    return sum(1 for kw in keywords if kw in name)


def _find_param(params: list[dict[str, Any]], keywords: set[str]) -> dict[str, Any] | None:
    for p in params:
        if _keyword_score(p.get("name", "").lower(), keywords) > 0:
            return p
    return None


def _extract_values(param: dict[str, Any] | None) -> list[ParamValue]:
    if param is None:
        return []
    return [
        ParamValue(id=str(v["id"]), name=v.get("name", str(v["id"])))
        for v in param.get("dictionary", [])
    ]


async def get_filament_filters(client: AllegroClient) -> FilamentFilters:
    cached = cache_get(_FILTERS_CACHE_KEY)
    if cached is not None:
        data = json.loads(cached)
        return FilamentFilters(
            brands=[ParamValue(**v) for v in data["brands"]],
            types=[ParamValue(**v) for v in data["types"]],
        )

    category_id = await _find_filament_category(client)
    data = await client.get(f"/sale/categories/{category_id}/parameters")
    params: list[dict[str, Any]] = data.get("parameters", [])

    brand_param = _find_param(params, _BRAND_KEYWORDS)
    type_param = _find_param(params, _TYPE_KEYWORDS)

    filters = FilamentFilters(
        brands=_extract_values(brand_param),
        types=_extract_values(type_param),
    )
    cache_set(
        _FILTERS_CACHE_KEY,
        json.dumps({
            "brands": [{"id": v.id, "name": v.name} for v in filters.brands],
            "types": [{"id": v.id, "name": v.name} for v in filters.types],
        }),
        ttl=_FILTERS_CACHE_TTL,
    )
    logger.info(
        "Discovered %d brands, %d types for category %s",
        len(filters.brands), len(filters.types), category_id,
    )
    return filters


async def _get_filter_param_ids(client: AllegroClient) -> tuple[str | None, str | None]:
    category_id = await _find_filament_category(client)
    data = await client.get(f"/sale/categories/{category_id}/parameters")
    params: list[dict[str, Any]] = data.get("parameters", [])
    brand_param = _find_param(params, _BRAND_KEYWORDS)
    type_param = _find_param(params, _TYPE_KEYWORDS)
    return (
        brand_param["id"] if brand_param else None,
        type_param["id"] if type_param else None,
    )


async def search_offers(
    client: AllegroClient,
    brand_ids: list[str] | None = None,
    type_ids: list[str] | None = None,
    limit: int = 60,
    offset: int = 0,
) -> ListingPage:
    category_id = await _find_filament_category(client)
    brand_param_id, type_param_id = await _get_filter_param_ids(client)

    params: dict[str, Any] = {
        "category.id": category_id,
        "limit": limit,
        "offset": offset,
    }

    if brand_ids and brand_param_id:
        params[f"parameter.{brand_param_id}"] = brand_ids

    if type_ids and type_param_id:
        params[f"parameter.{type_param_id}"] = type_ids

    data = await client.get("/offers/listing", params=params)
    return _parse_listing(data, offset=offset, limit=limit)


def _parse_listing(data: dict[str, Any], offset: int, limit: int) -> ListingPage:
    items = data.get("items", {})
    all_items = items.get("promoted", []) + items.get("regular", [])

    offers: list[Offer] = []
    for item in all_items:
        images = [OfferImage(url=img["url"]) for img in item.get("images", [])]
        price_data = item.get("sellingMode", {}).get("price")
        price = (
            Price(amount=price_data["amount"], currency=price_data["currency"])
            if price_data
            else None
        )
        offer_id = item.get("id", "")
        offers.append(Offer(
            id=offer_id,
            name=item.get("name", ""),
            url=f"https://allegro.pl/oferta/{offer_id}",
            price=price,
            images=images,
        ))

    total = data.get("searchMeta", {}).get("totalCount", len(offers))
    return ListingPage(offers=offers, total_count=total, offset=offset, limit=limit)
