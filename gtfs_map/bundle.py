from __future__ import annotations

from collections import defaultdict
from typing import Iterable

import pyproj
import shapely
from shapely.geometry import LineString, MultiLineString, mapping
from shapely.ops import linemerge


_WGS84 = "EPSG:4326"
_ITM = "EPSG:2157"  # Irish Transverse Mercator — metres
_TO_ITM = pyproj.Transformer.from_crs(_WGS84, _ITM, always_xy=True)
_TO_WGS = pyproj.Transformer.from_crs(_ITM, _WGS84, always_xy=True)

# Densify long segments so two routes sampling the same road at different
# rates get edges in the same places.
_DENSIFY_M = 5.0
# Snap coordinates to a 2 m grid so near-identical points compare equal.
_GRID_M = 2.0


def _project(line: LineString, transformer: pyproj.Transformer) -> LineString:
    coords = [transformer.transform(x, y) for x, y in line.coords]
    return LineString(coords)


def _quantize(x: float, y: float) -> tuple[float, float]:
    return (round(x / _GRID_M) * _GRID_M, round(y / _GRID_M) * _GRID_M)


def _edges_with_routes(
    sub_routes: dict[str, LineString],
) -> dict[frozenset, set[str]]:
    """Walk every line, return a mapping from quantized undirected edge
    (frozenset of two grid-snapped points) to the set of sub-route ids
    that use it."""
    edges: dict[frozenset, set[str]] = defaultdict(set)
    for sub_id, line in sub_routes.items():
        projected = _project(line, _TO_ITM)
        densified = shapely.segmentize(projected, max_segment_length=_DENSIFY_M)
        pts = [_quantize(x, y) for x, y in densified.coords]
        for a, b in zip(pts, pts[1:]):
            if a == b:
                continue
            edges[frozenset((a, b))].add(sub_id)
    return edges


def _merge_edges_to_lines(
    edges: Iterable[tuple[tuple[float, float], tuple[float, float]]]
) -> list[LineString]:
    """Merge contiguous undirected edges into the longest possible
    LineStrings using shapely.ops.linemerge."""
    raw = [LineString([a, b]) for a, b in edges if a != b]
    if not raw:
        return []
    merged = linemerge(MultiLineString(raw))
    if isinstance(merged, LineString):
        return [merged]
    return list(merged.geoms)


def bundle_routes(routes: dict[str, LineString]) -> list[dict]:
    """Topologically bundle a set of routes into shared-vs-single
    segments.

    Returns a list of GeoJSON-style Feature dicts where each Feature
    represents the longest contiguous run of road traversed by the
    same set of routes. Properties:
      route_set:   list of route ids on this segment
      route_count: len(route_set)
      kind:        "shared" if route_count >= 2, else "single"

    Inputs are LineStrings in WGS84 (lon, lat). Geometry is projected
    to Irish Transverse Mercator for metric snapping (5 m densify, 2 m
    grid quantization), then un-projected for the output Features.
    """
    edge_routes = _edges_with_routes(routes)

    # Group edges by the set of routes that share them.
    edges_by_key: dict[frozenset, list] = defaultdict(list)
    for edge_key, route_ids in edge_routes.items():
        a, b = sorted(edge_key)
        edges_by_key[frozenset(route_ids)].append((a, b))

    features: list[dict] = []
    for routes_fs, edges in edges_by_key.items():
        for line_itm in _merge_edges_to_lines(edges):
            line_wgs = _project(line_itm, _TO_WGS)
            features.append(
                {
                    "type": "Feature",
                    "geometry": mapping(line_wgs),
                    "properties": {
                        "route_set": sorted(routes_fs),
                        "route_count": len(routes_fs),
                        "kind": "shared" if len(routes_fs) >= 2 else "single",
                    },
                }
            )
    return features


# Back-compat alias used by older callers / tests still on "trunk/branch"
# terminology. Treats the whole input as a spine: shared-by-all → trunk,
# everything else → branch.
def bundle_spine(sub_routes: dict[str, LineString]) -> list[dict]:
    spine_size = len(sub_routes)
    feats = bundle_routes(sub_routes)
    for f in feats:
        f["properties"]["kind"] = (
            "trunk" if f["properties"]["route_count"] == spine_size else "branch"
        )
    return feats
