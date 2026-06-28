import asyncio
import hashlib
import logging
import pickle
from collections.abc import AsyncIterator
from concurrent.futures import ProcessPoolExecutor

import httpx
from PIL import Image

from filament_assistant.config import get_settings
from filament_assistant.core.allegro.models import Offer
from filament_assistant.core.cache import cache_get, cache_set
from filament_assistant.core.color.dominant import (
    ColorResult,
    chroma_of_hex,
    extract_dominant_color,
)
from filament_assistant.core.color.matching import RankedOffer, rank_offers
from filament_assistant.core.color.segmentation import remove_background

logger = logging.getLogger(__name__)

_IMAGE_CACHE_TTL = 86400 * 30   # 30 days — extracted colour per image URL
_MAX_IMAGES_PER_OFFER = 3       # only process the first N images per offer

# App-level process pool — initialised by init_executor() on startup so worker
# processes are reused across searches instead of spawned per call.
_executor: ProcessPoolExecutor | None = None


def init_executor(max_workers: int | None = None) -> None:
    """Create the shared process pool.  Call once on application startup."""
    global _executor
    if _executor is not None:
        return
    settings = get_settings()
    workers = max_workers or max(1, settings.image_concurrency // 2)
    _executor = ProcessPoolExecutor(max_workers=workers)
    logger.info("Process pool initialised with %d workers", workers)


def shutdown_executor(wait: bool = True) -> None:
    """Shut down the shared process pool.  Call on application shutdown."""
    global _executor
    if _executor is not None:
        _executor.shutdown(wait=wait)
        _executor = None
        logger.info("Process pool shut down")


def _image_cache_key(url: str) -> str:
    return "img_color:" + hashlib.sha256(url.encode()).hexdigest()[:16]


# ── CPU-bound work (run in a process pool) ────────────────────────────────────

def _process_image_bytes(image_bytes: bytes, target_is_neutral: bool) -> ColorResult | None:
    """Segment + dominant-colour extraction; runs in a subprocess."""
    try:
        import io
        image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        fg = remove_background(image)
        return extract_dominant_color(fg, target_is_neutral=target_is_neutral)
    except Exception as exc:
        logger.warning("Image processing failed: %s", exc)
        return None


# ── Async helpers ─────────────────────────────────────────────────────────────

async def _download(url: str, client: httpx.AsyncClient) -> bytes | None:
    try:
        resp = await client.get(url, follow_redirects=True, timeout=15.0)
        resp.raise_for_status()
        return resp.content
    except Exception as exc:
        logger.warning("Failed to download %s: %s", url, exc)
        return None


async def _color_for_url(
    url: str,
    client: httpx.AsyncClient,
    executor: ProcessPoolExecutor,
    target_is_neutral: bool,
) -> ColorResult | None:
    cache_key = _image_cache_key(url)
    cached = cache_get(cache_key)
    if cached is not None:
        return pickle.loads(cached)  # noqa: S301

    image_bytes = await _download(url, client)
    if image_bytes is None:
        return None

    result: ColorResult | None = await asyncio.get_running_loop().run_in_executor(
        executor, _process_image_bytes, image_bytes, target_is_neutral
    )

    if result is not None:
        cache_set(cache_key, pickle.dumps(result), ttl=_IMAGE_CACHE_TTL)

    return result


def _best_color(results: list[ColorResult | None]) -> ColorResult | None:
    """Pick the highest-confidence valid result across an offer's images."""
    valid = [r for r in results if r is not None]
    if not valid:
        return None
    return max(valid, key=lambda r: r.confidence)


# ── Public API ────────────────────────────────────────────────────────────────

async def process_offers(
    offers: list[Offer],
    target_hex: str,
    threshold: float | None = None,
) -> AsyncIterator[RankedOffer]:
    """
    Async generator: for each offer, download its images, extract the dominant
    filament colour, and yield a RankedOffer if within the ΔE threshold.
    Offers are processed concurrently (bounded by IMAGE_CONCURRENCY).

    Uses the app-level process pool when available (see init_executor); falls
    back to a per-call pool otherwise so the function is self-contained in
    tests and CLI usage.
    """
    settings = get_settings()
    if threshold is None:
        threshold = settings.delta_e_threshold

    target_is_neutral = chroma_of_hex(target_hex) < 15.0
    semaphore = asyncio.Semaphore(settings.image_concurrency)

    async def _process_offer(
        offer: Offer,
        client: httpx.AsyncClient,
        executor: ProcessPoolExecutor,
    ) -> RankedOffer | None:
        async with semaphore:
            urls = [img.url for img in offer.images[:_MAX_IMAGES_PER_OFFER]]
            if not urls:
                return None

            tasks = [
                _color_for_url(url, client, executor, target_is_neutral)
                for url in urls
            ]
            results = await asyncio.gather(*tasks)
            color = _best_color(list(results))
            if color is None:
                return None

            ranked = rank_offers([(offer, color)], target_hex, threshold)
            return ranked[0] if ranked else None

    owned = _executor is None
    executor = (
        _executor
        if _executor is not None
        else ProcessPoolExecutor(max_workers=max(1, settings.image_concurrency // 2))
    )
    try:
        async with httpx.AsyncClient() as client:
            coros = [_process_offer(offer, client, executor) for offer in offers]
            for coro in asyncio.as_completed(coros):
                result = await coro
                if result is not None:
                    yield result
    finally:
        if owned:
            executor.shutdown(wait=False)
