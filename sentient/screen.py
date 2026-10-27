"""Screen capture and input on Windows, in a scaled coordinate space the model sees."""
from __future__ import annotations

import ctypes
import time

import mss
from PIL import Image

MAX_LONG_EDGE = 1568
MAX_PIXELS = 1_150_000


def make_dpi_aware() -> None:
    # Without this, high-DPI displays make screenshot pixels and mouse coordinates disagree.
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


class Screen:
    """Primary-monitor capture, downscaled to fit image limits; maps model coords back to real pixels."""

    def __init__(self):
        make_dpi_aware()
        with mss.mss() as s:
            mon = s.monitors[1]
        self.left, self.top, self.w, self.h = mon["left"], mon["top"], mon["width"], mon["height"]
        scale = min(1.0, MAX_LONG_EDGE / max(self.w, self.h), (MAX_PIXELS / (self.w * self.h)) ** 0.5)
        self.sw, self.sh = int(self.w * scale), int(self.h * scale)
        self.scale = scale

    def grab(self) -> Image.Image:
        with mss.mss() as s:
            raw = s.grab(s.monitors[1])
        img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
        return img.resize((self.sw, self.sh), Image.LANCZOS) if self.scale < 1 else img

    def grab_full(self) -> Image.Image:
        with mss.mss() as s:
            raw = s.grab(s.monitors[1])
        return Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")

    def zoom(self, region) -> Image.Image:
        """Full-resolution crop of a region given in scaled coordinates, resized to fit limits."""
        x0, y0, x1, y1 = (int(v / self.scale) for v in region)
        crop = self.grab_full().crop((x0, y0, x1, y1))
        f = min(MAX_LONG_EDGE / max(crop.size), (MAX_PIXELS / (crop.width * crop.height)) ** 0.5, 4.0)
        return crop.resize((max(1, int(crop.width * f)), max(1, int(crop.height * f))), Image.LANCZOS)

    def to_real(self, xy) -> tuple[int, int]:
        x, y = xy
        return self.left + int(x / self.scale), self.top + int(y / self.scale)

    def to_scaled(self, xy) -> tuple[int, int]:
        x, y = xy
        return int((x - self.left) * self.scale), int((y - self.top) * self.scale)


def settle(seconds: float = 0.6) -> None:
    time.sleep(seconds)
