from __future__ import annotations

from pathlib import Path

import pytest

from pdf_map.extract import Path as PdfPath, extract_paths
from pdf_map.spine import (
    ROUTE_LINE_PALETTE,
    is_route_line,
    route_line_paths,
)


def _path(stroke, width, diag, *, points=((0.0, 0.0), (1.0, 1.0))):
    # Synthesize bbox so .diag matches the requested value
    pts = ((0.0, 0.0), (diag / (2 ** 0.5), diag / (2 ** 0.5)))
    return PdfPath(
        drawing_index=0, polyline_index=0,
        stroke=stroke, fill=None, width=width,
        points=pts,
        bbox=(0.0, 0.0, pts[1][0], pts[1][1]),
    )


class TestIsRouteLine:
    def test_red_at_typical_width_passes(self):
        red = next(iter(c for c in ROUTE_LINE_PALETTE if c[0] > 0.9 and c[2] < 0.2))
        assert is_route_line(_path(red, 0.40, 80))

    def test_orange_at_thin_stroke_still_passes(self):
        orange = (0.981, 0.65, 0.1)
        assert is_route_line(_path(orange, 0.20, 100))

    def test_off_palette_color_rejected(self):
        gray = (0.577, 0.586, 0.596)
        assert not is_route_line(_path(gray, 0.4, 80))

    def test_short_path_rejected_even_if_palette(self):
        red = next(iter(c for c in ROUTE_LINE_PALETTE if c[0] > 0.9 and c[2] < 0.2))
        assert not is_route_line(_path(red, 0.40, 10))

    def test_hairline_rejected(self):
        red = next(iter(c for c in ROUTE_LINE_PALETTE if c[0] > 0.9 and c[2] < 0.2))
        assert not is_route_line(_path(red, 0.05, 80))


PDF_PATH = Path(__file__).resolve().parents[1] / "data" / "pdf" / "big-picture-2025-10-02.pdf"


@pytest.mark.skipif(
    not PDF_PATH.exists(),
    reason=f"source PDF not present at {PDF_PATH}",
)
def test_real_pdf_yields_diverse_route_palette():
    paths = extract_paths(PDF_PATH)
    candidates = route_line_paths(paths)
    # Should have at least the five core spine colours represented.
    distinct_colors = {p.stroke for p in candidates}
    assert len(candidates) > 200
    assert len(distinct_colors) >= 5
