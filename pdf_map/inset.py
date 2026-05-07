"""Reject paths that don't belong to the main Dublin map.

Two failure modes show up in the raw extracted paths:
  - Paths inside the city-centre detail insets - rectangular panels
    on the right side of the page (PDF x in [882, 1132]) that
    re-render the city centre at a different scale. After the
    main-map affine these project to "east of Dublin."
  - Paths drawn outside the page mediabox - the PDF stream contains
    artwork below y = 842 (the page bottom) that's clipped from
    the printed output. Our affine, fit on landmarks well above
    that line, extrapolates these wildly out into the Irish Sea.

Both kinds get filtered here. `is_inside_inset` covers panel
content; `is_off_page` covers anything outside the page rect.
"""

from __future__ import annotations

from .extract import Path


# Right-side panel strip on the 2025-10 revision: covers both the
# "North Dublin" and "South Dublin" detail insets. The detected
# stroked-rectangle frames understate the visible panels (probably
# inner content boxes rather than outer chrome), so we go with one
# generous strip from x=875 to the page edge. Main-map content
# stays west of x=855 (Howth Head, the easternmost real corridor),
# so the 20pt margin is safe.
INSET_BBOXES: tuple[tuple[float, float, float, float], ...] = (
    (875.0, 0.0, 1190.55, 841.89),
)


# A3 landscape mediabox for this PDF revision; paths whose
# majority of points sit outside this rect get rejected.
PAGE_BBOX: tuple[float, float, float, float] = (0.0, 0.0, 1190.55, 841.89)


def is_inside_inset(
    path: Path,
    bboxes: tuple[tuple[float, float, float, float], ...] = INSET_BBOXES,
    *,
    threshold: float = 0.3,
) -> bool:
    """True if at least `threshold` fraction of the path's points
    lie inside the union of inset rectangles.

    Counts a point as "inset" if it falls inside *any* inset rect;
    a path that straddles the upper and lower panels still gets
    classified as inset content. Threshold defaults to 30% - some
    inset routes have long tails that extend slightly outside the
    panel frame (connector lines, off-page sweeps), so a strict
    50% would let them through.
    """
    if not path.points:
        return False
    inside = 0
    for px, py in path.points:
        for x0, y0, x1, y1 in bboxes:
            if x0 <= px <= x1 and y0 <= py <= y1:
                inside += 1
                break
    return inside / len(path.points) >= threshold


def is_off_page(
    path: Path,
    page_bbox: tuple[float, float, float, float] = PAGE_BBOX,
    *,
    threshold: float = 0.5,
) -> bool:
    """True if a majority of points sit outside the page mediabox."""
    if not path.points:
        return False
    x0, y0, x1, y1 = page_bbox
    on_page = sum(
        1 for px, py in path.points if x0 <= px <= x1 and y0 <= py <= y1
    )
    return on_page / len(path.points) < (1.0 - threshold)


def reject_inset_paths(
    paths: list[Path],
    bboxes: tuple[tuple[float, float, float, float], ...] = INSET_BBOXES,
) -> list[Path]:
    """Return paths that aren't inset content and aren't off-page."""
    return [p for p in paths if not is_inside_inset(p, bboxes) and not is_off_page(p)]
