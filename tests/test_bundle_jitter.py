"""Jitter tests for cross-route bundling.

These run in <2 seconds against synthetic 2-3 km input lines and
assert the bundled output stays close to a straight line — i.e. it
doesn't oscillate perpendicular to the corridor. Catches regressions
in the centroid-vs-snap design without needing a full GTFS rebuild.
"""

import math

import pyproj
from shapely.geometry import LineString
from shapely.ops import transform

from gtfs_map.bundle import bundle_routes
from gtfs_map.smooth import smooth_line


_TO_ITM = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:2157", always_xy=True)


def _itm_length(line_wgs: LineString) -> float:
    return transform(
        lambda x, y, z=None: _TO_ITM.transform(x, y), line_wgs
    ).length


def _straight_line_distance(line_wgs: LineString) -> float:
    coords = list(line_wgs.coords)
    if len(coords) < 2:
        return 0.0
    sx, sy = _TO_ITM.transform(coords[0][0], coords[0][1])
    ex, ey = _TO_ITM.transform(coords[-1][0], coords[-1][1])
    return math.hypot(ex - sx, ey - sy)


def _wiggle_ratio(line_wgs: LineString) -> float:
    """Length / straight-line distance. 1.0 is dead-straight; >1.05
    means the line meanders or zigzags more than typical road
    geometry would justify."""
    sl = _straight_line_distance(line_wgs)
    if sl < 1.0:
        return 1.0
    return _itm_length(line_wgs) / sl


def _bundle_and_smooth(routes, tolerance_m=25.0, smooth_m=4.0):
    feats = bundle_routes(routes, tolerance_m=tolerance_m)
    out = []
    for f in feats:
        geom = LineString(f["geometry"]["coordinates"])
        smoothed = smooth_line(geom, tolerance_m=smooth_m)
        out.append({"feature": f, "geom": smoothed})
    return out


def _route_sets(features):
    return [tuple(sorted(f["properties"]["route_set"])) for f in features]


def _make_parallel(lat: float, n_steps: int = 40, span_lon: float = 0.10):
    """Build a horizontal LineString at the given lat, sampled densely
    so the bundling has plenty of points to work with."""
    return LineString([
        (-6.30 + i * span_lon / (n_steps - 1), lat) for i in range(n_steps)
    ])


# --- Jitter tests ---------------------------------------------------------


def test_two_parallel_routes_produce_a_straight_bundled_line():
    a = _make_parallel(53.30000)
    b = _make_parallel(53.30010)  # ~11 m north — within 25 m tolerance

    bundled = _bundle_and_smooth({"A": a, "B": b}, tolerance_m=25.0)

    shared = [b for b in bundled if ("A", "B") == tuple(sorted(b["feature"]["properties"]["route_set"]))]
    assert shared, f"expected a shared A+B feature, got {[b['feature']['properties']['route_set'] for b in bundled]}"

    for s in shared:
        ratio = _wiggle_ratio(s["geom"])
        assert ratio < 1.02, (
            f"shared bundled line has wiggle ratio {ratio:.4f}; "
            f"expected near-straight (<1.02). Length="
            f"{_itm_length(s['geom']):.1f} m vs straight={_straight_line_distance(s['geom']):.1f} m"
        )


def test_three_parallel_routes_at_5_10_15_m_offsets_produce_clean_trunk():
    # Three lines close together. Tight enough that all three should
    # bundle into one trunk.
    a = _make_parallel(53.30000)
    b = _make_parallel(53.30005)  # ~5 m
    c = _make_parallel(53.30015)  # ~17 m

    bundled = _bundle_and_smooth({"A": a, "B": b, "C": c}, tolerance_m=25.0)

    full_trunks = [b for b in bundled if ("A", "B", "C") == tuple(sorted(b["feature"]["properties"]["route_set"]))]
    assert full_trunks, "expected an A+B+C trunk"

    for t in full_trunks:
        ratio = _wiggle_ratio(t["geom"])
        assert ratio < 1.03, (
            f"3-route trunk wiggle ratio {ratio:.4f}; expected <1.03"
        )


