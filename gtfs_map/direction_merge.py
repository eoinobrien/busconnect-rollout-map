"""Merge a route's two directions into a single geometry.

Each direction's GTFS shape is preserved as its own connected
LineString. When both exist, they're emitted as a MultiLineString
of two components. Each direction is also simplified to remove
small fold-back wobbles (terminal stand loops etc.) that the source
GTFS shape often carries.
"""

from __future__ import annotations

import pyproj
from shapely.geometry import LineString, MultiLineString
from shapely.ops import transform


_WGS84 = "EPSG:4326"
_ITM = "EPSG:2157"
_TO_ITM = pyproj.Transformer.from_crs(_WGS84, _ITM, always_xy=True)
_TO_WGS = pyproj.Transformer.from_crs(_ITM, _WGS84, always_xy=True)


# Vertices deviating less than this many metres perpendicular from
# the chord between their neighbours are removed (Douglas-Peucker).
# Drops fold-back wobbles, terminal stand loops, GTFS shape jitter —
# anything tighter than 30 m doesn't represent meaningful route shape.
_SIMPLIFY_TOLERANCE_M = 30.0


def _simplify(line: LineString) -> LineString:
    """Douglas-Peucker simplify in metres, preserving overall shape
    but dropping fold-backs and small detours.

    Short-circuits when simplification doesn't actually remove any
    vertices, returning the original line unchanged so callers
    don't see tiny float drift from a no-op projection round-trip.
    """
    if len(line.coords) < 3:
        return line
    proj = transform(lambda x, y, z=None: _TO_ITM.transform(x, y), line)
    simplified = proj.simplify(_SIMPLIFY_TOLERANCE_M, preserve_topology=False)
    if simplified.is_empty or len(simplified.coords) < 2:
        return line
    if len(simplified.coords) == len(proj.coords):
        # Nothing was removed; avoid the float drift of a round-trip.
        return line
    return transform(lambda x, y, z=None: _TO_WGS.transform(x, y), simplified)


def merge_directions(
    a: LineString,
    b: LineString | None,
    threshold_m: float = 30.0,
):
    """Combine two direction shapes of the same route into one
    geometry.

    Each direction is simplified individually (DP at 30 m) to remove
    fold-back wobbles from the source GTFS shape. Then:
      - `b is None` -> return the simplified `a` as a LineString.
      - both directions -> MultiLineString([simplified a, simplified b]).

    `threshold_m` is accepted for API compatibility but unused —
    the corridor-difference approach was rejected by the user as
    producing floating fragments.
    """
    a = _simplify(a)
    if b is None:
        return a
    b = _simplify(b)
    return MultiLineString([a, b])
