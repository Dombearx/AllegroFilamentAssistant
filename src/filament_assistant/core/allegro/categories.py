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
from filament_assistant.core.cache import cache_delete, cache_get, cache_set

logger = logging.getLogger(__name__)

_CATEGORY_CACHE_KEY = "allegro_filament_category"
# v2 includes param IDs and colour list — old entries are ignored automatically.
_FILTERS_CACHE_KEY = "allegro_filament_filters_v2"

# Known path on Allegro prod (and mirrored on sandbox):
#   Elektronika → Komputery → Drukarki i skanery → Drukarki 3D → Filamenty
# Each entry is a substring matched case-insensitively against category names.
_CATEGORY_PATH = ["elektronika", "komputer", "drukark", "3d", "filament"]

_BRAND_KEYWORDS = {"marka", "brand", "producent", "manufacturer"}
_TYPE_KEYWORDS = {"materiał", "material", "rodzaj", "typ", "type"}
_COLOR_KEYWORDS = {"kolor", "barwa", "color", "colour"}


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
            brands=[ParamValue(**v) for v in data.get("brands", [])],
            types=[ParamValue(**v) for v in data.get("types", [])],
            colors=[ParamValue(**v) for v in data.get("colors", [])],
            brand_param_id=data.get("brand_param_id"),
            type_param_id=data.get("type_param_id"),
            color_param_id=data.get("color_param_id"),
        )

    category_id = await _find_filament_category(client)
    data = await client.get(f"/sale/categories/{category_id}/parameters")
    params: list[dict[str, Any]] = data.get("parameters", [])

    brand_param = _find_param(params, _BRAND_KEYWORDS)
    type_param = _find_param(params, _TYPE_KEYWORDS)
    color_param = _find_param(params, _COLOR_KEYWORDS)

    filters = FilamentFilters(
        brands=_extract_values(brand_param),
        types=_extract_values(type_param),
        colors=_extract_values(color_param),
        brand_param_id=brand_param["id"] if brand_param else None,
        type_param_id=type_param["id"] if type_param else None,
        color_param_id=color_param["id"] if color_param else None,
    )
    cache_set(
        _FILTERS_CACHE_KEY,
        json.dumps({
            "brands": [{"id": v.id, "name": v.name} for v in filters.brands],
            "types": [{"id": v.id, "name": v.name} for v in filters.types],
            "colors": [{"id": v.id, "name": v.name} for v in filters.colors],
            "brand_param_id": filters.brand_param_id,
            "type_param_id": filters.type_param_id,
            "color_param_id": filters.color_param_id,
        }),
    )  # no TTL → permanent; use invalidate_filters() to force refresh
    logger.info(
        "Discovered %d brands, %d types, %d colours for category %s",
        len(filters.brands), len(filters.types), len(filters.colors), category_id,
    )
    return filters


def invalidate_filters() -> None:
    """Clear the cached filter list; next call to get_filament_filters will re-fetch."""
    cache_delete(_FILTERS_CACHE_KEY)
    logger.info("Filament filters cache cleared")


def invalidate_category() -> None:
    """Clear the cached category ID; next search will re-walk the category path."""
    cache_delete(_CATEGORY_CACHE_KEY)
    logger.info("Filament category cache cleared")


async def search_offers(
    client: AllegroClient,
    brand_ids: list[str] | None = None,
    type_ids: list[str] | None = None,
    color_ids: list[str] | None = None,
    limit: int = 60,
    offset: int = 0,
) -> ListingPage:
    category_id = await _find_filament_category(client)
    filters = await get_filament_filters(client)

    params: dict[str, Any] = {
        "category.id": category_id,
        "limit": limit,
        "offset": offset,
    }

    if brand_ids and filters.brand_param_id:
        params[f"parameter.{filters.brand_param_id}"] = brand_ids

    if type_ids and filters.type_param_id:
        params[f"parameter.{filters.type_param_id}"] = type_ids

    if color_ids and filters.color_param_id:
        params[f"parameter.{filters.color_param_id}"] = color_ids

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
