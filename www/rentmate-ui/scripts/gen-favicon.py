"""Rasterize the favicon SVG into .ico + apple-touch PNG.

Pillow doesn't render SVG directly, but the design is simple enough to
reproduce with ImageDraw primitives — cheaper than pulling in cairosvg
just to emit a few PNG sizes at build time. Re-run via:

    poetry run python www/rentmate-ui/scripts/gen-favicon.py
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

OUTPUT = Path(__file__).resolve().parents[1] / "public"
PRIMARY = (0x26, 0x80, 0xD9, 0xFF)   # hsl(210 70% 50%)
WHITE = (0xFF, 0xFF, 0xFF, 0xFF)
CLEAR = (0, 0, 0, 0)


def _house_badge(size: int) -> Image.Image:
    """Render the RentMate house-badge icon at the given square size."""
    img = Image.new("RGBA", (size, size), CLEAR)
    draw = ImageDraw.Draw(img)
    # Background: rounded square filling the icon.
    radius = max(1, size * 14 // 64)
    draw.rounded_rectangle([(0, 0), (size - 1, size - 1)], radius=radius, fill=PRIMARY)

    # House silhouette (scaled from the 64x64 SVG path).
    # Coordinates keep the same proportions so raster matches SVG.
    s = size / 64
    def pt(x: float, y: float) -> tuple[float, float]:
        return (x * s, y * s)

    # Roof triangle from the SVG: M32 13 L12 30 L52 30.
    draw.polygon([pt(32, 13), pt(12, 30), pt(52, 30)], fill=WHITE)
    # Eaves — the SVG has a tiny 3-px-tall eave band under the roof.
    draw.rectangle([pt(12, 30), pt(52, 33)], fill=WHITE)
    # House body.
    draw.rectangle([pt(15, 33), pt(49, 51)], fill=WHITE)
    # Door cutout.
    draw.rectangle([pt(27, 38), pt(37, 51)], fill=PRIMARY)

    # Chat-bubble dot in the upper right — hints at the AI assistant.
    # Skip at very small sizes so the house stays legible.
    if size >= 24:
        bubble_r = max(2, int(6 * s))
        cx, cy = pt(48, 18)
        draw.ellipse(
            [(cx - bubble_r, cy - bubble_r), (cx + bubble_r, cy + bubble_r)],
            fill=WHITE,
        )
        inner_r = max(1, int(3.5 * s))
        draw.ellipse(
            [(cx - inner_r, cy - inner_r), (cx + inner_r, cy + inner_r)],
            fill=PRIMARY,
        )

    return img


def main() -> None:
    # Multi-resolution favicon.ico — Windows / older browsers prefer ICO.
    # Pillow's ICO encoder takes a single high-res source and downsamples
    # itself when ``sizes`` is supplied, rather than accepting a list of
    # pre-rendered frames. Render at 256 with crisp edges and let Pillow
    # emit each embedded size from that one source.
    ico_sizes = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    _house_badge(256).save(
        OUTPUT / "favicon.ico",
        format="ICO",
        sizes=ico_sizes,
    )

    # apple-touch-icon — iOS home-screen shortcut.
    _house_badge(180).save(OUTPUT / "apple-touch-icon.png", format="PNG")

    # 192 + 512 PNG for Android / PWA manifests down the road.
    _house_badge(192).save(OUTPUT / "icon-192.png", format="PNG")
    _house_badge(512).save(OUTPUT / "icon-512.png", format="PNG")

    print("Wrote:")
    for name in ("favicon.ico", "apple-touch-icon.png", "icon-192.png", "icon-512.png"):
        print(f"  public/{name}")


if __name__ == "__main__":
    main()
