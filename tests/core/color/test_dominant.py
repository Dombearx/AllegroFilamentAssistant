import numpy as np
from PIL import Image

from filament_assistant.core.color.dominant import (
    NEUTRAL_CHROMA_THRESHOLD,
    chroma_of_hex,
    extract_dominant_color,
)


def _solid_rgba(r: int, g: int, b: int, a: int = 255, size: int = 100) -> Image.Image:
    """Create a solid-colour RGBA image of given size."""
    arr = np.full((size, size, 4), [r, g, b, a], dtype=np.uint8)
    return Image.fromarray(arr, mode="RGBA")


def _split_rgba(
    fg_rgb: tuple[int, int, int],
    bg_rgb: tuple[int, int, int],
    fg_fraction: float = 0.7,
    size: int = 100,
) -> Image.Image:
    """Image with a foreground block (alpha=255) and background block (alpha=0)."""
    arr = np.zeros((size, size, 4), dtype=np.uint8)
    split = int(size * fg_fraction)
    arr[:, :split] = [*fg_rgb, 255]   # foreground
    arr[:, split:] = [*bg_rgb, 0]     # transparent
    return Image.fromarray(arr, mode="RGBA")


# ── basic extraction ──────────────────────────────────────────────────────────

def test_extracts_red():
    img = _solid_rgba(200, 30, 30)
    result = extract_dominant_color(img)
    assert result is not None
    r, g, b = result.rgb
    assert r > 150, "Expected dominant red channel"
    assert r > g and r > b


def test_extracts_blue():
    img = _solid_rgba(20, 40, 210)
    result = extract_dominant_color(img)
    assert result is not None
    r, g, b = result.rgb
    assert b > 150
    assert b > r and b > g


def test_extracts_green():
    img = _solid_rgba(30, 190, 30)
    result = extract_dominant_color(img)
    assert result is not None
    r, g, b = result.rgb
    assert g > 130
    assert g > r and g > b


def test_hex_format():
    img = _solid_rgba(200, 30, 30)
    result = extract_dominant_color(img)
    assert result is not None
    assert result.hex.startswith("#")
    assert len(result.hex) == 7


def test_confidence_in_range():
    img = _solid_rgba(200, 30, 30)
    result = extract_dominant_color(img)
    assert result is not None
    assert 0.0 < result.confidence <= 1.0


# ── foreground masking ────────────────────────────────────────────────────────

def test_ignores_transparent_pixels():
    """Background (alpha=0) pixels must not influence the colour."""
    img = _split_rgba(fg_rgb=(200, 30, 30), bg_rgb=(0, 0, 255))
    result = extract_dominant_color(img)
    assert result is not None
    r, g, b = result.rgb
    assert r > b, "Should see red foreground, not blue background"


def test_returns_none_for_sparse_foreground():
    """Almost fully transparent image → not enough foreground pixels."""
    arr = np.zeros((100, 100, 4), dtype=np.uint8)
    arr[0, 0] = [200, 30, 30, 255]   # only 1 pixel
    img = Image.fromarray(arr, mode="RGBA")
    result = extract_dominant_color(img)
    assert result is None


# ── neutral filtering ─────────────────────────────────────────────────────────

def test_neutral_filter_skips_gray():
    """A gray spool area should not become the dominant colour when a
    saturated colour is also present."""
    arr = np.zeros((100, 100, 4), dtype=np.uint8)
    # Left 30%: gray (neutral reel)
    arr[:, :30] = [128, 128, 128, 255]
    # Right 70%: vivid blue (filament)
    arr[:, 30:] = [30, 30, 200, 255]
    img = Image.fromarray(arr, mode="RGBA")
    result = extract_dominant_color(img)
    assert result is not None
    r, g, b = result.rgb
    assert b > r, "Expected blue filament to win over gray reel"


def test_neutral_target_keeps_gray():
    """When target is neutral (white/black), neutral clusters are NOT filtered."""
    img = _solid_rgba(180, 180, 180)   # gray filament
    result = extract_dominant_color(img, target_is_neutral=True)
    assert result is not None


def test_fallback_when_all_neutral():
    """If every cluster is neutral, the most-saturated is returned (not None)."""
    img = _solid_rgba(200, 200, 200)   # pure gray, low chroma
    result = extract_dominant_color(img, target_is_neutral=False)
    assert result is not None  # falls back, doesn't swallow the image


# ── chroma_of_hex helper ──────────────────────────────────────────────────────

def test_chroma_of_vivid_colour_high():
    assert chroma_of_hex("#ff0000") > NEUTRAL_CHROMA_THRESHOLD


def test_chroma_of_white_low():
    assert chroma_of_hex("#ffffff") < NEUTRAL_CHROMA_THRESHOLD


def test_chroma_of_black_low():
    assert chroma_of_hex("#000000") < NEUTRAL_CHROMA_THRESHOLD


def test_chroma_of_gray_low():
    assert chroma_of_hex("#808080") < NEUTRAL_CHROMA_THRESHOLD
