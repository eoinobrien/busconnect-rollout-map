"""Reject paths drawn in the PDF stream but hidden under fills.

The Big Picture A3 PDF contains route-line drawings that aren't
visible on the printed page - they sit underneath the page-wide
sea-blue background, so the PDF rasterizes them invisibly. pymupdf
extracts them anyway because they're real entries in the content
stream, and they project (via the main-map affine) into Dublin Bay
where there are no real bus routes. Visually they look like a
phantom duplicate Dublin layer.

The fix: rasterize the page once, sample the rendered pixel colour
under each path's vertices, and drop any path whose sampled pixels
are mostly the bay/sea-blue or other "no content" colours. A path
that's actually drawn on top of land or rendered as a coloured
route stays in.
"""

from __future__ import annotations

from pathlib import Path as _FsPath

import fitz
from PIL import Image

from .extract import Path


# Sea-blue (Dublin Bay / Irish Sea) and a couple of map-furniture
# colours that count as "no real route here" - sampled from the
# rendered PDF. Tolerance is generous because anti-aliasing softens
# pixels at colour boundaries.
_HIDDEN_COLOURS_RGB: tuple[tuple[int, int, int], ...] = (
    (171, 225, 250),  # bay sea-blue
    (255, 255, 255),  # off-page / clipped white
)
_HIDDEN_TOL = 18


def _is_hidden_pixel(rgb: tuple[int, int, int]) -> bool:
    for ref in _HIDDEN_COLOURS_RGB:
        if (
            abs(rgb[0] - ref[0]) <= _HIDDEN_TOL
            and abs(rgb[1] - ref[1]) <= _HIDDEN_TOL
            and abs(rgb[2] - ref[2]) <= _HIDDEN_TOL
        ):
            return True
    return False


def reject_hidden_paths(
    paths: list[Path],
    pdf_path: _FsPath | str,
    *,
    page_index: int = 0,
    scale: float = 2.0,
    sample_count: int = 8,
    threshold: float = 0.5,
) -> list[Path]:
    """Filter out paths whose sampled vertices fall on hidden pixels.

    Renders page `page_index` at `scale` once, samples
    `sample_count` evenly spaced vertices from each path, and drops
    the path if at least `threshold` fraction of those samples land
    on a "hidden" colour (bay sea-blue, off-page white). Cheap
    relative to the full pipeline; costs ~1s for the rasterise plus
    O(paths * sample_count) pixel lookups.
    """
    doc = fitz.open(str(pdf_path))
    try:
        page = doc[page_index]
        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale))
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    finally:
        doc.close()

    out: list[Path] = []
    for path in paths:
        n = len(path.points)
        if n == 0:
            continue
        step = max(1, n // sample_count)
        sampled = list(range(0, n, step))[:sample_count]
        hidden = 0
        for idx in sampled:
            x, y = path.points[idx]
            sx, sy = int(x * scale), int(y * scale)
            if not (0 <= sx < img.width and 0 <= sy < img.height):
                hidden += 1
                continue
            if _is_hidden_pixel(img.getpixel((sx, sy))):
                hidden += 1
        if hidden / len(sampled) < threshold:
            out.append(path)
    return out
