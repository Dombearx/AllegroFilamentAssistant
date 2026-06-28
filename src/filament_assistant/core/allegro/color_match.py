from filament_assistant.core.allegro.models import ParamValue
from filament_assistant.core.color.matching import delta_e_ciede2000, hex_to_rgb

# Approximate sRGB values for Polish colour names used in Allegro's filter.
_COLOUR_MAP: dict[str, tuple[int, int, int]] = {
    "biały": (255, 255, 255),
    "czarny": (0, 0, 0),
    "czerwony": (220, 20, 20),
    "niebieski": (0, 60, 200),
    "granatowy": (0, 0, 128),
    "zielony": (0, 160, 0),
    "żółty": (255, 220, 0),
    "pomarańczowy": (255, 100, 0),
    "fioletowy": (120, 0, 180),
    "różowy": (255, 100, 160),
    "szary": (128, 128, 128),
    "brązowy": (139, 69, 19),
    "złoty": (212, 175, 55),
    "srebrny": (192, 192, 192),
    "beżowy": (245, 245, 220),
    "turkusowy": (64, 224, 208),
    "kremowy": (255, 253, 208),
    "miętowy": (152, 255, 152),
    "miedziany": (184, 115, 51),
    "khaki": (195, 176, 145),
    "transparentny": (200, 200, 200),
    "przeźroczysty": (200, 200, 200),
    "naturalny": (240, 230, 210),
}


def closest_allegro_colour(
    target_hex: str, param_values: list[ParamValue]
) -> ParamValue | None:
    """Return the ParamValue whose colour is closest to target_hex by CIEDE2000."""
    if not param_values:
        return None
    target_rgb = hex_to_rgb(target_hex)
    best_pv: ParamValue | None = None
    best_de = float("inf")
    for pv in param_values:
        name = pv.name.lower().strip()
        rgb = _COLOUR_MAP.get(name)
        if rgb is None:
            for key, val in _COLOUR_MAP.items():
                if key in name or name in key:
                    rgb = val
                    break
        if rgb is None:
            continue
        de = delta_e_ciede2000(target_rgb, rgb)
        if de < best_de:
            best_de = de
            best_pv = pv
    return best_pv
