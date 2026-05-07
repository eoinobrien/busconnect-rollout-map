"""Orchestrator: PDF -> per-path GeoJSON LineStrings.

Stitches the extraction, georeferencing, palette filter, and shield
matcher into a single end-to-end build. Each surviving polyline
becomes one Feature with:

    properties.routes      list of route_ids whose shields sit on it
    properties.route       a single representative id (first sorted)
    properties.category    gtfs_map.category.categorise() of `route`
    properties.colour      category colour from the existing palette
    properties.source      always "pdf-future" - lets the front-end
                           render these differently from live GTFS
    properties.phase       always "future"
    properties.pdf_stroke  raw PDF stroke RGB (debug / QA aid)

Untagged paths (no shield within range) still emit features so
shared-corridor segments aren't lost; their `routes` is empty and
`route` is None. The user can manually fix these post-export.
"""

from __future__ import annotations

import json
from pathlib import Path as _FsPath

from gtfs_map.category import categorise, category_colour

from .extract import extract_paths, extract_text_spans
from .georef import Affine, fit_from_spans
from .match import associate_shields_with_paths, find_route_shields
from .spine import route_line_paths


def build_future_routes(
    pdf_path: _FsPath | str,
) -> tuple[dict, dict]:
    """Return (geojson, meta).

    `geojson` is a FeatureCollection of LineStrings shaped like the
    GTFS pipeline's `routes.geojson`. `meta` is a small summary so
    the CLI can print build stats without re-walking the data.
    """
    pdf_path = _FsPath(pdf_path)

    paths = extract_paths(pdf_path)
    spans = extract_text_spans(pdf_path)
    transform, residuals = fit_from_spans(spans)

    candidates = route_line_paths(paths)
    shields = find_route_shields(spans)
    by_path = associate_shields_with_paths(candidates, shields)

    features: list[dict] = []
    for pi, path in enumerate(candidates):
        coords = [list(transform.apply(x, y)) for x, y in path.points]
        if len(coords) < 2:
            continue
        route_ids = sorted(by_path.get(pi, set()))
        primary = route_ids[0] if route_ids else None
        if primary is not None:
            cat = categorise(primary)
            col = category_colour(cat)
        else:
            cat = None
            col = None
        feat = {
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": coords},
            "properties": {
                "route": primary,
                "routes": route_ids,
                "category": cat,
                "colour": col,
                "source": "pdf-future",
                "phase": "future",
                "pdf_stroke": list(path.stroke) if path.stroke else None,
            },
        }
        features.append(feat)

    geojson = {"type": "FeatureCollection", "features": features}
    meta = {
        "source_pdf": str(pdf_path),
        "drawings_total": len(paths),
        "candidate_paths": len(candidates),
        "shield_spans": len(shields),
        "distinct_routes": len({rid for ids in by_path.values() for rid in ids}),
        "paths_with_routes": len(by_path),
        "georef_residuals_m": {k: round(v, 1) for k, v in residuals.items()},
    }
    return geojson, meta


def write_outputs(out_dir: _FsPath | str, geojson: dict, meta: dict) -> None:
    out = _FsPath(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "future-routes.geojson").write_text(json.dumps(geojson))
    (out / "future-routes-meta.json").write_text(json.dumps(meta, indent=2))
