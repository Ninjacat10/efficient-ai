"""Efficient AI brand: colors and the logo mark, drawn in code so the tray, shortcuts and README stay in sync.

The mark is a rounded square with a violet-to-blue gradient and an "E" built from three bars;
the middle bar is short and ends in a spark - the assistant finishing what you started.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

VIOLET, BLUE = (124, 92, 255), (59, 167, 255)
ASSETS = Path(__file__).resolve().parent.parent / "assets"


def logo(size: int = 256) -> Image.Image:
    s = size * 4  # supersample, then downscale for smooth edges
    grad = Image.new("RGB", (s, s))
    px = grad.load()
    for y in range(s):
        for x in range(s):
            t = (x + y) / (2 * s - 2)
            px[x, y] = tuple(int(a + (b - a) * t) for a, b in zip(VIOLET, BLUE))
    mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, s - 1, s - 1), radius=int(s * 0.23), fill=255)
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    img.paste(grad, (0, 0), mask)

    d = ImageDraw.Draw(img)
    u = s / 100
    white = (255, 255, 255, 255)
    bar = 12 * u
    r = bar / 2
    left = 26 * u
    d.rounded_rectangle((left, 24 * u, left + bar, 76 * u), radius=r, fill=white)            # spine
    d.rounded_rectangle((left, 24 * u, 74 * u, 24 * u + bar), radius=r, fill=white)          # top
    d.rounded_rectangle((left, 44 * u, 56 * u, 44 * u + bar), radius=r, fill=white)          # middle (short)
    d.rounded_rectangle((left, 64 * u, 74 * u, 64 * u + bar), radius=r, fill=white)          # bottom
    cx, cy, k = 68 * u, 50 * u, 7 * u                                                      # spark
    d.polygon([(cx, cy - k), (cx + k * 0.32, cy - k * 0.32), (cx + k, cy), (cx + k * 0.32, cy + k * 0.32),
               (cx, cy + k), (cx - k * 0.32, cy + k * 0.32), (cx - k, cy), (cx - k * 0.32, cy - k * 0.32)], fill=white)
    return img.resize((size, size), Image.LANCZOS)


def export() -> None:
    """Write assets/logo.png and assets/efficient.ico (used by the README and the shortcuts)."""
    ASSETS.mkdir(exist_ok=True)
    big = logo(512)
    big.save(ASSETS / "logo.png")
    big.save(ASSETS / "efficient.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])


if __name__ == "__main__":
    export()
