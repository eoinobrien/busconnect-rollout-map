"""Slow integration tests against the actual built output.

These tests assume `python build.py` has been run and that
output/segments.geojson reflects current code. They make hard
assertions about real-data quality so we can prove a fix works
instead of just claiming it.

Run with:
    python -m pytest tests/test_real_data.py -v -s

Skipped automatically if output/ doesn't exist.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

OUTPUT = Path(__file__).resolve().parents[1] / "output" / "segments.geojson"
GTFS = Path(__file__).resolve().parents[1] / "data" / "gtfs"

if not OUTPUT.exists() or not GTFS.exists():
    pytest.skip(
        "real-data test skipped (no built output). Run `python build.py`.",
        allow_module_level=True,
    )

# Import the diagnose helper from the script next to the project root.
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from diagnose_jaggedness import diagnose, load_original_shapes  # noqa: E402


# Cache the diagnosis so multiple tests don't recompute it.
@pytest.fixture(scope="module")
def diagnosis():
    return diagnose(OUTPUT, GTFS, sample_step_m=25.0, deviation_threshold_m=30.0)


def test_off_route_sample_rate_under_one_percent(diagnosis):
    """No more than 1% of sampled output points should sit >30 m from
    any of the routes attributed to that segment. The 25 m bundle
    tolerance produced 4%; we expect a tighter tolerance to drop it
    below 1%.
    """
    rate = diagnosis["bad_samples"] / diagnosis["total_samples"]
    assert rate < 0.01, (
        f"{diagnosis['bad_samples']} of {diagnosis['total_samples']} samples "
        f"({rate*100:.2f}%) sit off-route. The bundle is attributing routes "
        f"to corridors they don't physically traverse."
    )


def test_no_sample_more_than_300_m_off_route():
    """The worst sample shouldn't sit 1+ km off any of its routes' true
    shapes — that's geometry the bundle invented out of thin air."""
    import json
    import pyproj
    from shapely.geometry import shape, MultiLineString, LineString, Point

    to_itm = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:2157", always_xy=True)
    originals = load_original_shapes(GTFS)
    segs = json.load(open(OUTPUT))

    worst = 0.0
    worst_route = None

    for f in segs["features"]:
        rs = f["properties"].get("route_set", [])
        if not rs:
            continue
        from shapely.ops import transform as _t
        feat_geom = _t(lambda x, y, z=None: to_itm.transform(x, y), shape(f["geometry"]))
        components = (
            list(feat_geom.geoms)
            if isinstance(feat_geom, MultiLineString)
            else [feat_geom]
        )
        for line in components:
            length = line.length
            n = max(2, int(length / 25.0) + 1)
            for i in range(n):
                pt = line.interpolate(length * i / max(1, n - 1))
                best = float("inf")
                best_r = None
                for r in rs:
                    orig = originals.get(r)
                    if orig is None:
                        continue
                    d = pt.distance(orig)
                    if d < best:
                        best = d
                        best_r = r
                if best > worst:
                    worst = best
                    worst_route = best_r

    assert worst < 300.0, (
        f"Worst sample is {worst:.0f} m off route {worst_route} — "
        f"the bundle has snapped this route's geometry far from where "
        f"its actual GTFS shape goes."
    )
