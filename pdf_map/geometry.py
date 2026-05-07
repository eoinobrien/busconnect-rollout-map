"""Path primitives for PDF route extraction.

pymupdf returns each drawing as a list of items: straight lines `('l',
p0, p1)`, cubic beziers `('c', p0, c1, c2, p1)`, rectangles `('re',
rect)`, and quads `('qu', quad)`. Bus route lines on the Big Picture
map are typically chains of `l` and `c` items where each item starts
where the previous one ended, so we walk items left-to-right and
break into a new polyline whenever the current start point doesn't
match the previous end. Beziers are flattened to line segments by
parametric sampling — the source curves are gentle so 16 steps per
curve is enough to keep error well under 1 PDF point.
"""

from __future__ import annotations

from typing import Sequence

import fitz


_BEZIER_STEPS_DEFAULT = 16


def sample_cubic_bezier(
    p0: fitz.Point,
    p1: fitz.Point,
    p2: fitz.Point,
    p3: fitz.Point,
    *,
    steps: int = _BEZIER_STEPS_DEFAULT,
) -> list[tuple[float, float]]:
    """Sample a cubic Bezier at `steps + 1` evenly-spaced t values.

    Endpoints are exact (t=0, t=1). Returns plain (x, y) tuples in PDF
    coordinates so the caller can treat them like polyline vertices.
    """
    out: list[tuple[float, float]] = []
    for i in range(steps + 1):
        t = i / steps
        u = 1.0 - t
        x = (
            u * u * u * p0.x
            + 3 * u * u * t * p1.x
            + 3 * u * t * t * p2.x
            + t * t * t * p3.x
        )
        y = (
            u * u * u * p0.y
            + 3 * u * u * t * p1.y
            + 3 * u * t * t * p2.y
            + t * t * t * p3.y
        )
        out.append((x, y))
    return out


def _approx_equal(a: tuple[float, float], b: tuple[float, float], tol: float = 1e-3) -> bool:
    return abs(a[0] - b[0]) < tol and abs(a[1] - b[1]) < tol


def drawing_to_polylines(
    drawing: dict,
    *,
    bezier_steps: int = _BEZIER_STEPS_DEFAULT,
) -> list[list[tuple[float, float]]]:
    """Flatten a pymupdf drawing dict into one or more polylines.

    Items are walked in order. A new polyline starts whenever the next
    item's start point doesn't match the running endpoint (so a single
    `drawing` containing two disjoint sub-paths splits cleanly). Bezier
    items contribute `bezier_steps` interior points; rectangles emit
    their five-vertex closed outline; closed paths append the start
    vertex at the end of the final polyline.
    """
    polylines: list[list[tuple[float, float]]] = []
    current: list[tuple[float, float]] = []

    def _flush() -> None:
        nonlocal current
        if len(current) >= 2:
            polylines.append(current)
        current = []

    def _start_or_continue(start: tuple[float, float]) -> None:
        nonlocal current
        if not current:
            current = [start]
        elif not _approx_equal(current[-1], start):
            _flush()
            current = [start]

    for item in drawing.get("items", []):
        op = item[0]
        if op == "l":
            p0, p1 = item[1], item[2]
            _start_or_continue((p0.x, p0.y))
            current.append((p1.x, p1.y))
        elif op == "c":
            p0, c1, c2, p1 = item[1], item[2], item[3], item[4]
            _start_or_continue((p0.x, p0.y))
            sampled = sample_cubic_bezier(p0, c1, c2, p1, steps=bezier_steps)
            # First sample == p0; we already pushed it via _start_or_continue.
            current.extend(sampled[1:])
        elif op == "re":
            _flush()
            r = item[1]
            current = [
                (r.x0, r.y0),
                (r.x1, r.y0),
                (r.x1, r.y1),
                (r.x0, r.y1),
                (r.x0, r.y0),
            ]
            _flush()
        elif op == "qu":
            _flush()
            q = item[1]
            current = [
                (q.ul.x, q.ul.y),
                (q.ur.x, q.ur.y),
                (q.lr.x, q.lr.y),
                (q.ll.x, q.ll.y),
                (q.ul.x, q.ul.y),
            ]
            _flush()
        # Unknown ops are skipped — we never see them on this PDF.

    if drawing.get("closePath") and current and not _approx_equal(current[0], current[-1]):
        current.append(current[0])

    _flush()
    return polylines
