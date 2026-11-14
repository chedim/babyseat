"""HACS and Home Assistant showed "no image" for babyseat: the `membrane` domain has no entry in the brands CDN. HA
2026 serves a custom integration's own `brand/` folder (icon.png 256x256, icon@2x.png 512x512; every other brand image
falls back to the icon), so the integration ships its icon from the logo (#368)."""
from pathlib import Path

BRAND = Path(__file__).resolve().parents[2] / "custom_components" / "membrane" / "brand"


def _png_size(p: Path):
    b = p.read_bytes()[:24]
    assert b[:8] == b"\x89PNG\r\n\x1a\n", p
    return int.from_bytes(b[16:20], "big"), int.from_bytes(b[20:24], "big")


def test_the_integration_ships_its_icon_at_both_sizes():
    assert _png_size(BRAND / "icon.png") == (256, 256)
    assert _png_size(BRAND / "icon@2x.png") == (512, 512)
