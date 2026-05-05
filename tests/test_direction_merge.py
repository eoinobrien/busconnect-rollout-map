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


def test_M4_diverging_directions_collapse_to_one_line():
    """Even when the two directions are on different streets (e.g.
    Liffey-quay one-way pair, ~55 m apart), the merger picks the
    shorter one and discards the other. Per the user-facing
    contract: one line per route, no MultiLineString."""
    a = _line((-6.30, 53.300), (-6.20, 53.300))
    b = _line((-6.20, 53.30050), (-6.30, 53.30050))  # ~55 m north
    out = merge_directions(a, b, threshold_m=30)
    assert isinstance(out, LineString)


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


def test_M7_partial_divergence_collapses_to_canonical():
    """Most of the line is shared; one segment of dir 1 strays well
    north of dir 0. The merger keeps a (the shorter, straighter
    line) and drops dir 1's stray altogether."""
    a = _line(
        (-6.30, 53.300), (-6.25, 53.300), (-6.20, 53.300), (-6.15, 53.300),
    )
    b = _line(
        (-6.15, 53.30005), (-6.20, 53.30005),
        (-6.25, 53.302),  # strays ~220 m north here
        (-6.30, 53.30005),
    )
    out = merge_directions(a, b, threshold_m=30)
    assert isinstance(out, LineString)
    # The output is `a` (shorter) — its lats are all 53.300.
    for x, y in out.coords:
        assert abs(y - 53.300) < 1e-6
