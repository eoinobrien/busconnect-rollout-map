"""Bundling primitives.

Step 1: a single pure function `share_corridor(a, b, tolerance_m,
overlap_threshold)` that answers "do these two routes ride mostly
the same road?". Pure geometry, no pipeline integration yet.

Discipline: tests describe the contract; if implementation fails
a test, fix the implementation, not the assertion.
"""

from __future__ import annotations

from shapely.geometry import LineString

from gtfs_map.bundle import share_corridor


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
