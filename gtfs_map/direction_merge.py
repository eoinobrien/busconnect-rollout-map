"""Merge a route's two directions into a single geometry.

Each direction's GTFS shape is preserved as its own connected
LineString. When both exist, they're emitted as a MultiLineString
of two components. Each direction's path is also walked once to
drop fold-back vertices — points where the route briefly doubles
back on itself (typical at termini where the bus enters a stand
and exits via a tight loop).
"""

from __future__ import annotations

import math

import pyproj
from shapely.geometry import LineString, MultiLineString
from shapely.ops import transform


_WGS84 = "EPSG:4326"
_ITM = "EPSG:2157"
_TO_ITM = pyproj.Transformer.from_crs(_WGS84, _ITM, always_xy=True)
_TO_WGS = pyproj.Transformer.from_crs(_ITM, _WGS84, always_xy=True)


# Drop vertex b when the detour-ratio (a->b->c)/(a->c) exceeds this.
# A 60 degree turn has ratio 2.0; a 45 degree turn 2.6; a 30 degree
# turn 3.9; a U-turn (~10 degrees included angle) has ratio 11.5.
# 4.0 keeps real bends including very sharp ones while dropping
# fold-back wobbles where the path doubles back on itself.
_FOLD_DETOUR_RATIO = 4.0


def _drop_fold_backs(line: LineString) -> LineString:
    """Remove vertices b where the path a->b->c is more than
    `_FOLD_DETOUR_RATIO` times longer than the direct chord a->c.

    Walks the line once in projected (metric) space. A genuine bend,
    even a 30-45 degree turn, has ratio < 3 so it survives. A
    fold-back/U-turn vertex has ratio 5+ and is removed.
    """
    if len(line.coords) < 3:
        return line
    proj = [_TO_ITM.transform(x, y) for x, y in line.coords]

    kept = [proj[0]]
    for i in range(1, len(proj) - 1):
        a = kept[-1]
        b = proj[i]
        c = proj[i + 1]
        ab = math.hypot(b[0] - a[0], b[1] - a[1])
        bc = math.hypot(c[0] - b[0], c[1] - b[1])
        ac = math.hypot(c[0] - a[0], c[1] - a[1])
        if ac == 0:
            # a == c: drop b (lies on the same point)
            continue
        if (ab + bc) / ac > _FOLD_DETOUR_RATIO:
            # b is a detour vertex; skip it
            continue
        kept.append(b)
    kept.append(proj[-1])

    if len(kept) == len(proj):
        # No vertex dropped — return the original line untouched so
        # callers don't see float drift from a no-op round-trip.
        return line
    return LineString([_TO_WGS.transform(x, y) for x, y in kept])


def merge_directions(
    a: LineString,
    b: LineString | None,
    threshold_m: float = 30.0,
):
    """Combine two direction shapes of the same route into one
    geometry.

    Each direction is walked through `_drop_fold_backs` to remove
    only fold-back / U-turn vertices (terminal stand loops, brief
    backtracks). Real bends and curves are preserved entirely.
    Then:
      - `b is None` -> return the cleaned `a` as a LineString.
      - both directions -> MultiLineString([cleaned a, cleaned b]).

    `threshold_m` is accepted for API compatibility but unused.
    """
    a = _drop_fold_backs(a)
    if b is None:
        return a
    b = _drop_fold_backs(b)
    return MultiLineString([a, b])
