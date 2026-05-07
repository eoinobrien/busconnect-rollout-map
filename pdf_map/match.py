"""Associate route shields with the route-line polylines they label.

The Big Picture map places a route shield (e.g. `C1`, `N4`, `L25`)
*on top of* its corresponding line segment, repeated 12-23 times
along the line. Empirically the shield centroid sits within ~3 PDF
points of the underlying polyline. We exploit this directly:

  - Identify shield-shaped text spans by regex.
  - For every route-line candidate path, find any shield whose
    centroid lies within `MAX_SHIELD_DIST_PT` of *any* point on
    the path.
  - A path can carry multiple route_ids — shared corridors hold
    several spine routes whose shields all sit on the same line.

The output is a list of `(path_index, route_ids)` so callers can
attach the route_id metadata when projecting to GeoJSON. Shared-
corridor segments will appear once per layer and the front-end can
expand them into per-route fragments if needed.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass

from .extract import Path, TextSpan


# Match: spine + numeric (A1..H9), local (L\d{1,2}), orbital (N|S|W|E + digit),
# peak (X\d), or plain numeric route up to 3 digits with optional trailing
# letter. Case-insensitive — labels on the PDF are uppercase but stay
# tolerant.
SHIELD_RE = re.compile(
    r"^(?:[A-H]\d{1,2}|L\d{1,2}|[NSWE]\d{1,2}|X\d{1,2}|\d{1,3}[A-Za-z]?)$"
)


# Empirically tuned: shields sit on or immediately beside their
# route line. 4pt window covers placement variation without picking
# up a parallel route's line in the same corridor (the inter-line
# spacing on the PDF is ~6pt for stacked spines).
MAX_SHIELD_DIST_PT = 4.0


@dataclass(frozen=True)
class ShieldHit:
    """One (path, route_id) association with the closest distance."""

    path_index: int
    route_id: str
    distance_pt: float


def find_route_shields(spans: list[TextSpan]) -> list[tuple[str, TextSpan]]:
    """Return (route_id, span) for every span whose text matches a
    plausible route shield. Route_id is the shield's text uppercased.
    """
    out: list[tuple[str, TextSpan]] = []
    for s in spans:
        if SHIELD_RE.match(s.text):
            out.append((s.text.upper(), s))
    return out


def _point_to_polyline_distance(
    px: float, py: float, polyline: tuple[tuple[float, float], ...]
) -> float:
    """Minimum distance from (px, py) to any segment of `polyline`."""
    if not polyline:
        return float("inf")
    if len(polyline) == 1:
        dx = polyline[0][0] - px
        dy = polyline[0][1] - py
        return (dx * dx + dy * dy) ** 0.5

    best = float("inf")
    for i in range(len(polyline) - 1):
        ax, ay = polyline[i]
        bx, by = polyline[i + 1]
        dx, dy = bx - ax, by - ay
        seg_len_sq = dx * dx + dy * dy
        if seg_len_sq == 0:
            t = 0.0
        else:
            t = ((px - ax) * dx + (py - ay) * dy) / seg_len_sq
            t = max(0.0, min(1.0, t))
        cx = ax + t * dx
        cy = ay + t * dy
        d = ((cx - px) ** 2 + (cy - py) ** 2) ** 0.5
        if d < best:
            best = d
    return best


def associate_shields_with_paths(
    paths: list[Path],
    shields: list[tuple[str, TextSpan]],
    *,
    max_dist_pt: float = MAX_SHIELD_DIST_PT,
) -> dict[int, set[str]]:
    """For each path, return the set of route_ids whose shield centroid
    lies within `max_dist_pt` of any point on the path.

    Iteration is naive O(paths * shields). On a typical Big Picture
    PDF: ~400 candidate paths * ~1100 shields = ~440k checks, each
    cheap because we early-exit by axis-aligned bbox. Runs in well
    under a second.
    """
    by_path: dict[int, set[str]] = defaultdict(set)
    for pi, path in enumerate(paths):
        x0, y0, x1, y1 = path.bbox
        # Pad the bbox by max_dist so a shield just outside still gets considered
        x0 -= max_dist_pt
        y0 -= max_dist_pt
        x1 += max_dist_pt
        y1 += max_dist_pt
        for route_id, span in shields:
            cx, cy = span.center
            if cx < x0 or cx > x1 or cy < y0 or cy > y1:
                continue
            d = _point_to_polyline_distance(cx, cy, path.points)
            if d <= max_dist_pt:
                by_path[pi].add(route_id)
    return dict(by_path)
