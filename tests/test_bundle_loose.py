"""Cross-route bundling with a wider tolerance so two near-parallel
shapes on the same road get treated as one corridor."""

from collections import Counter

from shapely.geometry import LineString

from gtfs_map.bundle import bundle_routes


def _route_sets(features):
    return [tuple(sorted(f["properties"]["route_set"])) for f in features]


def test_two_near_parallel_lines_bundle_under_loose_tolerance():
    # Two horizontal lines about 11 m apart at lat 53.30 (1 deg lat ≈
    # 111 km, so 0.0001 deg ≈ 11 m).
    a = LineString([(-6.30, 53.30000), (-6.20, 53.30000)])
    b = LineString([(-6.30, 53.30010), (-6.20, 53.30010)])

    # Default (~2 m grid) — they don't bundle.
    feats_tight = bundle_routes({"A": a, "B": b})
    sets_tight = _route_sets(feats_tight)
    assert ("A", "B") not in sets_tight, "tight bundle should not merge 11 m offset"

    # 20 m tolerance — they DO bundle.
    feats_loose = bundle_routes({"A": a, "B": b}, tolerance_m=20.0)
    sets_loose = _route_sets(feats_loose)
    assert ("A", "B") in sets_loose, (
        f"20 m tolerance should merge A and B; got {sets_loose}"
    )


def test_loose_bundle_keeps_truly_disjoint_routes_separate():
    a = LineString([(-6.30, 53.30), (-6.20, 53.30)])
    b = LineString([(-6.30, 53.40), (-6.20, 53.40)])  # 11 km apart

    feats = bundle_routes({"A": a, "B": b}, tolerance_m=20.0)
    sets = _route_sets(feats)
    assert ("A", "B") not in sets
    assert ("A",) in sets
    assert ("B",) in sets


def test_three_close_lines_all_bundle():
    a = LineString([(-6.30, 53.30000), (-6.20, 53.30000)])
    b = LineString([(-6.30, 53.30005), (-6.20, 53.30005)])  # ~5 m
    c = LineString([(-6.30, 53.30015), (-6.20, 53.30015)])  # ~17 m

    feats = bundle_routes({"A": a, "B": b, "C": c}, tolerance_m=20.0)
    sets = _route_sets(feats)
    assert ("A", "B", "C") in sets, f"got {sets}"


def test_default_bundle_unchanged_when_no_tolerance_passed():
    # Calling without tolerance_m should still produce the existing
    # tight bundling, so spine-letter bundles in test_bundle.py keep
    # passing.
    line = LineString([(-6.30, 53.30), (-6.20, 53.30)])
    feats = bundle_routes({"X": line})
    assert _route_sets(feats) == [("X",)]
