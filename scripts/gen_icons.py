#!/usr/bin/env python3
"""Generate the Anthill icon set from one source of truth.

Draws the brand anthill mound and emits every launcher / PWA / dashboard asset, so
the icon is reproducible (re-run after a brand tweak) instead of a hand-edited binary.

Outputs:
  assets/anthill-icon-1024.png         icon-tile master (also the .icns source)
  assets/anthill-mark.png              512 tile (macOS .icns source for build-app.sh)
  anthill/web/static/icon-512.png      PWA
  anthill/web/static/icon-192.png      PWA
  anthill/web/static/icon-maskable-512.png   PWA maskable (full-bleed safe zone)
  anthill/web/static/apple-touch-icon.png    180 tile
  anthill/web/static/favicon-32.png    32 tile
  anthill/web/static/anthill-mark-light.png  transparent inline glyph (sidebar/chat)

Run:  python scripts/gen_icons.py   (then build-app.sh / iconutil refreshes anthill.icns)
"""

from __future__ import annotations

import os

from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SS = 4  # supersample factor for smooth (anti-aliased) edges

# Brand palette - a vivid green from the app's own pine-green family (design-tokens-wave1.md's
# --accent) on the app's white/light-grey ground, not the old amber-on-cream. An early pass tried a
# --accent-tinted DOME (a desaturated grey-sage); on the mound's rounded silhouette that read as
# moss/mildew rather than a brand mark, so DOME is a more saturated, more vivid green instead -
# still the same family as RIDGE/GROUND, just not washed out toward white.
CREAM = (0xFF, 0xFF, 0xFF)  # --surface (white)
SAND = (0xF7, 0xF8, 0xFA)  # --bg (light grey)
DOME = (0x3D, 0x8B, 0x63)  # vivid green, same family as --accent
RIDGE = (0x1F, 0x5C, 0x40)  # deeper shade for the ridge lines
GROUND = (0x15, 0x46, 0x30)  # deeper still, ground line
SOIL = (0x08, 0x1C, 0x13)  # near-black green, entrance


def _gradient(size: int, top: tuple, bottom: tuple) -> Image.Image:
    """A vertical top->bottom gradient as an opaque RGB image."""
    img = Image.new("RGB", (size, size), top)
    px = img.load()
    for y in range(size):
        t = y / max(1, size - 1)
        row = tuple(round(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
        for x in range(size):
            px[x, y] = row
    return img


def _draw_mound(
    d: ImageDraw.ImageDraw,
    S: int,
    cx: float,
    base: float,
    w: float,
    h: float,
    *,
    shadow: bool = True,
) -> None:
    """Draw the pine-green mound (dome + ridges + entrance) sitting on a ground line.

    ``shadow`` adds a soft ground shadow - on for the opaque tile, off for the
    transparent inline glyph (an opaque shadow would show as a blob on a dark sidebar).
    """
    if shadow:
        sh_w = w * 0.72
        d.ellipse([cx - sh_w, base - h * 0.05, cx + sh_w, base + h * 0.16], fill=(0xE4, 0xE7, 0xEA))
    # dome (upper half of an ellipse), flat clay to match the app's brand mark
    d.pieslice([cx - w / 2, base - h, cx + w / 2, base + h], 180, 360, fill=DOME)
    # ridge highlights (the stacked-soil look): gentle convex-up arcs that hug the surface
    t = max(2, int(S * 0.013))
    for yr_frac, rw_frac, rv_frac in ((0.62, 0.30, 0.22), (0.34, 0.37, 0.22)):
        yr, rw, rv = base - h * yr_frac, w * rw_frac, h * rv_frac
        d.arc([cx - rw, yr, cx + rw, yr + 2 * rv], 200, 340, fill=RIDGE, width=t)
    # entrance (dark arch at the base centre)
    ew, eh = w * 0.26, h * 0.46
    d.pieslice([cx - ew / 2, base - eh, cx + ew / 2, base + eh], 180, 360, fill=SOIL)
    # ground line (rounded soil bar a little wider than the mound)
    gw, gt = w * 0.64, max(3, int(S * 0.016))
    d.rounded_rectangle([cx - gw, base - gt / 2, cx + gw, base + gt / 2], radius=gt / 2, fill=GROUND)


def tile(size: int, *, maskable: bool = False) -> Image.Image:
    """A rounded app-icon tile (or a full-bleed maskable square) with the mound centred."""
    S = size * SS
    bg = _gradient(S, CREAM, SAND).convert("RGBA")
    d = ImageDraw.Draw(bg)
    # mound geometry: smaller safe zone for maskable so OS cropping can't clip it
    w = S * (0.56 if maskable else 0.64)
    h = S * (0.34 if maskable else 0.40)
    base = S * (0.62 if maskable else 0.66)
    _draw_mound(d, S, S / 2, base, w, h)
    if not maskable:
        # round the tile corners (transparent outside) - iOS/macOS app-tile shape
        mask = Image.new("L", (S, S), 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            [0, 0, S - 1, S - 1], radius=int(S * 0.225), fill=255
        )
        bg.putalpha(mask)
    return bg.resize((size, size), Image.LANCZOS)


def glyph(size: int) -> Image.Image:
    """The mound on a transparent background (inline brand mark for the sidebar/chat)."""
    S = size * SS
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    _draw_mound(d, S, S / 2, S * 0.70, S * 0.78, S * 0.46, shadow=False)
    return img.resize((size, size), Image.LANCZOS)


def _save(img: Image.Image, *parts: str) -> None:
    path = os.path.join(ROOT, *parts)
    img.save(path)
    print("wrote", os.path.relpath(path, ROOT))


def main() -> None:
    static = ("anthill", "web", "static")
    _save(tile(1024), "assets", "anthill-icon-1024.png")
    _save(tile(512), "assets", "anthill-mark.png")  # macOS .icns source (build-app.sh)
    _save(tile(512), *static, "icon-512.png")
    _save(tile(192), *static, "icon-192.png")
    _save(tile(512, maskable=True), *static, "icon-maskable-512.png")
    _save(tile(180), *static, "apple-touch-icon.png")
    _save(tile(32), *static, "favicon-32.png")
    _save(glyph(512), *static, "anthill-mark-light.png")


if __name__ == "__main__":
    main()
