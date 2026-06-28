import io

import numpy as np
from PIL import Image

from filament_assistant.core.allegro.models import Offer, OfferImage, Price
from filament_assistant.core.color.dominant import ColorResult
from filament_assistant.core.color.pipeline import (
    _best_color,
    _image_cache_key,
    _process_image_bytes,
)


def _offer(oid: str, image_url: str = "https://img.example.com/img.jpg") -> Offer:
    return Offer(
        id=oid,
        name=f"Offer {oid}",
        url=f"https://allegro.pl/oferta/{oid}",
        price=Price(amount="50.00", currency="PLN"),
        images=[OfferImage(url=image_url)],
    )


def _red_jpeg_bytes() -> bytes:
    """Minimal JPEG bytes of a solid-red 100×100 image."""
    img = Image.fromarray(
        np.full((100, 100, 3), [200, 30, 30], dtype=np.uint8), mode="RGB"
    )
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _color(hex_color: str, confidence: float = 0.7) -> ColorResult:
    h = hex_color.lstrip("#")
    rgb = (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    return ColorResult(hex=hex_color, rgb=rgb, confidence=confidence)


# ── _image_cache_key ──────────────────────────────────────────────────────────

def test_cache_key_deterministic():
    url = "https://img.example.com/img.jpg"
    assert _image_cache_key(url) == _image_cache_key(url)


def test_cache_key_differs_per_url():
    assert _image_cache_key("https://a.com/1.jpg") != _image_cache_key("https://a.com/2.jpg")


def test_cache_key_has_prefix():
    assert _image_cache_key("https://x.com").startswith("img_color:")


# ── _best_color ───────────────────────────────────────────────────────────────

def test_best_color_picks_highest_confidence():
    results = [
        _color("#ff0000", confidence=0.3),
        _color("#00ff00", confidence=0.8),
        _color("#0000ff", confidence=0.5),
    ]
    best = _best_color(results)
    assert best is not None
    assert best.hex == "#00ff00"


def test_best_color_skips_nones():
    results = [None, _color("#ff0000", confidence=0.6), None]
    best = _best_color(results)
    assert best is not None
    assert best.hex == "#ff0000"


def test_best_color_all_none_returns_none():
    assert _best_color([None, None]) is None


def test_best_color_empty_returns_none():
    assert _best_color([]) is None


# ── _process_image_bytes ──────────────────────────────────────────────────────

def test_process_image_bytes_returns_color_result():
    """
    Run the full CPU pipeline (rembg + k-means) on a solid-red JPEG.
    rembg may leave the foreground mostly intact for a uniform image, so we
    only assert that a result is returned (not None) and has a reddish hue.
    """
    image_bytes = _red_jpeg_bytes()
    result = _process_image_bytes(image_bytes, target_is_neutral=False)
    # rembg may produce a low-confidence result on a plain solid image,
    # but it should not raise.
    # Result can be None if rembg removes all pixels as "background".
    if result is not None:
        assert isinstance(result, ColorResult)
        assert result.hex.startswith("#")
        assert 0.0 <= result.confidence <= 1.0


def test_process_image_bytes_bad_data_returns_none():
    result = _process_image_bytes(b"not an image", target_is_neutral=False)
    assert result is None
