"""Phase 1 — direction merge with curve simplification.

Scenarios:
  M1 identical directions collapse to one line
  M2 close-parallel directions collapse to the simpler one
  M3 one direction detours (curves) while the other is straight ->
     merged result is the straight one
  M4 truly divergent directions stay as separate components
     (MultiLineString output)
  M5 single direction passes through unchanged
  M6 a loop route in both directions -> single loop in output

The merger operates on two LineStrings already projected to ITM
(metres); the pipeline-level integration test verifies output
shape from the GTFS-level fixture.
"""

from __future__ import annotations

import math

import pyproj
from shapely.geometry import LineString, MultiLineString

from gtfs_map.direction_merge import merge_directions


_TO_ITM = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:2157", always_xy=True)


def _wgs_to_itm(line: LineString) -> LineString:
    return LineString([_TO_ITM.transform(x, y) for x, y in line.coords])


# Long enough that residuals can survive the min-residual filter.
def _line(*pts):
    return LineString(pts)


def test_M1_identical_directions_yield_one_line():
    a = _line((-6.30, 53.30), (-6.20, 53.30))
    b = _line((-6.30, 53.30), (-6.20, 53.30))
    out = merge_directions(a, b, threshold_m=30)
    assert isinstance(out, LineString)


def test_M2_close_parallel_directions_yield_one_line():
    """Dir 1 runs ~6 m north of dir 0 (within 30 m corridor) — both
    represent the same road, so merge to one line."""
    a = _line((-6.30, 53.300), (-6.20, 53.300))
    b = _line((-6.20, 53.30005), (-6.30, 53.30005))  # reverse direction, ~5 m N
    out = merge_directions(a, b, threshold_m=30)
    assert isinstance(out, LineString)


def test_M3_detour_curve_simplified_to_straight_when_other_is_straight():
    """Dir 0 goes straight east. Dir 1 goes east but takes a small
    100 m detour north (still within 30 m corridor for most of its
    length). The merger should keep dir 0's straight geometry —
    the curve disappears."""
    a = _line(
        (-6.30, 53.300),
        (-6.25, 53.300),
        (-6.20, 53.300),
    )
    # b mostly tracks a but bumps north by ~10 m at midpoint.
    b = _line(
        (-6.20, 53.30005),
        (-6.25, 53.30015),  # detour vertex ~17 m N of a's midpoint
        (-6.30, 53.30005),
    )
    out = merge_directions(a, b, threshold_m=30)
    assert isinstance(out, LineString), f"expected LineString, got {type(out)}"
    # Result must follow a's straight geometry (lat 53.300 ± a tiny
    # epsilon for projection round-trip), not the detour midpoint.
    coords = list(out.coords)
    for x, y in coords:
        assert abs(y - 53.300) < 0.001, (
            f"merged line strayed to lat {y}, expected 53.300 ± epsilon"
        )


def test_M4_substantial_divergence_yields_multi_line_string():
    """Liffey-quay-style one-way pair: dir 0 ~10 km east on one quay,
    dir 1 ~10 km east on another quay 55 m north. The full 10 km
    residual is far above the 500 m drop threshold, so output keeps
    both legs as a MultiLineString with 2 components."""
    a = _line((-6.30, 53.300), (-6.20, 53.300))         # ~6.7 km east
    b = _line((-6.20, 53.30050), (-6.30, 53.30050))     # ~55 m north, ~6.7 km
    out = merge_directions(a, b, threshold_m=30)
    assert isinstance(out, MultiLineString)
    assert len(out.geoms) == 2


def test_M5_single_direction_passes_through_unchanged():
    a = _line((-6.30, 53.300), (-6.20, 53.300))
    out = merge_directions(a, None, threshold_m=30)
    assert isinstance(out, LineString)
    assert list(out.coords) == list(a.coords)


def test_M6_loop_route_both_directions_merges_to_one_loop():
    """A loop traversed in both directions (start == end). Both
    sample identically; merger collapses to one loop."""
    loop = _line(
        (-6.30, 53.30), (-6.28, 53.32), (-6.26, 53.32),
        (-6.26, 53.30), (-6.30, 53.30),
    )
    rev = _line(
        (-6.30, 53.30), (-6.26, 53.30), (-6.26, 53.32),
        (-6.28, 53.32), (-6.30, 53.30),
    )
    out = merge_directions(loop, rev, threshold_m=30)
    assert isinstance(out, LineString)
    coords = list(out.coords)
    assert coords[0] == coords[-1]


def test_M7_short_stray_below_threshold_is_dropped():
    """Most of the line is shared; dir 1 has a brief east-west detour
    (~200 m of geometry outside the 30 m corridor). The stray's
    total residual length is well below the 500 m drop threshold,
    so it's treated as a wobble and discarded.
    """
    a = _line(
        (-6.30, 53.300), (-6.20, 53.300),
    )
    # Brief bump at -6.265 to -6.262 going ~50 m north then back to
    # corridor. Residual segment is ~200 m east-west, below 500 m.
    b = _line(
        (-6.30, 53.30005),
        (-6.265, 53.30005),
        (-6.2635, 53.30050),  # ~50 m north
        (-6.262, 53.30005),
        (-6.20, 53.30005),
    )
    out = merge_directions(a, b, threshold_m=30)
    assert isinstance(out, LineString)


def test_M8_long_substantial_stray_above_threshold_kept_as_residual():
    """Dir 1 has a ~3 km stray that's well above the 500 m residual
    threshold — it's a real divergent leg, not a wobble. Keep it as
    a MultiLineString component."""
    a = _line(
        (-6.30, 53.300), (-6.20, 53.300), (-6.10, 53.300), (-6.00, 53.300),
    )
    b = _line(
        (-6.00, 53.30005), (-6.10, 53.30005),
        (-6.15, 53.310),  # ~1.1 km north, then continues east
        (-6.20, 53.310),  # stays ~1.1 km north for a stretch
        (-6.20, 53.30005),
        (-6.30, 53.30005),
    )
    out = merge_directions(a, b, threshold_m=30)
    assert isinstance(out, MultiLineString)
    # Canonical + at least one substantial residual
    assert len(out.geoms) >= 2
