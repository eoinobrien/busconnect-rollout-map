"""Segment-level bundling primitives.

Step 1: `segment_route_by_others(name, line, others, tolerance_m)` —
walk one route's line and split it whenever the set of *other*
routes within `tolerance_m` changes. Each sub-segment is returned
with its membership frozenset (always includes `name`).

Discipline: tests describe the contract; if implementation fails a
test, fix the implementation, not the assertion.
"""

from __future__ import annotations

from shapely.geometry import LineString

from gtfs_map.segment_bundle import segment_bundle, segment_route_by_others


def _line(*pts):
    return LineString(pts)


# ---------- S1: no others -> single segment with self set ------------------


def test_S1_no_others_yields_single_self_segment():
    a = _line((-6.30, 53.30), (-6.20, 53.30))
    out = segment_route_by_others("A", a, {}, tolerance_m=10)
    assert len(out) == 1
    seg, members, walker = out[0]
    assert members == frozenset({"A"})
    assert walker == "A"
    coords = list(seg.coords)
    assert coords[0] == (-6.30, 53.30)
    assert coords[-1] == (-6.20, 53.30)


# ---------- S2: distant others don't affect membership --------------------


def test_S2_distant_other_does_not_affect_membership():
    a = _line((-6.30, 53.30), (-6.20, 53.30))
    b = _line((-6.30, 53.40), (-6.20, 53.40))  # 11 km north
    out = segment_route_by_others("A", a, {"B": b}, tolerance_m=10)
    assert len(out) == 1
    assert out[0][1] == frozenset({"A"})


# ---------- S3: a route fully within tolerance bumps the whole set --------


def test_S3_fully_overlapping_other_appears_in_full_set():
    a = _line((-6.30, 53.30000), (-6.20, 53.30000))
    b = _line((-6.30, 53.30005), (-6.20, 53.30005))  # ~5.5 m north, full overlap
    out = segment_route_by_others("A", a, {"B": b}, tolerance_m=10)
    assert len(out) == 1
    assert out[0][1] == frozenset({"A", "B"})


# ---------- S4: partial overlap splits the route into 3 sub-segments ------


def test_S4_partial_overlap_splits_into_three_segments():
    """Route A runs 0..30 km east. Route B runs along the middle
    third only. A's walk should yield three sub-segments:
      [0..10]   {A}
      [10..20]  {A, B}
      [20..30]  {A}
    """
    a = _line((-6.30, 53.30000), (-6.20, 53.30000))   # ~6.7 km
    # B covers the middle ~third of A, within tolerance
    b = _line((-6.27, 53.30005), (-6.23, 53.30005))   # ~2.7 km, middle
    out = segment_route_by_others("A", a, {"B": b}, tolerance_m=10)
    sets = [s for _, s, _ in out]
    assert sets == [
        frozenset({"A"}),
        frozenset({"A", "B"}),
        frozenset({"A"}),
    ], f"got {sets}"


# ---------- S5: two others on different halves of A -----------------------


def test_S5_two_others_on_different_halves_yield_two_distinct_sets():
    """A spans 0..30 km. B is along the western half (0..15 km),
    C is along the eastern half (15..30 km). A's walk:
      [0..15]   {A, B}
      [15..30]  {A, C}
    """
    a = _line((-6.30, 53.30000), (-6.20, 53.30000))
    b = _line((-6.30, 53.30005), (-6.25, 53.30005))   # west half
    c = _line((-6.25, 53.30005), (-6.20, 53.30005))   # east half
    out = segment_route_by_others("A", a, {"B": b, "C": c}, tolerance_m=10)
    sets = [s for _, s, _ in out]
    assert sets == [
        frozenset({"A", "B"}),
        frozenset({"A", "C"}),
    ], f"got {sets}"


# ---------- S6: sub-segments concatenate back to the original geometry ----


