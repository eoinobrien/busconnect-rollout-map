from collections import Counter

from shapely.geometry import LineString, shape

from gtfs_map.bundle import bundle_spine


# All test geometries use lon/lat near Dublin (53.3N, -6.2W) so projection
# to Irish Transverse Mercator behaves realistically. Points are spaced
# in roughly 0.01 degree increments (~1 km) so they survive 5 m
# densification + 2 m quantization without merging.


def _route_sets(features):
    return [tuple(sorted(f["properties"]["route_set"])) for f in features]


def _kinds(features):
    return Counter(f["properties"]["kind"] for f in features)


def test_two_overlapping_routes_produce_a_trunk_and_two_branches():
    # A1 goes east then north; A2 goes east then continues east.
    # Shared bottom-east segment is the trunk; the north and far-east
    # segments are branches.
    a1 = LineString([(-6.30, 53.30), (-6.20, 53.30), (-6.20, 53.40)])
    a2 = LineString([(-6.30, 53.30), (-6.20, 53.30), (-6.10, 53.30)])

    features = bundle_spine({"A1": a1, "A2": a2})

    sets = _route_sets(features)
    assert ("A1", "A2") in sets, "trunk segment shared by both should exist"
    assert ("A1",) in sets, "A1-only branch should exist"
    assert ("A2",) in sets, "A2-only branch should exist"

    kinds = _kinds(features)
    assert kinds["trunk"] >= 1
    assert kinds["branch"] >= 2


def test_two_fully_overlapping_routes_produce_only_a_trunk():
    line = LineString([(-6.30, 53.30), (-6.20, 53.30), (-6.10, 53.30)])

    features = bundle_spine({"B1": line, "B2": line})

    assert len(features) >= 1
    assert all(
        sorted(f["properties"]["route_set"]) == ["B1", "B2"] for f in features
    )
    assert all(f["properties"]["kind"] == "trunk" for f in features)


def test_two_disjoint_routes_produce_two_branch_features_with_no_trunk():
    a1 = LineString([(-6.30, 53.30), (-6.25, 53.30)])
    a2 = LineString([(-6.20, 53.40), (-6.15, 53.40)])

    features = bundle_spine({"A1": a1, "A2": a2})

    sets = _route_sets(features)
    assert ("A1",) in sets
    assert ("A2",) in sets
    # No edge is shared, so nothing should be a trunk
    kinds = _kinds(features)
    assert kinds.get("trunk", 0) == 0
    assert kinds["branch"] == 2


def test_geometry_is_in_lon_lat_wgs84_after_round_trip():
    line = LineString([(-6.30, 53.30), (-6.20, 53.30)])
    features = bundle_spine({"X1": line})

    assert len(features) == 1
    geom = shape(features[0]["geometry"])
    coords = list(geom.coords)
    # First and last points should still be near the original WGS84
    # coords (within projection round-trip + 2 m grid quantization)
    assert abs(coords[0][0] - -6.30) < 0.001
    assert abs(coords[0][1] - 53.30) < 0.001
    assert abs(coords[-1][0] - -6.20) < 0.001
    assert abs(coords[-1][1] - 53.30) < 0.001


def test_three_routes_trunk_count_matches_spine_size():
    # All three share the bottom; only one branches off.
    common = [(-6.30, 53.30), (-6.20, 53.30)]
    c1 = LineString(common + [(-6.10, 53.30)])
    c2 = LineString(common + [(-6.10, 53.30)])
    c3 = LineString(common + [(-6.20, 53.40)])

    features = bundle_spine({"C1": c1, "C2": c2, "C3": c3})

    # Trunk must list all three
    trunks = [f for f in features if f["properties"]["kind"] == "trunk"]
    assert len(trunks) >= 1
    for t in trunks:
        assert sorted(t["properties"]["route_set"]) == ["C1", "C2", "C3"]
        assert t["properties"]["route_count"] == 3
