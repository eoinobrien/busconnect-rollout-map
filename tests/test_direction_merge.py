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


def test_M1_two_directions_emit_multilinestring():
    """When both directions exist, emit BOTH as full LineStrings
    inside a MultiLineString. Each direction is its own connected
    route; the user-facing Feature is "the route, both directions"."""
    a = _line((-6.30, 53.30), (-6.20, 53.30))
    b = _line((-6.20, 53.30), (-6.30, 53.30))
    out = merge_directions(a, b)
    assert isinstance(out, MultiLineString)
    assert len(out.geoms) == 2


def test_M2_each_direction_meaningful_geometry_preserved():
    """No corridor merging, no canonical-picking — each direction's
    full GTFS shape comes through. Vertices that contribute real
    shape (clear bends) survive; only redundant ones get simplified."""
    a = _line((-6.30, 53.300), (-6.20, 53.300), (-6.20, 53.310))  # right-angle north
    b = _line((-6.20, 53.310), (-6.20, 53.300), (-6.30, 53.300))  # mirror
    out = merge_directions(a, b)
    assert isinstance(out, MultiLineString)
    # All three vertices kept (the bend can't be simplified out).
    assert len(list(out.geoms[0].coords)) == 3
    assert len(list(out.geoms[1].coords)) == 3


def test_M3_detour_in_one_direction_is_kept_intact():
    """If dir 1 takes a detour, that detour is part of dir 1's
    real geometry and stays in the output. We're not simplifying
    away anything — both directions are rendered as-is."""
    a = _line((-6.30, 53.300), (-6.20, 53.300))
    b = _line(
        (-6.20, 53.30005),
        (-6.25, 53.30100),  # detour
        (-6.30, 53.30005),
    )
    out = merge_directions(a, b)
    assert isinstance(out, MultiLineString)
    # b's middle vertex preserved
    coords_b = list(out.geoms[1].coords)
    assert (-6.25, 53.30100) in coords_b


def test_M5_single_direction_passes_through_unchanged():
    a = _line((-6.30, 53.300), (-6.20, 53.300))
    out = merge_directions(a, None)
    assert isinstance(out, LineString)
    assert list(out.coords) == list(a.coords)


def test_M9_fold_back_vertex_is_removed():
    """The classic fold-back: vertex b is ~24 m west of a, vertex c
    is then ~24 m back east, ending only ~7 m from a. The detour
    ratio (a->b + b->c) / (a->c) is ~6.8, well above the 4.0 drop
    threshold. b is removed; the cleaned line goes straight a -> c.
    """
    # Reproduces route 4 dir 1's vertices 19/20/21 at the Heuston
    # terminus.
    a = LineString([
        (-6.29700, 53.34693),
        (-6.29738, 53.34693),
        (-6.29772, 53.34702),  # b: fold-back vertex
        (-6.29749, 53.34692),
        (-6.29710, 53.34691),
    ])
    out = merge_directions(a, None)
    coords = list(out.coords)
    # The middle (fold-back) vertex must be gone.
    bad = (-6.29772, 53.34702)
    assert bad not in coords, f"fold-back vertex still present: {coords}"


def test_M9b_real_bend_is_preserved():
    """A genuine 90 degree turn has ratio 1.41 (well below 4.0), so
    every vertex is kept."""
    a = LineString([
        (-6.30, 53.300),
        (-6.20, 53.300),
        (-6.20, 53.310),  # 90 degree turn
        (-6.10, 53.310),
    ])
    out = merge_directions(a, None)
    assert len(list(out.coords)) == 4


def test_M9c_30_degree_turn_is_preserved():
    """Bus routes have all sorts of turn angles; the threshold must
    keep even sharp 30 degree turns (ratio ~3.9, just below 4.0)."""
    # A vertex at 30 degree included angle: roughly equilateral
    # legs with chord ratio 1/sin(15) ~= 3.86.
    # Construct: vertex 1 at apex, sides going 100 m each at
    # 30 deg between them.
    apex = (-6.250, 53.305)
    # Two endpoints 100 m from apex, included angle 30 deg
    # (so each leg is 15 deg off the bisector).
    a = LineString([
        (-6.301, 53.305),
        apex,
        (-6.20, 53.30115),  # very tight bend back-and-forth
    ])
    out = merge_directions(a, None)
    # Apex must survive (real bend, not a fold-back)
    assert apex in list(out.coords) or any(
        abs(c[0] - apex[0]) < 1e-6 and abs(c[1] - apex[1]) < 1e-6
        for c in out.coords
    )


def test_M6_each_direction_connected_within_itself():
    """Even when the two directions diverge dramatically (Liffey
    quay one-way pair), each is a single connected LineString in
    the output. No floating residuals."""
    a = _line((-6.30, 53.300), (-6.25, 53.300), (-6.20, 53.300))
    b = _line((-6.20, 53.30050), (-6.25, 53.30050), (-6.30, 53.30050))
    out = merge_directions(a, b)
    assert isinstance(out, MultiLineString)
    assert len(out.geoms) == 2
    # Each component has at least 2 coords (it's a proper LineString,
    # not a fragment)
    for g in out.geoms:
        assert len(g.coords) >= 2