def test_S6_segments_cover_full_input_line_with_no_gaps():
    """The union of returned sub-segments (in order) should retrace
    the input line from start to end with no gaps and no overlaps.
    Endpoints of consecutive segments must coincide."""
    a = _line((-6.30, 53.30000), (-6.20, 53.30000))
    b = _line((-6.27, 53.30005), (-6.23, 53.30005))
    out = segment_route_by_others("A", a, {"B": b}, tolerance_m=10)
    # Endpoints must chain: segment[i].end == segment[i+1].start
    for i in range(len(out) - 1):
        end = list(out[i][0].coords)[-1]
        start = list(out[i + 1][0].coords)[0]
        # Compare with small tolerance for float drift.
        assert abs(end[0] - start[0]) < 1e-9
        assert abs(end[1] - start[1]) < 1e-9
    # First point matches A's start, last point matches A's end.
    assert list(out[0][0].coords)[0] == (-6.30, 53.30000)
    assert list(out[-1][0].coords)[-1] == (-6.20, 53.30000)


# ---------- T1-T6: segment_bundle (across many routes, dedup) -------------


def _routes(*pairs):
    return {name: LineString(pts) for name, pts in pairs}


def test_T1_empty_input_yields_empty_output():
    assert segment_bundle({}) == []


def test_T2_single_route_yields_single_self_segment():
    r = _routes(("A", [(-6.30, 53.30), (-6.20, 53.30)]))
    out = segment_bundle(r)
    assert len(out) == 1
    assert out[0][1] == frozenset({"A"})


def test_T3_two_distant_routes_yield_two_singleton_segments():
    r = _routes(
        ("A", [(-6.30, 53.30), (-6.20, 53.30)]),
        ("B", [(-6.30, 53.40), (-6.20, 53.40)]),  # 11 km north
    )
    out = segment_bundle(r)
    assert len(out) == 2
    sets = sorted([m for _, m, _ in out], key=lambda s: sorted(s))
    assert sets == [frozenset({"A"}), frozenset({"B"})]


def test_T4_two_fully_overlapping_routes_emit_per_route_shared_segments():
    """A and B run the exact same road end-to-end. Each route's
    walk emits a {A, B} segment using its OWN geometry, so the
    output has two overlapping shared segments (one per member).
    Geometry-per-route preserves continuity even though it duplicates
    visually-coincident corridors."""
    r = _routes(
        ("A", [(-6.30, 53.30000), (-6.20, 53.30000)]),
        ("B", [(-6.30, 53.30005), (-6.20, 53.30005)]),
    )
    out = segment_bundle(r, tolerance_m=10)
    assert len(out) == 2
    sets = [m for _, m, _ in out]
    assert sets == [frozenset({"A", "B"}), frozenset({"A", "B"})]


def test_T5_partial_overlap_emits_per_route_segments():
    """A from x=0..L, B from x=L/2..1.5L (50% overlap with A's east).
    Each route's walk produces its own segments along its own
    geometry, so the shared {A, B} corridor appears twice — once on
    A's geometry, once on B's. Routes stay continuous.
    """
    r = _routes(
        ("A", [(-6.30, 53.30000), (-6.20, 53.30000)]),
        ("B", [(-6.25, 53.30005), (-6.15, 53.30005)]),
    )
    out = segment_bundle(r, tolerance_m=10)
    member_sets = sorted([m for _, m, _ in out], key=lambda s: (len(s), sorted(s)))
    # A's walk: [A] + [A, B]; B's walk: [A, B] + [B]; total 4
    assert member_sets == [
        frozenset({"A"}),
        frozenset({"B"}),
        frozenset({"A", "B"}),
        frozenset({"A", "B"}),
    ]


def test_T6_three_route_partial_overlap_per_route_segments():
    """A, B, C with pairwise overlap. Each route walks its own
    geometry; shared corridors appear once per member."""
    r = _routes(
        ("A", [(-6.30, 53.30000), (-6.10, 53.30000)]),
        ("B", [(-6.20, 53.30005), (-6.05, 53.30005)]),
        ("C", [(-6.30, 53.40000), (-6.10, 53.40000)]),  # 11km N
    )
    out = segment_bundle(r, tolerance_m=10)
    member_sets = [m for _, m, _ in out]
    # A's walk: [A] + [A, B]; B's walk: [A, B] + [B]; C's walk: [C]
    assert frozenset({"A"}) in member_sets
    assert frozenset({"A", "B"}) in member_sets
    assert frozenset({"B"}) in member_sets
    assert frozenset({"C"}) in member_sets
    # Shared {A,B} segment appears twice — once from each member's walk.
    assert sum(1 for s in member_sets if s == frozenset({"A", "B"})) == 2
