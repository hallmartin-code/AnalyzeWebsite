#!/usr/bin/env python3
"""Generate the favicon set from the official TEN Capital mark.

    python tools/make_favicon.py

The source of truth is ``analyzewebsite_web/assets/ten_capital_mark.png`` — the
supplied brand artwork. Everything in ``analyzewebsite_web/static/`` derives
from it, so the mark lives in exactly one place and the tab icon cannot drift
from the brand.

This mirrors the generator used by the sibling TEN Capital tools, deliberately:
the artwork, the 6% clear space, and the emitted file names are the same, so a
browser tab reads identically whichever tool it is showing.

Two consequences of a raster source, both accepted deliberately:

* ``favicon.svg`` wraps the artwork rather than describing it in paths. It is
  therefore several KB rather than under one, and gains no crispness over the
  192px PNG. It exists so the ``image/svg+xml`` link keeps working and so every
  icon has the same shape — not because it is resolution-independent.
* The source is 236px, so 16–192px are downscales (crisp) while 512px is an
  upscale (slightly soft). Android uses 512 only for a splash screen, where
  that is invisible; supply larger artwork if it ever matters.

Pillow is a build-time dependency only. It is deliberately absent from
requirements.txt — the running app never resizes anything, it just serves the
files this script commits.
"""

from __future__ import annotations

import base64
from io import BytesIO
from pathlib import Path
from typing import Optional, Tuple

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "analyzewebsite_web" / "assets" / "ten_capital_mark.png"
STATIC = ROOT / "analyzewebsite_web" / "static"

# Sampled from the artwork itself, for anything that needs the brand values.
CORAL = "#ED5644"
AMBER = "#F1A31F"
TEAL = "#4FC4D6"

# The navy the iOS icon is composited onto; transparency there renders on black.
IOS_BACKGROUND = "#0B1526"

# Clear space around the mark, as a proportion of the icon. The artwork ships
# with its own padding, which is trimmed first so this applies consistently.
PADDING = 0.06

PNG_SIZES: Tuple[int, ...] = (16, 32, 192, 512)


def load_mark() -> Image.Image:
    """The artwork, trimmed to its content and squared up."""
    if not SOURCE.is_file():
        raise SystemExit(
            f"Brand artwork not found at {SOURCE}.\n"
            "Place the TEN Capital circle mark there (transparent PNG) and re-run."
        )
    image = Image.open(SOURCE).convert("RGBA")

    bbox = image.getbbox()
    if bbox:
        image = image.crop(bbox)

    # Square the canvas so a non-square crop is centred rather than stretched.
    side = max(image.size)
    square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    square.alpha_composite(image, ((side - image.width) // 2, (side - image.height) // 2))
    return square


def render(size: int, background: Optional[str] = None) -> Image.Image:
    """The mark at ``size`` px, with consistent clear space."""
    mark = load_mark()
    inner = max(1, round(size * (1 - 2 * PADDING)))
    resized = mark.resize((inner, inner), Image.LANCZOS)

    canvas = Image.new("RGBA", (size, size), background or (0, 0, 0, 0))
    offset = (size - inner) // 2
    canvas.alpha_composite(resized, (offset, offset))
    return canvas.convert("RGB") if background else canvas


def build_svg() -> str:
    """An SVG wrapping the artwork, so every icon shares one shape."""
    buffer = BytesIO()
    load_mark().save(buffer, format="PNG", optimize=True)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    pad = PADDING * 100
    span = 100 - 2 * pad
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" '
        'xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 100 100" '
        'role="img" aria-label="TEN Capital Network">\n'
        f'  <image x="{pad:.0f}" y="{pad:.0f}" width="{span:.0f}" height="{span:.0f}" '
        f'xlink:href="data:image/png;base64,{encoded}"/>\n'
        "</svg>\n"
    )


def main() -> None:
    STATIC.mkdir(parents=True, exist_ok=True)
    written = []

    svg = STATIC / "favicon.svg"
    svg.write_text(build_svg(), encoding="utf-8")
    written.append(svg)

    # Transparent PNGs: a tab strip may be light or dark and the mark reads on
    # both. Only the iOS icon gets a baked background.
    for size in PNG_SIZES:
        path = STATIC / f"favicon-{size}x{size}.png"
        render(size).save(path, format="PNG", optimize=True)
        written.append(path)

    # The plain name, for anything that expects exactly /favicon.png.
    plain = STATIC / "favicon.png"
    render(32).save(plain, format="PNG", optimize=True)
    written.append(plain)

    ico = STATIC / "favicon.ico"
    render(64).save(ico, format="ICO", sizes=[(16, 16), (32, 32), (48, 48), (64, 64)])
    written.append(ico)

    apple = STATIC / "apple-touch-icon.png"
    render(180, background=IOS_BACKGROUND).save(apple, format="PNG", optimize=True)
    written.append(apple)

    for path in written:
        print(f"wrote {path.relative_to(ROOT)} ({path.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
