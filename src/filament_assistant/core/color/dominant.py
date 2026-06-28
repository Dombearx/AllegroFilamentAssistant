import logging
from dataclasses import dataclass

import cv2
import numpy as np
from PIL import Image
from sklearn.cluster import KMeans

logger = logging.getLogger(__name__)

# LAB chroma = sqrt(a² + b²).  Below this the cluster is white/gray/black.
NEUTRAL_CHROMA_THRESHOLD = 15.0
# Skip images where the foreground is a tiny fraction of the frame.
MIN_FOREGROUND_PIXELS = 200
# Number of colour clusters to fit.
N_CLUSTERS = 4
# Alpha channel cutoff for "foreground" pixels.
ALPHA_THRESHOLD = 200


@dataclass
class ColorResult:
    hex: str                    # e.g. "#1e88e5"
    rgb: tuple[int, int, int]   # 0-255 each
    confidence: float           # 0-1; higher = more reliable


def extract_dominant_color(
    image: Image.Image,
    target_is_neutral: bool = False,
) -> ColorResult | None:
    """
    Extract the most prominent non-neutral colour from a background-removed
    RGBA image.  Returns None when the image is unusable (too sparse or fully
    neutral with neutral target).

    target_is_neutral: when True, skip the neutral-cluster filter so white /
    gray / black filaments are handled correctly.
    """
    rgba = np.array(image.convert("RGBA"), dtype=np.uint8)
    alpha = rgba[:, :, 3]
    fg_mask = alpha > ALPHA_THRESHOLD
    fg_pixels = rgba[fg_mask, :3]

    if len(fg_pixels) < MIN_FOREGROUND_PIXELS:
        logger.debug("Too few foreground pixels (%d), skipping image", len(fg_pixels))
        return None

    total_pixels = int(rgba.shape[0]) * int(rgba.shape[1])
    fg_fraction = len(fg_pixels) / total_pixels

    lab_pixels = _rgb_to_lab(fg_pixels)

    k = min(N_CLUSTERS, len(fg_pixels))
    kmeans = KMeans(n_clusters=k, n_init=10, random_state=42)
    labels = kmeans.fit_predict(lab_pixels)
    centers = kmeans.cluster_centers_           # shape (k, 3) in LAB

    sizes = np.bincount(labels, minlength=k) / len(labels)   # fraction per cluster
    chromas = np.sqrt(centers[:, 1] ** 2 + centers[:, 2] ** 2)

    # Decide which clusters are candidates.
    if target_is_neutral:
        valid = np.ones(k, dtype=bool)
    else:
        valid = chromas >= NEUTRAL_CHROMA_THRESHOLD
        if not valid.any():
            # Everything neutral: fall back to the most saturated cluster.
            valid[int(np.argmax(chromas))] = True

    # Score = cluster size × chroma; suppressed for invalid clusters.
    scores = np.where(valid, sizes * chromas, -1.0)
    best = int(np.argmax(scores))

    rgb = _lab_to_rgb(centers[best])
    hex_color = "#{:02x}{:02x}{:02x}".format(*rgb)
    # Confidence: how much of the frame is the winning cluster's foreground.
    confidence = float(fg_fraction * sizes[best])

    logger.debug(
        "Dominant colour %s (ΔE conf=%.3f, fg=%.1f%%, cluster=%.1f%%)",
        hex_color, confidence, fg_fraction * 100, sizes[best] * 100,
    )
    return ColorResult(hex=hex_color, rgb=tuple(rgb), confidence=confidence)


def chroma_of_hex(hex_color: str) -> float:
    """Return LAB chroma of a hex colour; used to detect neutral targets."""
    hex_color = hex_color.lstrip("#")
    r, g, b = (int(hex_color[i:i+2], 16) for i in (0, 2, 4))
    lab = _rgb_to_lab(np.array([[r, g, b]], dtype=np.uint8))[0]
    return float(np.sqrt(lab[1] ** 2 + lab[2] ** 2))


# ── Internal helpers ──────────────────────────────────────────────────────────

def _rgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    """Convert uint8 RGB (N, 3) → float32 LAB (N, 3) via OpenCV."""
    pixels = rgb.astype(np.float32) / 255.0
    # cv2 expects shape (H, W, 3); we use (1, N, 3) as a proxy.
    pixels_3d = pixels.reshape(1, -1, 3)
    lab_3d = cv2.cvtColor(pixels_3d, cv2.COLOR_RGB2LAB)
    return lab_3d.reshape(-1, 3)


def _lab_to_rgb(lab: np.ndarray) -> tuple[int, int, int]:
    """Convert a single float32 LAB (3,) → uint8 RGB (3,) via OpenCV."""
    lab_3d = lab.astype(np.float32).reshape(1, 1, 3)
    rgb_3d = cv2.cvtColor(lab_3d, cv2.COLOR_LAB2RGB)
    rgb = np.clip(rgb_3d.reshape(3) * 255, 0, 255).astype(np.uint8)
    return (int(rgb[0]), int(rgb[1]), int(rgb[2]))
