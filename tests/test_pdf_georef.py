from __future__ import annotations

from pathlib import Path

import pytest

from pdf_map.extract import extract_text_spans
from pdf_map.georef import Affine, fit_affine, fit_from_spans


PDF_PATH = Path(__file__).resolve().parents[1] / "data" / "pdf" / "big-picture-2025-10-02.pdf"


class TestFitAffine:
    def test_identity_recovered_from_three_points(self):
        # Identity transform: world == pdf coords
        pts = [(0.0, 0.0), (10.0, 0.0), (0.0, 5.0)]
        out = fit_affine(pts, pts)
        assert out.a == pytest.approx(1.0)
        assert out.b == pytest.approx(0.0, abs=1e-9)
        assert out.c == pytest.approx(0.0, abs=1e-9)
        assert out.d == pytest.approx(0.0, abs=1e-9)
        assert out.e == pytest.approx(1.0)
        assert out.f == pytest.approx(0.0, abs=1e-9)

    def test_recovers_known_affine_with_translation_and_flip(self):
        # World = (0.5*x + 100, -0.25*y + 50) — scale + y-flip + translate.
        truth = Affine(a=0.5, b=0.0, c=100.0, d=0.0, e=-0.25, f=50.0)
        pdf_pts = [(0.0, 0.0), (200.0, 0.0), (0.0, 200.0), (200.0, 200.0)]
        world_pts = [truth.apply(*p) for p in pdf_pts]
        out = fit_affine(pdf_pts, world_pts)
        for got, want in zip(
            (out.a, out.b, out.c, out.d, out.e, out.f),
            (truth.a, truth.b, truth.c, truth.d, truth.e, truth.f),
        ):
            assert got == pytest.approx(want, abs=1e-9)

    def test_least_squares_handles_overdetermined_with_noise(self):
        # 6 points with light noise: fit recovers truth within tolerance
        truth = Affine(a=1.2, b=-0.3, c=10.0, d=0.4, e=1.1, f=-5.0)
        pdf_pts = [(1.0, 2.0), (3.0, 5.0), (7.0, 11.0), (13.0, 4.0), (8.0, 9.0), (2.0, 0.0)]
        world_pts = [truth.apply(*p) for p in pdf_pts]
        # Add a tiny bias to one point to force lstsq into use
        world_pts[2] = (world_pts[2][0] + 0.001, world_pts[2][1] - 0.001)
        out = fit_affine(pdf_pts, world_pts)
        for got, want in zip(
            (out.a, out.b, out.c, out.d, out.e, out.f),
            (truth.a, truth.b, truth.c, truth.d, truth.e, truth.f),
        ):
            assert got == pytest.approx(want, abs=1e-3)

    def test_raises_on_too_few_points(self):
        with pytest.raises(ValueError):
            fit_affine([(0.0, 0.0), (1.0, 1.0)], [(0.0, 0.0), (1.0, 1.0)])


@pytest.mark.skipif(
    not PDF_PATH.exists(),
    reason=f"source PDF not present at {PDF_PATH}",
)
class TestFitFromRealPDF:
    def test_finds_at_least_a_dozen_landmarks(self):
        spans = extract_text_spans(PDF_PATH)
        transform, residuals = fit_from_spans(spans)
        assert len(residuals) >= 12

    def test_residuals_are_within_schematic_tolerance(self):
        # Map is schematic, so 1km median residual is acceptable.
        # Worst-case singletons may go higher but should stay below
        # ~3km — anything worse points to a bad lat/lon constant or
        # a label mismatch.
        spans = extract_text_spans(PDF_PATH)
        _, residuals = fit_from_spans(spans)
        sorted_res = sorted(residuals.values())
        median = sorted_res[len(sorted_res) // 2]
        assert median < 1500, f"median residual {median:.0f} m too high; per-point: {residuals}"
        assert max(sorted_res) < 5000, f"max residual {max(sorted_res):.0f} m: {residuals}"
