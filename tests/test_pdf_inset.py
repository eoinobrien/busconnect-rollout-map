from __future__ import annotations

import pytest

from pdf_map.extract import Path as PdfPath
from pdf_map.inset import INSET_BBOXES, is_inside_inset, reject_inset_paths


def _path(cx, cy, half=5.0):
    pts = ((cx - half, cy - half), (cx + half, cy + half))
    return PdfPath(
        drawing_index=0, polyline_index=0,
        stroke=None, fill=None, width=None,
        points=pts, bbox=(pts[0][0], pts[0][1], pts[1][0], pts[1][1]),
    )


class TestIsInsideInset:
    def test_inside_north_inset(self):
        # Solidly inside the right-side strip, top half (north panel area).
        assert is_inside_inset(_path(1000, 200))

    def test_inside_south_inset(self):
        # Right strip, lower half (south panel area).
        assert is_inside_inset(_path(1000, 600))

    def test_outside_to_left(self):
        # Top-left main map territory (above the bottom inset's y range).
        assert not is_inside_inset(_path(500, 400))

    def test_inside_bottom_center_bray_inset(self):
        # Bray detail panel at (650, 519)-(953, 800).
        assert is_inside_inset(_path(800, 650))

    def test_main_map_howth_position_excluded(self):
        # Howth's main-map PDF position is around (855, 270) -
        # just inside x=875, so safe.
        assert not is_inside_inset(_path(855, 270))

    def test_killiney_in_bray_inset_now_excluded(self):
        # The schematic Killiney area at (782, 581) sits inside the
        # bottom-center Bray detail inset, so it gets filtered out -
        # which is what we want, since that's the duplicate render.
        assert is_inside_inset(_path(782, 581))

    def test_killiney_inset_position_included(self):
        # Inset Killiney is around (1033, 491).
        assert is_inside_inset(_path(1033, 491))


def test_reject_filters_only_inset_paths():
    paths = [
        _path(500, 400),    # main map (top-center)
        _path(1000, 300),   # north panel area
        _path(1000, 600),   # south panel area
        _path(800, 650),    # bottom-center Bray inset
        _path(300, 700),    # main map (bottom-left, outside Bray inset)
    ]
    out = reject_inset_paths(paths)
    assert len(out) == 2
    centroids = [(p.bbox[0] + 5, p.bbox[1] + 5) for p in out]
    assert (500, 400) in centroids
    assert (300, 700) in centroids


def test_inset_bboxes_are_within_page_bounds():
    # Every inset box must lie inside the A3 page rect.
    for x0, y0, x1, y1 in INSET_BBOXES:
        assert 0 <= x0 < x1 <= 1200
        assert 0 <= y0 < y1 <= 850


def test_off_page_path_rejected():
    from pdf_map.inset import is_off_page
    # A path drawn entirely below the page bottom (y > 842).
    p = _path(800, 950)  # centre at (800, 950) - below page
    assert is_off_page(p)


def test_on_page_path_kept():
    from pdf_map.inset import is_off_page
    p = _path(500, 400)
    assert not is_off_page(p)


def test_reject_filters_off_page_too():
    paths = [
        _path(500, 400),    # main map
        _path(800, 950),    # off-page (below)
        _path(1000, 600),   # inset
    ]
    out = reject_inset_paths(paths)
    assert len(out) == 1
    cx = (out[0].bbox[0] + out[0].bbox[2]) / 2
    assert cx == 500
