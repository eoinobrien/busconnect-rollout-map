"""Synthetic junction tests that catch jaggedness regressions cheaply.

We assert hard geometric properties on bundle output for small,
hand-crafted inputs that simulate common Dublin road topologies:
  - parallel routes on the same road
  - routes meeting at a 4-way junction
  - routes diverging from a shared corridor

Failures here mean the bundle algorithm is producing geometry that
doesn't match the inputs — even if the route attribution is right.
"""

from __future__ import annotations

import math

import pyproj
from shapely.geometry import LineString, MultiLineString, shape

from gtfs_map.bundle import bundle_routes
from gtfs_map.smooth import smooth_line


_TO_ITM = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:2157", always_xy=True)


def _itm_length(coords) -> float:
    """Total length in metres of a list of (lon, lat) coords."""
    if len(coords) < 2:
        return 0.0
    pts = [_TO_ITM.transform(x, y) for x, y in coords]
    total = 0.0
    for i in range(1, len(pts)):
        total += math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1])
    return total


def _max_edge_m(coords) -> float:
    if len(coords) < 2:
        return 0.0
    pts = [_TO_ITM.transform(x, y) for x, y in coords]
    return max(
        math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1])
        for i in range(1, len(pts))
    )


def _all_lines(features):
    """Yield each Feature's component LineStrings (handles MultiLineString)."""
    for f in features:
        g = f["geometry"]
        if g["type"] == "LineString":
            yield f, g["coordinates"]
        else:  # MultiLineString
            for line in g["coordinates"]:
                yield f, line


def test_parallel_routes_produce_a_clean_straight_trunk():
    """Two routes 5 m apart on the same east-west road should bundle
    to one near-straight trunk. The output's max consecutive-vertex
    distance shouldn't exceed the road length plus a small slack."""
    a = LineString([(-6.30 + i * 0.0005, 53.30) for i in range(40)])  # ~3 km
    b = LineString([(-6.30 + i * 0.0005, 53.30005) for i in range(40)])  # ~5.5 m N

    features = bundle_routes({"A": a, "B": b}, tolerance_m=15.0)
    shared = [f for f in features if set(f["properties"]["route_set"]) == {"A", "B"}]
    assert shared, "expected an A+B shared trunk"

    for f, coords in _all_lines(shared):
        max_e = _max_edge_m(coords)
        # No edge should be more than a typical road-segment hop. If
        # bundling produced a "shortcut" jump the max would be huge.
        assert max_e < 200, (
            f"max consecutive-vertex edge is {max_e:.0f} m; bundle is "
            f"jumping or has a shortcut"
        )


def test_route_with_right_angle_turn_keeps_the_corner_visible():
    """A route that turns 90 degrees at a junction shouldn't get
    smoothed into a diagonal line. The DP smoothing tolerance must
    preserve corners that deviate clearly from a straight path."""
    # 1 km east, then 1 km north — sharp 90 degree turn at midpoint.
    east = [(-6.30 + i * 0.0001, 53.30) for i in range(150)]  # ~1 km E
    north = [(-6.285, 53.30 + i * 0.000045) for i in range(150)]  # ~1 km N
    line = LineString(east + north)

    features = bundle_routes({"R": line}, tolerance_m=10.0)
    assert len(features) >= 1
    # smooth at 4 m as in the pipeline
    f = features[0]
    g = shape(f["geometry"])
    smoothed = smooth_line(g if isinstance(g, LineString) else list(g.geoms)[0], tolerance_m=4.0)

    # The corner should still be in the smoothed output. Find the
    # vertex closest to the corner (-6.285, 53.30) and verify it's
    # within a few metres.
    corner_dist = min(
        math.hypot(
            (c[0] - -6.285) * 67_000,  # rough east distance metres
            (c[1] - 53.30) * 111_000,
        )
        for c in smoothed.coords
    )
    assert corner_dist < 30, f"corner at midpoint lost in smoothing: {corner_dist:.0f} m off"


def test_three_routes_meeting_at_a_junction_dont_share_route_sets_after_branch():
    """Three routes converge at a junction then go in different
    directions. Each post-junction branch should have its own
    route_set with only the route that takes that branch.
    """
    # Junction at (-6.295, 53.30). Three routes share path from
    # (-6.30, 53.30) -> (-6.295, 53.30), then split:
    common = [(-6.30 + i * 0.0001, 53.30) for i in range(50)]  # ~330 m east
    a_branch = [(-6.295 + i * 0.0001, 53.30) for i in range(50)]  # E
    b_branch = [(-6.295, 53.30 + i * 0.0001) for i in range(50)]  # N
    c_branch = [(-6.295, 53.30 - i * 0.0001) for i in range(50)]  # S
    a = LineString(common + a_branch[1:])
    b = LineString(common + b_branch[1:])
    c = LineString(common + c_branch[1:])

    features = bundle_routes({"A": a, "B": b, "C": c}, tolerance_m=10.0)
    sets_seen = {tuple(sorted(f["properties"]["route_set"])) for f in features}
    # Shared trunk
    assert ("A", "B", "C") in sets_seen, f"shared trunk missing; got {sets_seen}"
    # Each branch should be solo
    assert ("A",) in sets_seen
    assert ("B",) in sets_seen
    assert ("C",) in sets_seen


def test_no_huge_edge_in_bundled_output_for_dense_input():
    """If the input is densified at 5 m, no consecutive output edge
    should be more than ~80 m long. Larger jumps mean the algorithm
    has produced a teleportation between vertices.
    """
    line = LineString([(-6.30 + i * 0.00005, 53.30) for i in range(400)])  # ~1.3 km
    features = bundle_routes({"R": line})
    for f, coords in _all_lines(features):
        max_e = _max_edge_m(coords)
        assert max_e < 80, f"edge of {max_e:.0f} m on a densified straight line"
