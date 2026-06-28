"""
CLI smoke test for the Allegro client (M1).

Usage:
    uv run allegro-smoke-test

Requires a .env file (see .env.example) with ALLEGRO_CLIENT_ID and
ALLEGRO_CLIENT_SECRET. Defaults to the sandbox environment.

What it does:
  1. Fetches an OAuth2 token.
  2. Discovers the filament category and its brand/type parameters.
  3. Lists up to 5 brands and 5 types available.
  4. Fetches the first page of offers (no filters).
  5. Prints a sample of offers with name, price, and image count.
"""

import asyncio
import logging
import sys

from filament_assistant.config import get_settings
from filament_assistant.core.allegro.auth import get_access_token
from filament_assistant.core.allegro.categories import get_filament_filters, search_offers
from filament_assistant.core.allegro.client import AllegroClient

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


async def run() -> None:
    settings = get_settings()
    logger.info("Environment: %s", settings.allegro_env)
    logger.info("API base:    %s", settings.api_base)

    # Step 1: token
    logger.info("\n── Step 1: OAuth2 token ──────────────────────────────────────")
    token = await get_access_token(settings)
    logger.info("Token obtained (%d chars)", len(token))

    async with AllegroClient(settings) as client:
        # Step 2: filters
        logger.info("\n── Step 2: Filament filters (brands & types) ─────────────────")
        filters = await get_filament_filters(client)

        if not filters.brands and not filters.types:
            logger.warning(
                "No brands or types found. The category discovery may have landed on the "
                "wrong node, or this sandbox has no parameter data. Continuing anyway."
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

        # Step 3: offer listing (no filters, first page)
        logger.info("\n── Step 3: Offer listing (first 10, no filters) ──────────────")
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
    try:
        asyncio.run(run())
    except Exception as exc:
        logger.error("Smoke test failed: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
