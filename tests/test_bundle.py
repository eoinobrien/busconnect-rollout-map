"""Bundling primitives.

Step 1: `share_corridor(a, b, tolerance_m, overlap_threshold)` —
do two routes ride the same road?

Step 2: `corridor_groups(routes_dict, ...)` — given many routes,
return connected components (transitively grouped via pairwise
share_corridor).

Discipline: tests describe the contract; if implementation fails
a test, fix the implementation, not the assertion.
"""

from __future__ import annotations

from shapely.geometry import LineString

from gtfs_map.bundle import corridor_groups, share_corridor


def _line(*pts):
    return LineString(pts)


# ---------- B1: identical geometries share a corridor ----------------------


def test_B1_identical_lines_share_corridor():
    a = _line((-6.30, 53.30), (-6.20, 53.30))
    b = _line((-6.30, 53.30), (-6.20, 53.30))
    assert share_corridor(a, b, tolerance_m=10, overlap_threshold=0.9)


def test_B1b_identical_lines_in_opposite_direction_still_share():
    """Direction shouldn't matter for corridor identity."""
    a = _line((-6.30, 53.30), (-6.20, 53.30))
    b = _line((-6.20, 53.30), (-6.30, 53.30))  # reversed
    assert share_corridor(a, b, tolerance_m=10, overlap_threshold=0.9)


# ---------- B2: parallel-but-distinct lines don't share --------------------


def test_B2_parallel_lines_50_m_apart_do_not_share():
    """Two streets ~50 m apart (think Liffey-quay one-way pair).
    Outside tolerance, so no corridor sharing — they're separate
    streets even though they're parallel."""
    a = _line((-6.30, 53.30000), (-6.20, 53.30000))
    b = _line((-6.30, 53.30050), (-6.20, 53.30050))  # ~55 m N
    assert not share_corridor(a, b, tolerance_m=10, overlap_threshold=0.9)


def test_B2b_close_parallel_within_tolerance_does_share():
    """Same road, just GTFS sampling jitter (~5 m offset)."""
    a = _line((-6.30, 53.30000), (-6.20, 53.30000))
    b = _line((-6.30, 53.30005), (-6.20, 53.30005))  # ~5.5 m N
    assert share_corridor(a, b, tolerance_m=10, overlap_threshold=0.9)


# ---------- B3: disjoint geometries don't share ----------------------------


def test_B3_disjoint_lines_do_not_share():
    a = _line((-6.30, 53.30), (-6.20, 53.30))
    b = _line((-6.30, 53.40), (-6.20, 53.40))  # 11 km N
    assert not share_corridor(a, b, tolerance_m=10, overlap_threshold=0.9)


# ---------- B4: partial overlap depends on threshold -----------------------


def test_B4_routes_sharing_50_percent_below_default_threshold():
    """A and B share half their length, then B diverges. Default
    threshold (90% overlap) -> NOT a corridor share."""
    a = _line(
        (-6.30, 53.300), (-6.25, 53.300), (-6.20, 53.300),
    )
    b = _line(
        (-6.30, 53.30005), (-6.25, 53.30005),
        (-6.20, 53.31),  # diverges sharply north
    )
    assert not share_corridor(a, b, tolerance_m=10, overlap_threshold=0.9)


def test_B4b_routes_sharing_50_percent_pass_lower_threshold():
    """Same lines, threshold lowered to 0.4 — now a sharer."""
    a = _line(
        (-6.30, 53.300), (-6.25, 53.300), (-6.20, 53.300),
    )
    b = _line(
        (-6.30, 53.30005), (-6.25, 53.30005),
        (-6.20, 53.31),
    )
    assert share_corridor(a, b, tolerance_m=10, overlap_threshold=0.4)


# ---------- B5: short stub vs long route -----------------------------------


def test_B5_short_segment_inside_long_route_corridor_does_share():
    """A short bus route entirely within a longer route's corridor
    shares it (the short route's full length sits on the long
    route's road)."""
    long_a = _line((-6.30, 53.300), (-6.20, 53.300))   # ~6.7 km
    short_b = _line((-6.27, 53.30005), (-6.25, 53.30005))  # ~1.3 km, inside
    assert share_corridor(long_a, short_b, tolerance_m=10, overlap_threshold=0.9)


# ---------- B6-B11: corridor_groups (connected components) -----------------


def _routes(*pairs):
    """Helper: build {name: LineString} from (name, [pts...]) pairs."""
    return {name: LineString(pts) for name, pts in pairs}


def test_B6_empty_dict_yields_empty_list():
    assert corridor_groups({}) == []


def test_B7_single_route_yields_one_singleton_group():
    r = _routes(("A", [(-6.30, 53.30), (-6.20, 53.30)]))
    groups = corridor_groups(r)
    assert groups == [frozenset({"A"})]


def test_B8_three_disjoint_routes_yield_three_singleton_groups():
    r = _routes(
        ("A", [(-6.30, 53.30), (-6.20, 53.30)]),
        ("B", [(-6.30, 53.40), (-6.20, 53.40)]),  # 11 km north
        ("C", [(-6.30, 53.50), (-6.20, 53.50)]),  # 22 km north
    )
    groups = corridor_groups(r)
    assert len(groups) == 3
    assert all(len(g) == 1 for g in groups)
    assert {next(iter(g)) for g in groups} == {"A", "B", "C"}


def test_B9_three_identical_routes_yield_one_group_of_three():
    pts = [(-6.30, 53.30), (-6.20, 53.30)]
    r = _routes(("A", pts), ("B", pts), ("C", pts))
    groups = corridor_groups(r)
    assert len(groups) == 1
    assert groups[0] == frozenset({"A", "B", "C"})


def test_B10_transitive_grouping_via_chain():
    """A overlaps B and B overlaps C, but A and C do NOT directly
    overlap. Should still group all three transitively."""
    # A and B nearly overlap (within 10 m); B and C nearly overlap;
    # A and C are 20 m apart (above 10 m tolerance).
    r = _routes(
        ("A", [(-6.30, 53.30000), (-6.20, 53.30000)]),
        ("B", [(-6.30, 53.30009), (-6.20, 53.30009)]),  # ~10 m N of A
        ("C", [(-6.30, 53.30018), (-6.20, 53.30018)]),  # ~20 m N of A, ~10 m N of B
    )
    groups = corridor_groups(r, tolerance_m=11, overlap_threshold=0.9)
    assert len(groups) == 1
    assert groups[0] == frozenset({"A", "B", "C"})


def test_B11_two_clusters_stay_separate():
    """A,B share corridor; C,D share corridor; the two clusters
    are far apart -> two distinct groups."""
    r = _routes(
        ("A", [(-6.30, 53.30000), (-6.20, 53.30000)]),
        ("B", [(-6.30, 53.30005), (-6.20, 53.30005)]),
        ("C", [(-6.30, 53.40000), (-6.20, 53.40000)]),  # 11 km N
        ("D", [(-6.30, 53.40005), (-6.20, 53.40005)]),  # near C
    )
    groups = corridor_groups(r)
    assert len(groups) == 2
    sets = sorted(groups, key=lambda g: sorted(g))
    assert sets[0] == frozenset({"A", "B"})
    assert sets[1] == frozenset({"C", "D"})
