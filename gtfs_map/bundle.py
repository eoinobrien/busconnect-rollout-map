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
    """Loose bundling via spatial clustering: any two densified points
    within `tolerance_m` of each other end up at the same canonical
    centroid before edges are built. Catches routes that share a road
    but with shapes sampled differently (different lanes, different
    sampling rates) — a 2 m grid would treat them as separate."""
    from shapely.geometry import Point
    from shapely.strtree import STRtree

    # Step 1: project every component, densify, store points alongside
    # the sub_id that produced them. Track which sub_routes each unique
    # point belongs to so the cluster step can avoid merging points
    # from the SAME route (which would collapse a long straight line
    # of densified points into one cluster).
    per_route_points: list[tuple[str, list[tuple[float, float]]]] = []
    unique_pts: list[tuple[float, float]] = []
    pt_index: dict[tuple[float, float], int] = {}
    pt_route_sets: list[set[str]] = []

    for sub_id, geom in sub_routes.items():
        for line in _components(geom):
            projected = _project(line, _TO_ITM)
            densified = shapely.segmentize(projected, max_segment_length=_DENSIFY_M)
            line_pts = [_quantize(x, y) for x, y in densified.coords]
            per_route_points.append((sub_id, line_pts))
            for p in line_pts:
                idx = pt_index.get(p)
                if idx is None:
                    pt_index[p] = len(unique_pts)
                    unique_pts.append(p)
                    pt_route_sets.append({sub_id})
                else:
                    pt_route_sets[idx].add(sub_id)

    if not unique_pts:
        return {}

    # Step 2: union-find spatial cluster within tolerance_m. The key
    # constraint: two clusters only merge if their CURRENT route sets
    # are disjoint. Otherwise a chain of unions A0-B0-A1-B1... would
    # collapse every point on a long parallel pair into one mega-
    # cluster (because each individual union "looks" disjoint at the
    # point level even when the growing cluster already contains both
    # routes).
    point_geoms = [Point(p) for p in unique_pts]
    tree = STRtree(point_geoms)
    parent = list(range(len(unique_pts)))
    cluster_routes: dict[int, set[str]] = {
        i: set(pt_route_sets[i]) for i in range(len(unique_pts))
    }

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def try_union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra == rb:
            return
        if not cluster_routes[ra].isdisjoint(cluster_routes[rb]):
            return  # merging would put two same-route points in one cluster
        parent[ra] = rb
        cluster_routes[rb] |= cluster_routes[ra]
        del cluster_routes[ra]

    for i, g in enumerate(point_geoms):
        for j in tree.query(g.buffer(tolerance_m)):
            if j == i:
                continue
            if g.distance(point_geoms[j]) <= tolerance_m:
                try_union(i, j)

    # Step 3: canonical position per cluster — the centroid keeps the
    # bundled line near the average of the input shapes rather than
    # snapping to any one route's geometry.
    cluster_members: dict[int, list[int]] = defaultdict(list)
    for i in range(len(unique_pts)):
        cluster_members[find(i)].append(i)
    canonical: dict[tuple[float, float], tuple[float, float]] = {}
    for root, members in cluster_members.items():
        cx = sum(unique_pts[i][0] for i in members) / len(members)
        cy = sum(unique_pts[i][1] for i in members) / len(members)
        for i in members:
            canonical[unique_pts[i]] = (cx, cy)

    # Step 4: build edges using canonical positions.
    edges: dict[frozenset, set[str]] = defaultdict(set)
    for sub_id, pts in per_route_points:
        snapped = [canonical[p] for p in pts]
        # Drop consecutive duplicates introduced by clustering.
        cleaned: list[tuple[float, float]] = []
        for p in snapped:
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
