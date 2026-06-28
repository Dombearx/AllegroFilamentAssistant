
from filament_assistant.core.allegro.models import Offer, OfferImage, Price
from filament_assistant.core.color.dominant import ColorResult
from filament_assistant.core.color.matching import (
    delta_e_ciede2000,
    hex_to_rgb,
    rank_offers,
)


def _offer(oid: str) -> Offer:
    return Offer(
        id=oid,
        name=f"Offer {oid}",
        url=f"https://allegro.pl/oferta/{oid}",
        price=Price(amount="50.00", currency="PLN"),
        images=[OfferImage(url="https://img.example.com/img.jpg")],
    )


def _color(hex_color: str) -> ColorResult:
    h = hex_color.lstrip("#")
    rgb = (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    return ColorResult(hex=hex_color, rgb=rgb, confidence=0.8)


# ── hex_to_rgb ────────────────────────────────────────────────────────────────

def test_hex_to_rgb_red():
    assert hex_to_rgb("#ff0000") == (255, 0, 0)


def test_hex_to_rgb_without_hash():
    assert hex_to_rgb("00ff00") == (0, 255, 0)


def test_hex_to_rgb_blue():
    assert hex_to_rgb("#0000ff") == (0, 0, 255)


# ── delta_e_ciede2000 ─────────────────────────────────────────────────────────

def test_identical_colours_zero_delta():
    de = delta_e_ciede2000((200, 50, 50), (200, 50, 50))
    assert de < 0.01


def test_similar_colours_small_delta():
    # Slightly different reds should be perceptually close.
    de = delta_e_ciede2000((200, 30, 30), (210, 35, 35))
    assert de < 5.0


def test_opposite_colours_large_delta():
    # Red vs cyan are far apart.
    de = delta_e_ciede2000((255, 0, 0), (0, 255, 255))
    assert de > 30.0


def test_black_vs_white_large_delta():
    de = delta_e_ciede2000((0, 0, 0), (255, 255, 255))
    assert de > 50.0


def test_delta_e_symmetric():
    de1 = delta_e_ciede2000((200, 30, 30), (30, 200, 30))
    de2 = delta_e_ciede2000((30, 200, 30), (200, 30, 30))
    assert abs(de1 - de2) < 0.01


# ── rank_offers ───────────────────────────────────────────────────────────────

def test_rank_orders_by_delta_e():
    candidates = [
        (_offer("a"), _color("#cc2020")),   # far from blue
        (_offer("b"), _color("#2020cc")),   # close to blue target
        (_offer("c"), _color("#4040dd")),   # also close but slightly less
    ]
    ranked = rank_offers(candidates, target_hex="#2233cc", threshold=20.0)
    assert ranked[0].offer.id == "b"
    assert ranked[0].delta_e < ranked[1].delta_e


def test_rank_filters_by_threshold():
    candidates = [
        (_offer("close"), _color("#ff1010")),
        (_offer("far"),   _color("#0000ff")),
    ]
    ranked = rank_offers(candidates, target_hex="#ff0000", threshold=5.0)
    ids = [r.offer.id for r in ranked]
    assert "close" in ids
    assert "far" not in ids


def test_rank_empty_when_none_match():
    candidates = [(_offer("x"), _color("#00ff00"))]
    ranked = rank_offers(candidates, target_hex="#ff0000", threshold=1.0)
    assert ranked == []


def test_rank_all_match_when_threshold_high():
    candidates = [
        (_offer("a"), _color("#ff0000")),
        (_offer("b"), _color("#0000ff")),
    ]
    ranked = rank_offers(candidates, target_hex="#ff0000", threshold=200.0)
    assert len(ranked) == 2
