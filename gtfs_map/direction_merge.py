"""Merge a route's two directions into a single geometry.

Each direction's GTFS shape is preserved as its own connected
LineString. When both exist, they're emitted as a MultiLineString
of two components so each direction is a complete connected route;
the user-facing Feature is "the route, both directions".
"""

from __future__ import annotations

from shapely.geometry import LineString, MultiLineString


def merge_directions(
    a: LineString,
    b: LineString | None,
    threshold_m: float = 30.0,
):
    """Combine two direction shapes of the same route into one
    geometry.

    - If `b is None`: return `a` as a LineString.
    - Otherwise: return MultiLineString([a, b]). Each direction is
      a full connected LineString — no corridor difference, no
      canonical-picking, no curve simplification. Where the two
      directions share a road they overlap visually; where they
      diverge (one-way pairs) both legs are rendered.

    `threshold_m` is accepted for API compatibility but unused —
    the corridor-difference approach was rejected by the user as
    producing floating fragments.
    """
    if b is None:
        return a
    return MultiLineString([a, b])
