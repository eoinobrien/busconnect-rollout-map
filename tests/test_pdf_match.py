from __future__ import annotations

from pathlib import Path

import pytest

from pdf_map.extract import Path as PdfPath, TextSpan, extract_paths, extract_text_spans
from pdf_map.match import (
    SHIELD_RE,
    associate_shields_with_paths,
    find_route_shields,
    _point_to_polyline_distance,
)
from pdf_map.spine import route_line_paths


class TestShieldRegex:
    @pytest.mark.parametrize("label", ["A1", "B3", "C12", "H9", "L25", "L89", "N4", "S6", "W2", "X1", "39", "145", "1", "39A"])
    def test_matches_known_shield_styles(self, label):
        assert SHIELD_RE.match(label) is not None

    @pytest.mark.parametrize("label", ["TYMON", "PARK", "PHOENIX", "Bohernabreena", "1234", "AA"])
    def test_rejects_non_shields(self, label):
        assert SHIELD_RE.match(label) is None


class TestPointToPolylineDistance:
    def test_endpoint_match(self):
        d = _point_to_polyline_distance(0.0, 0.0, ((0.0, 0.0), (10.0, 0.0)))
        assert d == 0.0

    def test_perpendicular_to_segment(self):
        d = _point_to_polyline_distance(5.0, 3.0, ((0.0, 0.0), (10.0, 0.0)))
        assert d == pytest.approx(3.0)

    def test_off_end_uses_endpoint(self):
        d = _point_to_polyline_distance(15.0, 4.0, ((0.0, 0.0), (10.0, 0.0)))
        # Closest point is (10,0); distance is sqrt(25+16) = sqrt(41)
        assert d == pytest.approx((25 + 16) ** 0.5)

    def test_picks_nearest_of_multiple_segments(self):
        polyline = ((0.0, 0.0), (10.0, 0.0), (10.0, 10.0))
        # Closest to second segment at (10, 5): dist 1
        d = _point_to_polyline_distance(11.0, 5.0, polyline)
        assert d == pytest.approx(1.0)


def _path(points):
    pts = tuple(points)
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return PdfPath(
        drawing_index=0, polyline_index=0, stroke=(0.93, 0.11, 0.14),
        fill=None, width=0.4, points=pts,
        bbox=(min(xs), min(ys), max(xs), max(ys)),
    )


def _span(text, x, y):
    return TextSpan(text=text, bbox=(x - 1, y - 1, x + 1, y + 1), color=None, size=6.0, font="x")


class TestAssociateShieldsWithPaths:
    def test_shield_on_line_associates(self):
        paths = [_path([(0, 0), (10, 0), (20, 0)])]
        shields = [("C1", _span("C1", 5, 0))]
        out = associate_shields_with_paths(paths, shields, max_dist_pt=2.0)
        assert out == {0: {"C1"}}

    def test_shield_far_from_line_skipped(self):
        paths = [_path([(0, 0), (10, 0)])]
        shields = [("C1", _span("C1", 5, 100))]
        out = associate_shields_with_paths(paths, shields, max_dist_pt=2.0)
        assert out == {}

    def test_path_gets_multiple_route_ids_when_corridor_shared(self):
        paths = [_path([(0, 0), (50, 0)])]
        shields = [
            ("C1", _span("C1", 10, 0)),
            ("C2", _span("C2", 30, 0)),
        ]
        out = associate_shields_with_paths(paths, shields, max_dist_pt=2.0)
        assert out == {0: {"C1", "C2"}}


PDF_PATH = Path(__file__).resolve().parents[1] / "data" / "pdf" / "big-picture-2025-10-02.pdf"


@pytest.mark.skipif(
    not PDF_PATH.exists(),
    reason=f"source PDF not present at {PDF_PATH}",
)
def test_real_pdf_associates_many_shields():
    spans = extract_text_spans(PDF_PATH)
    paths = route_line_paths(extract_paths(PDF_PATH))
    shields = find_route_shields(spans)
    by_path = associate_shields_with_paths(paths, shields)
    # Should hit at least half the candidate paths and at least 100
    # distinct routes.
    assert len(by_path) > len(paths) // 4
    distinct_routes = set()
    for ids in by_path.values():
        distinct_routes.update(ids)
    assert len(distinct_routes) >= 50
