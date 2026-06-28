"""
CLI smoke test for the Allegro client (M1).

Usage:
    uv run allegro-smoke-test                    # full smoke test
    uv run allegro-smoke-test --discover-category  # print category tree and exit

Requires a .env file (see .env.example) with ALLEGRO_CLIENT_ID and
ALLEGRO_CLIENT_SECRET. Defaults to the sandbox environment.

--discover-category:
  Walks the Allegro category tree and prints every node whose name contains
  filament-related keywords, along with its ID.  Use the output to pick the
  correct ID and set ALLEGRO_FILAMENT_CATEGORY_ID in your .env.

Full smoke test:
  1. Fetches an OAuth2 token.
  2. Resolves the filament category (pinned ID or BFS discovery).
  3. Lists up to 5 brands and 5 types available.
  4. Fetches the first page of offers (no filters).
  5. Prints a sample of offers with name, price, and image count.
"""

import asyncio
import logging
import sys
from typing import Any

from filament_assistant.config import get_settings
from filament_assistant.core.allegro.auth import get_access_token
from filament_assistant.core.allegro.categories import get_filament_filters, search_offers
from filament_assistant.core.allegro.client import AllegroClient

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


async def discover_category_tree(client: AllegroClient) -> None:
    """Walk the tree and print all filament-related category nodes with their IDs."""
    logger.info("Walking Allegro category tree — this may take a moment…")

    _keywords = {"filament", "filamet", "druk 3d", "3d print"}

    async def _walk(parent_id: str | None, depth: int) -> None:
        params: dict[str, Any] = {}
        if parent_id:
            params["parent.id"] = parent_id
        data = await client.get("/sale/categories", params=params or None)
        for cat in data.get("categories", []):
            name: str = cat.get("name", "")
            cat_id: str = cat["id"]
            if any(kw in name.lower() for kw in _keywords):
                indent = "  " * depth
                logger.info("%s[%s] %s", indent, cat_id, name)
                await _walk(cat_id, depth + 1)
            elif depth == 0:
                # Always descend one level so we don't miss subtrees.
                await _walk(cat_id, depth + 1)

    await _walk(None, 0)
    logger.info(
        "\nSet ALLEGRO_FILAMENT_CATEGORY_ID=<id> in your .env to pin the correct category."
    )


async def run(discover_only: bool = False) -> None:
    settings = get_settings()
    logger.info("Environment: %s", settings.allegro_env)
    logger.info("API base:    %s", settings.api_base)

    token = await get_access_token(settings)
    logger.info("Token obtained (%d chars)", len(token))

    async with AllegroClient(settings) as client:
        if discover_only:
            await discover_category_tree(client)
            return

        logger.info("\n── Step 1: Filament filters (brands & types) ─────────────────")
        if settings.allegro_filament_category_id:
            logger.info("Using pinned category ID: %s", settings.allegro_filament_category_id)
        else:
            logger.info("No ALLEGRO_FILAMENT_CATEGORY_ID set — running BFS discovery.")
            logger.info("Run with --discover-category to find and pin the correct ID.")

        filters = await get_filament_filters(client)

        if not filters.brands and not filters.types:
            logger.warning(
                "No brands or types found. The category may be wrong or the sandbox "
                "has no parameter data. Try --discover-category to verify."
            )
        else:
            logger.info("Brands (%d total):", len(filters.brands))
            for b in filters.brands[:5]:
                logger.info("  id=%-12s name=%s", b.id, b.name)
            if len(filters.brands) > 5:
                logger.info("  … and %d more", len(filters.brands) - 5)

            logger.info("Types (%d total):", len(filters.types))
            for t in filters.types[:5]:
                logger.info("  id=%-12s name=%s", t.id, t.name)
            if len(filters.types) > 5:
                logger.info("  … and %d more", len(filters.types) - 5)

        logger.info("\n── Step 2: Offer listing (first 10, no filters) ──────────────")
        page = await search_offers(client, limit=10, offset=0)
        logger.info("Total offers available: %d", page.total_count)
        logger.info("Offers on this page:    %d", len(page.offers))

        for offer in page.offers[:5]:
            price_str = (
                f"{offer.price.amount} {offer.price.currency}" if offer.price else "N/A"
            )
            logger.info(
                "  [%s] %s | %s | %d image(s)",
                offer.id, offer.name[:60], price_str, len(offer.images),
            )

    logger.info("\n── Done. M1 smoke test passed ────────────────────────────────")


def main() -> None:
    discover_only = "--discover-category" in sys.argv
    try:
        asyncio.run(run(discover_only=discover_only))
    except Exception as exc:
        logger.error("Smoke test failed: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
