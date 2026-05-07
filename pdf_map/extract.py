"""Read drawings + text spans from the BusConnects Big Picture PDF.

Two read paths:
  - `extract_paths()` walks every drawing on page 0, flattens it to
    polylines via `geometry.drawing_to_polylines`, and returns one
    `Path` per polyline carrying its stroke colour, width, and the
    underlying drawing index. Filters out trivially small paths to
    keep downstream colour-cluster analysis tractable.
  - `extract_text_spans()` returns every visible text span with its
    bbox centre, font size, and rgb colour — these are the route
    shields and landmark labels we use for georeferencing.

We deliberately keep this thin: no clustering, no semantic naming.
Higher-level modules (`spine`, `georef`, `match`) consume these
neutral records.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path as _FsPath
from typing import Optional

import fitz

from .geometry import drawing_to_polylines


RGB = tuple[float, float, float]


@dataclass(frozen=True)
class Path:
    """One flattened polyline from the PDF."""

    drawing_index: int
    polyline_index: int  # which sub-polyline within the drawing
    stroke: Optional[RGB]
    fill: Optional[RGB]
    width: Optional[float]
    points: tuple[tuple[float, float], ...]
    bbox: tuple[float, float, float, float]  # (x0, y0, x1, y1)

    @property
    def diag(self) -> float:
        x0, y0, x1, y1 = self.bbox
        dx, dy = x1 - x0, y1 - y0
        return (dx * dx + dy * dy) ** 0.5


@dataclass(frozen=True)
class TextSpan:
    """One visible text span (route shield, landmark label, etc.)."""

    text: str
    bbox: tuple[float, float, float, float]
    color: Optional[RGB]
    size: float
    font: str

    @property
    def center(self) -> tuple[float, float]:
        x0, y0, x1, y1 = self.bbox
        return ((x0 + x1) / 2.0, (y0 + y1) / 2.0)


def _round_rgb(c) -> Optional[RGB]:
    if c is None:
        return None
    return (round(float(c[0]), 4), round(float(c[1]), 4), round(float(c[2]), 4))


def _bbox_of(points: tuple[tuple[float, float], ...]) -> tuple[float, float, float, float]:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


def extract_paths(
    pdf_path: _FsPath | str,
    *,
    page_index: int = 0,
    min_diag: float = 1.0,
    bezier_steps: int = 16,
) -> list[Path]:
    """Return every flattened polyline whose bbox diagonal exceeds
    `min_diag` PDF points. The default of 1pt drops degenerate
    zero-length artefacts but keeps short shield outlines so the
    caller can still reason about labels if desired. Ordering follows
    pymupdf's drawing emission order (roughly z-stack), preserved on
    the returned `drawing_index`/`polyline_index` so callers can
    correlate paths with the original PDF when debugging.
    """
    doc = fitz.open(str(pdf_path))
    try:
        page = doc[page_index]
        drawings = page.get_drawings()
    finally:
        # Defer close until after we've finished pulling rect data —
        # but `get_drawings()` materialises everything as plain dicts,
        # so closing here is safe.
        doc.close()

    out: list[Path] = []
    for di, d in enumerate(drawings):
        polylines = drawing_to_polylines(d, bezier_steps=bezier_steps)
        for pi, pl in enumerate(polylines):
            pts = tuple(pl)
            bbox = _bbox_of(pts)
            x0, y0, x1, y1 = bbox
            diag = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
            if diag < min_diag:
                continue
            out.append(
                Path(
                    drawing_index=di,
                    polyline_index=pi,
                    stroke=_round_rgb(d.get("color")),
                    fill=_round_rgb(d.get("fill")),
                    width=(
                        round(float(d["width"]), 3)
                        if d.get("width") is not None
                        else None
                    ),
                    points=pts,
                    bbox=bbox,
                )
            )
    return out


def extract_text_spans(
    pdf_path: _FsPath | str,
    *,
    page_index: int = 0,
) -> list[TextSpan]:
    """Return every non-empty text span on the requested page."""
    doc = fitz.open(str(pdf_path))
    try:
        page = doc[page_index]
        td = page.get_text("dict")
    finally:
        doc.close()

    out: list[TextSpan] = []
    for block in td.get("blocks", []):
        if block.get("type") != 0:  # 0 == text block
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                txt = span.get("text", "").strip()
                if not txt:
                    continue
                color_int = span.get("color")
                color: Optional[RGB] = None
                if isinstance(color_int, int):
                    r = ((color_int >> 16) & 0xFF) / 255.0
                    g = ((color_int >> 8) & 0xFF) / 255.0
                    b = (color_int & 0xFF) / 255.0
                    color = (round(r, 4), round(g, 4), round(b, 4))
                bbox = tuple(span.get("bbox", (0.0, 0.0, 0.0, 0.0)))
                out.append(
                    TextSpan(
                        text=txt,
                        bbox=(float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])),
                        color=color,
                        size=float(span.get("size", 0.0)),
                        font=str(span.get("font", "")),
                    )
                )
    return out
