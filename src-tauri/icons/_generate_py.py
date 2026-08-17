#!/usr/bin/env python3
"""Regenerate SyncMaster app icons as proper RGBA PNG/ICNS/ICO via Pillow."""
import sys
from pathlib import Path

try:
    from PIL import Image, ImageDraw
except ImportError:
    sys.exit("Pillow is required: pip install Pillow")

OUT = Path(__file__).resolve().parent

# Brand colors
BG_TOP = (37, 99, 235)      # blue-600
BG_BOTTOM = (29, 78, 216)   # blue-700
FG = (255, 255, 255, 255)


def make_icon(size: int) -> Image.Image:
    """Draw a rounded blue square with a sync-style double arrow glyph."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # rounded rect background via supersampling for smooth corners
    s = size
    radius = max(4, int(s * 0.22))
    d.rounded_rectangle([0, 0, s - 1, s - 1], radius=radius, fill=BG_BOTTOM)
    # subtle top highlight
    d.rounded_rectangle([0, 0, s - 1, int(s * 0.5)], radius=radius, fill=BG_TOP)
    d.rounded_rectangle([0, int(s * 0.5), s - 1, s - 1], radius=radius, fill=BG_BOTTOM)
    # two opposing chevrons (sync motif)
    t = max(2, int(s * 0.09))
    cx, cy = s / 2, s / 2
    a = s * 0.20
    # left-pointing chevron (top)
    d.line([(cx + a, cy - a), (cx - a, cy - a), (cx - a, cy - a + a)], fill=FG, width=t, joint="curve")
    d.line([(cx - a, cy - a), (cx + a, cy - a)], fill=FG, width=t)
    d.line([(cx - a, cy - a), (cx - a, cy + a)], fill=FG, width=t)
    # right-pointing chevron (bottom)
    d.line([(cx - a, cy + a), (cx + a, cy + a)], fill=FG, width=t)
    d.line([(cx + a, cy + a), (cx + a, cy - a)], fill=FG, width=t)
    return img


def main() -> None:
    sizes = {
        "icon_32x32.png": 32,
        "icon_128x128.png": 128,
        "icon_256x256.png": 256,
        "icon_512x512.png": 512,
        "icon_512.png": 512,
        "icon.png": 512,
        "128x128.png": 128,
        "128x128@2x.png": 256,
    }
    for name, size in sizes.items():
        img = make_icon(size)
        img.save(OUT / name)
        print(f"OK {name} ({size}x{size})")

    # ICNS (macOS)
    big = make_icon(512)
    big.save(OUT / "icon.icns", format="ICNS")
    print("OK icon.icns")

    # ICO (windows) — needs multiple sizes embedded
    ico_sizes = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    frames = [make_icon(s) for s, _ in ico_sizes]
    frames[0].save(OUT / "icon.ico", format="ICO", sizes=[(s, s) for s, _ in ico_sizes])
    print("OK icon.ico")


if __name__ == "__main__":
    main()
