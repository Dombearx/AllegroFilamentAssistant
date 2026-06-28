import json
import logging
from typing import Any

from filament_assistant.config import get_settings
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

# Cache keys and TTLs
_CATEGORY_CACHE_KEY = "allegro_filament_category"
_FILTERS_CACHE_KEY = "allegro_filament_filters"
_CATEGORY_CACHE_TTL = 86400 * 7   # 7 days — category IDs rarely change
_FILTERS_CACHE_TTL = 86400        # 1 day  — parameter values change occasionally

# Polish / Allegro search terms for the 3D printing filament category.
# High-weight keywords (score 3 each) identify the actual filament category leaf.
# Low-weight keywords (score 1 each) identify ancestor / breadcrumb nodes worth descending.
_FILAMENT_HIGH = {"filament", "filamet"}
_FILAMENT_LOW = {"pla", "petg", "abs", "tpu", "druk 3d"}
_BRAND_KEYWORDS = {"marka", "brand", "producent", "manufacturer"}
_TYPE_KEYWORDS = {"materiał", "material", "rodzaj", "typ", "type"}


async def _find_filament_category(client: AllegroClient) -> str:
    # Pinned ID in config: skip all discovery and cache logic.
    pinned = get_settings().allegro_filament_category_id
    if pinned:
        logger.debug("Using pinned filament category ID: %s", pinned)
        return pinned

    cached = cache_get(_CATEGORY_CACHE_KEY)
    if cached is not None:
        logger.debug("Filament category ID from cache: %s", cached)
        return cached

    category_id = await _discover_category(client)
    cache_set(_CATEGORY_CACHE_KEY, category_id, ttl=_CATEGORY_CACHE_TTL)
    logger.info("Discovered filament category ID: %s", category_id)
    return category_id


async def _discover_category(client: AllegroClient) -> str:
    # Walk the category tree looking for a node whose name suggests 3D printing filament.
    # Allegro's top-level categories include "Elektronika", "Dom i ogród", etc.
    # We need to traverse into "Druk 3D" > "Filamenty" (or similar).

    data = await client.get("/sale/categories")
    top_categories = data.get("categories", [])

    # BFS; collect candidates scored by keyword match.
    queue: list[dict[str, Any]] = list(top_categories)
    best_id: str | None = None
    best_score: int = -1

    visited: set[str] = set()
    while queue:
        node = queue.pop(0)
        cat_id: str = node["id"]
        if cat_id in visited:
            continue
        visited.add(cat_id)

        name: str = node.get("name", "").lower()
        score = _keyword_score(name, _FILAMENT_HIGH) * 3 + _keyword_score(name, _FILAMENT_LOW)
        if score > best_score:
            best_score = score
            best_id = cat_id

        # Fetch children for categories whose names hint at 3D printing; skip clear leaves.
        _explore_kws = {"3d", "druk", "filament", "filamet", "technologia", "elektronik"}
        if any(kw in name for kw in _explore_kws):
            children_data = await client.get("/sale/categories", params={"parent.id": cat_id})
            children = children_data.get("categories", [])
            queue = children + queue  # depth-first into promising subtrees

    if best_id is None:
        raise RuntimeError(
            "Could not find a filament category in the Allegro category tree. "
            "Check that you are hitting the correct environment (prod/sandbox)."
        )
    return best_id


def _keyword_score(name: str, keywords: set[str]) -> int:
    return sum(1 for kw in keywords if kw in name)


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
        "Discovered %d brands and %d types for category %s",
        len(filters.brands), len(filters.types), category_id,
    )
    return filters


def _find_param(params: list[dict[str, Any]], keywords: set[str]) -> dict[str, Any] | None:
    for p in params:
        name = p.get("name", "").lower()
        if _keyword_score(name, keywords) > 0:
            return p
    return None


def _extract_values(param: dict[str, Any] | None) -> list[ParamValue]:
    if param is None:
        return []
    return [
        ParamValue(id=str(v["id"]), name=v.get("name", str(v["id"])))
        for v in param.get("dictionary", [])
    ]


async def get_filter_params(client: AllegroClient) -> tuple[str | None, str | None]:
    """Return (brand_param_id, type_param_id) for the filament category."""
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
    brand_param_id, type_param_id = await get_filter_params(client)

    params: dict[str, Any] = {
        "category.id": category_id,
        "limit": limit,
        "offset": offset,
    }

    if brand_ids and brand_param_id:
        # httpx serialises lists as repeated query params automatically.
        params[f"parameter.{brand_param_id}"] = brand_ids

    if type_ids and type_param_id:
        params[f"parameter.{type_param_id}"] = type_ids

    data = await client.get("/offers/listing", params=params)
    return _parse_listing(data, offset=offset, limit=limit)


def _parse_listing(data: dict[str, Any], offset: int, limit: int) -> ListingPage:
    items = data.get("items", {})
    promoted: list[dict] = items.get("promoted", [])
    regular: list[dict] = items.get("regular", [])
    all_items = promoted + regular

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
