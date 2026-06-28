import logging

from PIL import Image
from rembg import new_session, remove

logger = logging.getLogger(__name__)

# One session is created per process and reused — avoids reloading the ONNX
# model on every call. The u2net model is downloaded on first use (~170 MB).
_session = None


def _get_session():
    global _session
    if _session is None:
        logger.info("Loading rembg u2net model (downloaded on first run)…")
        _session = new_session("u2net")
    return _session


def remove_background(image: Image.Image) -> Image.Image:
    """Return an RGBA image with the background removed using U2-Net."""
    result = remove(image, session=_get_session())
    if result.mode != "RGBA":
        result = result.convert("RGBA")
    return result
