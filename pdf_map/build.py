"""Orchestrator: PDF -> per-path GeoJSON LineStrings.

Stitches the extraction, georeferencing, palette filter, inset
rejection, and shield matcher into a single end-to-end build. Each
surviving polyline becomes one Feature with:

    properties.routes        list of route_ids whose shields sit on it
    properties.route         a single representative id (first sorted)
    properties.category      gtfs_map.category.categorise() of `route`
    properties.colour        category colour (existing layer-style key)
    properties.route_colour  per-route colour from gtfs_map.colour
                             (spine-letter palette for spine routes,
                             deterministic-by-hash for others)
    properties.pdf_colour    hex string of the original PDF stroke -
                             handy when manually QA-ing which schematic
                             corridor a feature came from
    properties.source        always "pdf-future"
    properties.phase         always "future"

Untagged paths (no shield within range) still emit features so
shared-corridor segments aren't lost; their `routes` is empty and
`route` / `route_colour` are None. The user can manually fix these
post-export.

Inset filtering: the Big Picture A3 has two city-centre detail
panels on the right side of the page that would otherwise project
to "east of Dublin" coordinates. We reject paths whose centroid
falls inside either inset rectangle (see `inset.py`).
"""

from __future__ import annotations

import json
from pathlib import Path as _FsPath

from gtfs_map.category import categorise, category_colour
from gtfs_map.colour import SPINE_COLOURS, route_colour

from .extract import extract_paths, extract_text_spans
from .georef import Affine, fit_from_spans
from .inset import reject_inset_paths
from .match import associate_shields_with_paths, find_route_shields
from .spine import route_line_paths


def _hex_of_rgb(rgb: tuple[float, float, float] | None) -> str | None:
    if rgb is None:
        return None
    r, g, b = rgb
    return "#{:02x}{:02x}{:02x}".format(
        max(0, min(255, round(r * 255))),
        max(0, min(255, round(g * 255))),
        max(0, min(255, round(b * 255))),
    )


def _route_palette_colour(route_id: str) -> str:
    """Map a route_id to a stable display colour. Spines use the
    project's curated 8-colour palette; everything else gets a
    deterministic hash colour from the same module so future runs
    stay stable.
    """
    if route_id and len(route_id) >= 2 and route_id[0] in SPINE_COLOURS and route_id[1:].isdigit():
        return SPINE_COLOURS[route_id[0]]
    return route_colour(route_id)


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
    candidates_main = reject_inset_paths(candidates)
    shields = find_route_shields(spans)
    by_path = associate_shields_with_paths(candidates_main, shields)

    features: list[dict] = []
    for pi, path in enumerate(candidates_main):
        coords = [list(transform.apply(x, y)) for x, y in path.points]
        if len(coords) < 2:
            continue
        route_ids = sorted(by_path.get(pi, set()))
        primary = route_ids[0] if route_ids else None
        if primary is not None:
            cat = categorise(primary)
            col = category_colour(cat)
            rcol = _route_palette_colour(primary)
        else:
            cat = None
            col = None
            rcol = None
        feat = {
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": coords},
            "properties": {
                "route": primary,
                "routes": route_ids,
                "category": cat,
                "colour": col,
                "route_colour": rcol,
                "pdf_colour": _hex_of_rgb(path.stroke),
                "source": "pdf-future",
                "phase": "future",
            },
        }
        features.append(feat)

    geojson = {"type": "FeatureCollection", "features": features}
    meta = {
        "source_pdf": str(pdf_path),
        "drawings_total": len(paths),
        "candidate_paths": len(candidates),
        "candidate_paths_after_inset_filter": len(candidates_main),
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
