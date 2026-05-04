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


def test_category_offsets_are_assigned_per_category():
    # Spine and radial both at 0 (radial intentionally at 0 — see
    # offset.py for why). The middle categories step away from the
    # centreline so they're visible side-by-side on shared corridors.
    assert CATEGORY_OFFSET_M["spine"] == 0
    assert CATEGORY_OFFSET_M["radial"] == 0
    for cat in ("orbital", "local", "peak"):
        assert CATEGORY_OFFSET_M[cat] > 0
    # Orbital < local < peak (so the visible offsets don't collide).
    assert (
        CATEGORY_OFFSET_M["orbital"]
        < CATEGORY_OFFSET_M["local"]
        < CATEGORY_OFFSET_M["peak"]
    )


def test_two_offset_categories_render_visibly_apart():
    # Spine sits at 0; peak gets the largest offset of the offset
    # categories, so it's the cleanest pair to verify.
    line = LineString([(-6.30, 53.30), (-6.20, 53.30)])
    spine_offset = offset_line(line, CATEGORY_OFFSET_M["spine"])
    peak_offset = offset_line(line, CATEGORY_OFFSET_M["peak"])
    mid_a = spine_offset.interpolate(0.5, normalized=True)
    mid_b = peak_offset.interpolate(0.5, normalized=True)
    metres = abs(mid_a.y - mid_b.y) * 111_000
    assert metres >= CATEGORY_OFFSET_M["peak"] - 5, (
        f"peak should sit ~{CATEGORY_OFFSET_M['peak']} m from spine, got {metres:.1f} m"
    )
