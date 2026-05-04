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


def _components(geom: object) -> list[LineString]:
    if isinstance(geom, LineString):
        return [geom]
    if isinstance(geom, MultiLineString):
        return list(geom.geoms)
    return [g for g in geom if isinstance(g, LineString)]


def _edges_with_routes(
    sub_routes: dict[str, object],
) -> dict[frozenset, set[str]]:
    """Tight bundling at the 2 m grid: two routes only share an edge if
    their densified, grid-snapped points coincide exactly. Fast,
    suitable for spine sub-routes whose shapes hug the same road."""
    edges: dict[frozenset, set[str]] = defaultdict(set)
    for sub_id, geom in sub_routes.items():
        for line in _components(geom):
            projected = _project(line, _TO_ITM)
            densified = shapely.segmentize(projected, max_segment_length=_DENSIFY_M)
            pts = [_quantize(x, y) for x, y in densified.coords]
            for a, b in zip(pts, pts[1:]):
                if a == b:
                    continue
                edges[frozenset((a, b))].add(sub_id)
    return edges


def _edges_with_routes_loose(
    sub_routes: dict[str, object],
    tolerance_m: float,
) -> dict[frozenset, set[str]]:
    """Loose bundling via primary-line snapping.

    Sorts routes by length, longest first. The longest route's
    geometry becomes the seed canonical. Each subsequent route's
    densified points are perpendicular-projected onto the closest
    canonical segment within tolerance_m; points further out keep
    their original position and become NEW canonical segments for
    later routes to snap to.

    This is option B from the design discussion: the bundled trunk
    inherits one route's clean line rather than a wobbly centroid
    averaged across pairings, eliminating the cluster-centroid
    jitter that the union-find approach exhibited.
    """
    from shapely.geometry import LineString as _LS
    from shapely.geometry import Point
    from shapely.strtree import STRtree

    # Pre-project + sort routes by total length, longest first.
    routes_with_lines: list[tuple[str, list[LineString], float]] = []
    for sub_id, geom in sub_routes.items():
        components_proj: list[LineString] = []
        total_len = 0.0
        for line in _components(geom):
            proj = _project(line, _TO_ITM)
            components_proj.append(proj)
            total_len += proj.length
        if components_proj:
            routes_with_lines.append((sub_id, components_proj, total_len))
    routes_with_lines.sort(key=lambda kv: -kv[2])

    canonical_lines: list[LineString] = []
    per_route_snapped: list[tuple[str, list[tuple[float, float]]]] = []

    for sub_id, components_proj, _length in routes_with_lines:
        # Snapshot the canonical at start of this route so all of its
        # snapping decisions are made against a stable reference.
        tree = STRtree(canonical_lines) if canonical_lines else None
        new_segments: list[LineString] = []

        for line_proj in components_proj:
            densified = shapely.segmentize(line_proj, max_segment_length=_DENSIFY_M)
            snapped_pts: list[tuple[float, float]] = []
            current_new: list[tuple[float, float]] = []

            for c in densified.coords:
                pt = Point(c)
                snapped = None
                if tree is not None:
                    best_dist = tolerance_m
                    for cl_idx in tree.query(pt.buffer(tolerance_m)):
                        cl = canonical_lines[cl_idx]
                        proj_d = cl.project(pt)
                        proj_pt = cl.interpolate(proj_d)
                        dist = pt.distance(proj_pt)
                        if dist < best_dist:
                            best_dist = dist
                            snapped = (proj_pt.x, proj_pt.y)

                if snapped is None:
                    snapped_pts.append((c[0], c[1]))
                    current_new.append((c[0], c[1]))
                else:
                    snapped_pts.append(snapped)
                    if len(current_new) >= 2:
                        new_segments.append(_LS(current_new))
                    current_new = []

            if len(current_new) >= 2:
                new_segments.append(_LS(current_new))

            per_route_snapped.append((sub_id, snapped_pts))

        # Subsequent routes can also snap to this route's divergent
        # stretches (its un-snapped runs).
        canonical_lines.extend(new_segments)

    # Build edges from snapped (then quantized) point sequences. The
    # snapping itself produces clean geometry; the 2 m quantize is
    # there only so identical positions hash the same in the edge keys.
    edges: dict[frozenset, set[str]] = defaultdict(set)
    for sub_id, pts in per_route_snapped:
        pts_q = [_quantize(x, y) for x, y in pts]
        cleaned: list[tuple[float, float]] = []
        for p in pts_q:
            if not cleaned or cleaned[-1] != p:
                cleaned.append(p)
        for a, b in zip(cleaned, cleaned[1:]):
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


def bundle_routes(
    routes: dict[str, LineString], tolerance_m: float | None = None
) -> list[dict]:
    """Topologically bundle a set of routes into shared-vs-single
    segments.

    `tolerance_m` controls how close two routes' shapes need to be to
    bundle:
      None or <=2: tight 2 m grid (default — preserves geometry, only
                   bundles routes that hug the same road samples).
      else:        spatial clustering at the given metres — routes
                   whose shapes are within `tolerance_m` of each other
                   bundle even if their GTFS shapes differ slightly.

    Returns a list of GeoJSON-style Feature dicts where each Feature
    represents the longest contiguous run of road traversed by the
    same set of routes. Properties:
      route_set:   list of route ids on this segment
      route_count: len(route_set)
      kind:        "shared" if route_count >= 2, else "single"
    """
    if tolerance_m is not None and tolerance_m > _GRID_M * 1.5:
        edge_routes = _edges_with_routes_loose(routes, tolerance_m)
    else:
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