def test_offset_sampled_routes_dont_zigzag_on_bundle():
    # Two lines on the same road, sampled at slightly different
    # phases — one densified at offset 0, the other at offset 2.5 m
    # (half-step). Real GTFS shapes look like this.
    n = 80
    a = LineString([(-6.30 + i * 0.001, 53.30000) for i in range(n)])
    # b sampled with a half-step phase offset
    b = LineString([(-6.30 + (i + 0.5) * 0.001, 53.30005) for i in range(n - 1)])

    bundled = _bundle_and_smooth({"A": a, "B": b}, tolerance_m=25.0)

    shared = [bb for bb in bundled if ("A", "B") == tuple(sorted(bb["feature"]["properties"]["route_set"]))]
    assert shared

    for s in shared:
        ratio = _wiggle_ratio(s["geom"])
        assert ratio < 1.02, (
            f"phase-offset sampled inputs produced wiggle ratio {ratio:.4f}"
        )


def test_disjoint_routes_each_render_straight():
    # Two routes far apart — each should be its own straight line in
    # the output, with no jitter introduced by the bundling pass.
    a = _make_parallel(53.30)
    b = _make_parallel(53.40)  # ~11 km away

    bundled = _bundle_and_smooth({"A": a, "B": b}, tolerance_m=25.0)

    for bb in bundled:
        ratio = _wiggle_ratio(bb["geom"])
        assert ratio < 1.02, (
            f"disjoint route's own line wiggles {ratio:.4f} — bundling "
            "must not touch routes that don't share a corridor"
        )


def test_canonical_position_anchored_to_longest_route():
    # Long route A and short route B run on the same corridor. After
    # bundling, the shared trunk should sit exactly on A's geometry,
    # not on a midpoint between A and B (which would produce centroid
    # jitter when the pairing isn't perfect).
    a = LineString([
        (-6.30 + i * 0.001, 53.30) for i in range(60)
    ])  # ~6 km long
    b = LineString([
        (-6.27 + i * 0.001, 53.30005) for i in range(20)
    ])  # ~2 km, parallel ~5.5 m north of A

    bundled = _bundle_and_smooth({"A": a, "B": b}, tolerance_m=20.0, smooth_m=2.0)
    shared = [bb for bb in bundled if ("A", "B") == tuple(sorted(bb["feature"]["properties"]["route_set"]))]
    assert shared, "expected an A+B shared segment"

    # The shared segment's lat should be A's lat (53.30), not the
    # midpoint (~53.300025).
    for s in shared:
        for lon, lat in s["geom"].coords:
            assert abs(lat - 53.30) < 0.00002, (
                f"shared trunk lat {lat} should snap to A's 53.30, "
                f"not centroid 53.300025"
            )


def test_l_shaped_sharing_keeps_corner_clean():
    # Two routes share a 5 km east leg, then diverge: A turns north,
    # B continues east. The corner at (-6.20, 53.30) should be sharp;
    # the shared east leg should be straight.
    a = LineString([
        *((-6.30 + i * 0.005, 53.30) for i in range(20)),  # east leg
        *((-6.20, 53.30 + i * 0.005) for i in range(1, 20)),  # north leg
    ])
    b = LineString([
        *((-6.30 + i * 0.005, 53.30) for i in range(20)),  # east leg
        *((-6.20 + i * 0.005, 53.30) for i in range(1, 20)),  # continue east
    ])

    bundled = _bundle_and_smooth({"A": a, "B": b}, tolerance_m=25.0)

    sets = _route_sets([bb["feature"] for bb in bundled])
    # Shared east leg must exist
    assert ("A", "B") in sets, f"got {sets}"
    # Each route's divergent leg must exist
    assert ("A",) in sets
    assert ("B",) in sets

    # The shared trunk should be near-straight
    shared = [bb for bb in bundled if tuple(sorted(bb["feature"]["properties"]["route_set"])) == ("A", "B")]
    for s in shared:
        ratio = _wiggle_ratio(s["geom"])
        assert ratio < 1.05, f"L-shape trunk wiggles {ratio:.4f}"
