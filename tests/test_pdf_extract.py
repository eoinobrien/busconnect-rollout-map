"""Smoke-tests against the real Big Picture PDF.

These are integration-style: they run only when the source PDF has
been downloaded into `data/pdf/` (the repo's standing convention for
input artefacts). They guard against regressions in the pymupdf
plumbing — counts shouldn't change wildly across pymupdf upgrades —
and assert that key route-line and landmark text spans actually
exist where downstream stages expect them.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pdf_map.extract import extract_paths, extract_text_spans


PDF_PATH = Path(__file__).resolve().parents[1] / "data" / "pdf" / "big-picture-2025-10-02.pdf"


pytestmark = pytest.mark.skipif(
    not PDF_PATH.exists(),
    reason=f"source PDF not present at {PDF_PATH}",
)


def test_paths_are_extracted_in_bulk():
    paths = extract_paths(PDF_PATH)
    # Real PDF has ~107k drawings; allowing for filter/flatten the
    # output is still many tens of thousands.
    assert len(paths) > 5_000


def test_route_line_candidate_paths_exist():
    paths = extract_paths(PDF_PATH)
    # BusConnects red @ width 0.4 are spine route lines (~95 with
    # diag > 50 from earlier inspection). Looser predicate here
    # avoids over-fitting to one decade of stroke data.
    busconnects_red = (0.9304, 0.1109, 0.1415)

    def near(a, b, tol=0.005):
        return a is not None and all(abs(a[i] - b[i]) < tol for i in range(3))

    candidates = [p for p in paths if near(p.stroke, busconnects_red) and p.diag > 50]
    assert len(candidates) > 30


def test_text_spans_include_known_landmarks_and_shields():
    spans = extract_text_spans(PDF_PATH)
    texts = {s.text.upper() for s in spans}
    # A few park labels we plan to use as georef control points.
    assert "PHOENIX" in texts
    assert "TYMON PARK" in texts
    # A few route shields across categories (spine, orbital, local).
    assert "A1" in texts
    assert "N4" in texts
    assert "L25" in texts or "L26" in texts
