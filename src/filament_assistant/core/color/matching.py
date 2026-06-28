import logging
import warnings
from dataclasses import dataclass, field

import colour
import numpy as np

from filament_assistant.core.allegro.models import Offer
from filament_assistant.core.color.dominant import ColorResult

# Silence colour-science's matplotlib availability warning.
warnings.filterwarnings("ignore", category=colour.utilities.ColourUsageWarning)

logger = logging.getLogger(__name__)


@dataclass
class ImageDebugInfo:
    url: str
    fg_image_b64: str | None  # base64 PNG of rembg foreground
    color: ColorResult | None


@dataclass
class RankedOffer:
    offer: Offer
    color: ColorResult
    delta_e: float
    debug: list[ImageDebugInfo] | None = field(default=None)


def hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def delta_e_ciede2000(rgb1: tuple[int, int, int], rgb2: tuple[int, int, int]) -> float:
    """Perceptual colour difference between two sRGB (0-255) triples."""
    lab1 = _rgb_to_lab(rgb1)
    lab2 = _rgb_to_lab(rgb2)
    return float(colour.delta_E(lab1, lab2, method="CIE 2000"))


def rank_offers(
    candidates: list[tuple[Offer, ColorResult]],
    target_hex: str,
    threshold: float,
) -> list[RankedOffer]:
    """
    Score each (offer, color) pair against target_hex using CIEDE2000 and
    return those within threshold, sorted by ascending ΔE.
    """
    target_rgb = hex_to_rgb(target_hex)
    ranked: list[RankedOffer] = []

    for offer, color in candidates:
        de = delta_e_ciede2000(color.rgb, target_rgb)
        if de <= threshold:
            ranked.append(RankedOffer(offer=offer, color=color, delta_e=de))

    ranked.sort(key=lambda r: r.delta_e)
    logger.debug(
        "Ranked %d/%d offers within ΔE≤%.1f for target %s",
        len(ranked), len(candidates), threshold, target_hex,
    )
    return ranked


# ── Internal helpers ──────────────────────────────────────────────────────────

def _rgb_to_lab(rgb: tuple[int, int, int]) -> np.ndarray:
    srgb = np.array(rgb, dtype=np.float64) / 255.0
    xyz = colour.sRGB_to_XYZ(srgb)
    return colour.XYZ_to_Lab(xyz)
