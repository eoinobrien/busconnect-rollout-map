from __future__ import annotations

import math

import pytest
import fitz

from pdf_map.geometry import sample_cubic_bezier, drawing_to_polylines


def _pt(x, y):
    return fitz.Point(x, y)


class TestSampleCubicBezier:
    def test_endpoints_are_exact(self):
        pts = sample_cubic_bezier(_pt(0, 0), _pt(1, 2), _pt(3, 2), _pt(4, 0), steps=8)
        assert pts[0] == pytest.approx((0.0, 0.0))
        assert pts[-1] == pytest.approx((4.0, 0.0))

    def test_returns_steps_plus_one_points(self):
        pts = sample_cubic_bezier(_pt(0, 0), _pt(0, 1), _pt(1, 1), _pt(1, 0), steps=10)
        assert len(pts) == 11

    def test_straight_line_collapses_to_line(self):
        # Control points colinear on the segment → samples lie on it.
        pts = sample_cubic_bezier(_pt(0, 0), _pt(1, 1), _pt(2, 2), _pt(3, 3), steps=6)
        for x, y in pts:
            assert x == pytest.approx(y)

    def test_midpoint_of_symmetric_curve(self):
        # Symmetric cubic — midpoint should sit on x = (P0.x + P3.x)/2.
        pts = sample_cubic_bezier(_pt(0, 0), _pt(0, 1), _pt(1, 1), _pt(1, 0), steps=2)
        mid = pts[1]
        assert mid[0] == pytest.approx(0.5)


class TestDrawingToPolylines:
    def test_pure_line_chain_is_one_polyline(self):
        d = {
            "items": [
                ("l", _pt(0, 0), _pt(1, 0)),
                ("l", _pt(1, 0), _pt(2, 0)),
                ("l", _pt(2, 0), _pt(2, 1)),
            ],
            "closePath": False,
        }
        lines = drawing_to_polylines(d, bezier_steps=4)
        assert len(lines) == 1
        assert lines[0] == [(0.0, 0.0), (1.0, 0.0), (2.0, 0.0), (2.0, 1.0)]

    def test_disjoint_segments_split_into_separate_polylines(self):
        # Second segment doesn't continue from the first endpoint.
        d = {
            "items": [
                ("l", _pt(0, 0), _pt(1, 0)),
                ("l", _pt(5, 5), _pt(6, 5)),
            ],
            "closePath": False,
        }
        lines = drawing_to_polylines(d, bezier_steps=4)
        assert len(lines) == 2
        assert lines[0] == [(0.0, 0.0), (1.0, 0.0)]
        assert lines[1] == [(5.0, 5.0), (6.0, 5.0)]

    def test_curve_appends_to_chain_via_endpoint(self):
        d = {
            "items": [
                ("l", _pt(0, 0), _pt(1, 0)),
                ("c", _pt(1, 0), _pt(1, 1), _pt(2, 1), _pt(2, 0)),
            ],
            "closePath": False,
        }
        lines = drawing_to_polylines(d, bezier_steps=4)
        assert len(lines) == 1
        # First vertex from line, last from curve endpoint.
        assert lines[0][0] == (0.0, 0.0)
        assert lines[0][-1] == pytest.approx((2.0, 0.0))
        # Bezier should contribute interior points between (1,0) and (2,0).
        assert len(lines[0]) > 3

    def test_close_path_appends_back_to_start(self):
        d = {
            "items": [
                ("l", _pt(0, 0), _pt(1, 0)),
                ("l", _pt(1, 0), _pt(1, 1)),
                ("l", _pt(1, 1), _pt(0, 1)),
            ],
            "closePath": True,
        }
        lines = drawing_to_polylines(d, bezier_steps=4)
        assert len(lines) == 1
        assert lines[0][-1] == (0.0, 0.0)

    def test_rect_item_becomes_closed_polyline(self):
        rect = fitz.Rect(0, 0, 2, 1)
        d = {
            "items": [("re", rect)],
            "closePath": False,
        }
        lines = drawing_to_polylines(d, bezier_steps=4)
        assert len(lines) == 1
        # Four corners + back to start.
        assert len(lines[0]) == 5
        assert lines[0][0] == lines[0][-1]
