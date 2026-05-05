"""Merge a route's two directions into a single geometry.

Where both directions sit within `threshold_m` of each other for
their entire length, the result is the SHORTER of the two — a
curve in one direction collapses into the straight line of the
other. Where they truly diverge (one-way pairs on different
streets), the result is a MultiLineString containing the primary
plus the divergent residual.
"""

from __future__ import annotations

import pyproj
from shapely.geometry import LineString, MultiLineString, Point
from shapely.ops import transform


_WGS84 = "EPSG:4326"
_ITM = "EPSG:2157"
_TO_ITM = pyproj.Transformer.from_crs(_WGS84, _ITM, always_xy=True)
_TO_WGS = pyproj.Transformer.from_crs(_ITM, _WGS84, always_xy=True)


# Drop residual fragments shorter than this — they're slivers, not
# meaningful divergence.
# Drop residual fragments shorter than this. 500 m comfortably
# excludes brief wobbles and terminal-loop slivers while still
# preserving substantial one-way-pair divergence (Liffey quays,
# city-centre one-way pairs like Westmoreland/Pearse).
_MIN_RESIDUAL_M = 500.0


def _project_to_itm(geom):
    return transform(lambda x, y, z=None: _TO_ITM.transform(x, y), geom)


def _project_to_wgs(geom):
    return transform(lambda x, y, z=None: _TO_WGS.transform(x, y), geom)


def _residual_pieces(line_itm: LineString, primary_corridor) -> list[LineString]:
    """Return the parts of `line_itm` that fall outside primary_corridor,
    filtered to those longer than _MIN_RESIDUAL_M."""
    leftover = line_itm.difference(primary_corridor)
    if leftover.is_empty:
        return []
    if isinstance(leftover, LineString):
        candidates = [leftover]
    elif isinstance(leftover, MultiLineString):
        candidates = list(leftover.geoms)
    else:  # GeometryCollection
        candidates = [g for g in getattr(leftover, "geoms", []) if isinstance(g, LineString)]
    return [p for p in candidates if p.length >= _MIN_RESIDUAL_M]


def merge_directions(
    a: LineString,
    b: LineString | None,
    threshold_m: float = 30.0,
):
    """Merge two direction shapes of the same route.

    - Pick the SHORTER of the two as canonical (a curve is longer
      than the straight line it deviates from).
    - Compute the OTHER direction's residual outside the canonical's
      threshold-buffered corridor.
    - Drop residual pieces shorter than `_MIN_RESIDUAL_M` (wobbles,
      terminal slivers).
    - If no significant residual remains: return the canonical
      LineString.
    - Otherwise: return a MultiLineString of [canonical, ...
      residual_pieces], so genuine one-way-pair divergence (Liffey
      quays, city-centre one-way pairs) renders both visible legs.

    If `b is None`, `a` is returned unchanged.

    Inputs are LineStrings in WGS84 (lon/lat). Output is in WGS84.
    """
    if b is None:
        return a

    a_itm = _project_to_itm(a)
    b_itm = _project_to_itm(b)

    if a_itm.length <= b_itm.length:
        primary, primary_itm = a, a_itm
        other_itm = b_itm
    else:
        primary, primary_itm = b, b_itm
        other_itm = a_itm

    corridor = primary_itm.buffer(threshold_m)
    pieces = _residual_pieces(other_itm, corridor)
    if not pieces:
        return primary

    return MultiLineString([primary] + [_project_to_wgs(p) for p in pieces])
