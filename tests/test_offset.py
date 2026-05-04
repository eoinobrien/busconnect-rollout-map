"""Per-category perpendicular offset to keep cross-class overlaps visible."""

import math

from shapely.geometry import LineString

from gtfs_map.offset import offset_line, CATEGORY_OFFSET_M


def test_zero_offset_returns_geometry_unchanged():
    line = LineString([(-6.30, 53.30), (-6.20, 53.30)])
    out = offset_line(line, 0.0)
    assert list(out.coords) == list(line.coords)


def test_positive_offset_shifts_a_horizontal_line_perpendicularly():
    # A west-east line near Dublin (lat 53.30). A perpendicular shift
    # of ~5 m should change latitude by roughly 5 / 111000 ≈ 4.5e-5.
    line = LineString([(-6.30, 53.30), (-6.20, 53.30)])
    out = offset_line(line, 5.0)
    out_coords = list(out.coords)
    assert len(out_coords) >= 2
    # Mean latitude should have moved by ~5 m worth of degrees
    delta = abs(sum(c[1] for c in out_coords) / len(out_coords) - 53.30)
    assert 1e-5 < delta < 2e-4, f"expected ~5 m perpendicular shift, got delta={delta}"
    # Longitudes should still span roughly the same range
    lons = [c[0] for c in out_coords]
    assert min(lons) < -6.25 and max(lons) > -6.25


def test_offset_does_not_crash_on_degenerate_input():
    # A zero-length line is shapely-invalid; the function must fall
    # back to returning the input rather than raising.
    degenerate = LineString([(-6.30, 53.30), (-6.30, 53.30)])
    out = offset_line(degenerate, 5.0)
    assert out is not None  # no exception, returns something


def test_category_offsets_are_in_priority_order():
    # Spine should not move; each lower category should move more.
    assert CATEGORY_OFFSET_M["spine"] == 0
    order = ("spine", "orbital", "local", "peak", "radial")
    distances = [CATEGORY_OFFSET_M[c] for c in order]
    # Strictly increasing
    for a, b in zip(distances, distances[1:]):
        assert b > a, f"{a} should be < {b}"


def test_two_lines_at_different_categories_end_up_visibly_apart():
    # Two parallel routes on the same road, offset by spine vs radial
    # should end up apart by spine_distance + radial_distance worth of
    # metres (or close to it, since one shifts 0 and the other shifts 16).
    line = LineString([(-6.30, 53.30), (-6.20, 53.30)])
    spine_offset = offset_line(line, CATEGORY_OFFSET_M["spine"])
    radial_offset = offset_line(line, CATEGORY_OFFSET_M["radial"])
    # Sample mid-points
    mid_a = spine_offset.interpolate(0.5, normalized=True)
    mid_b = radial_offset.interpolate(0.5, normalized=True)
    # Convert their lat-difference to metres
    metres = abs(mid_a.y - mid_b.y) * 111_000
    assert 8 <= metres <= 24, (
        f"radial should sit ~16 m from spine, got {metres:.1f} m"
    )
