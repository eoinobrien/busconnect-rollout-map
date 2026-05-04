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
    """Loose bundling via point-cloud union-find with first-point
    canonical.

    Single-pass algorithm:
      1. Sort routes longest-first; densify and quantize every point
         in route+component+vertex order (so the longest route's
         points get the smallest indices).
      2. Build one STRtree over all unique points.
      3. Union-find spatial cluster with disjoint-route-set
         constraint (so two points from the same route never merge
         into one cluster — that would collapse a long straight line
         into a single mega-cluster).
      4. For each cluster, canonical position = the smallest-index
         member's point. Because routes were sorted longest-first,
         this is the longest route's point at that location.
      5. Build edges from each route's quantized point sequence,
         snapped through find() to its cluster's canonical.

    O(N log N) end-to-end — fast enough for the full Dublin GTFS
    feed (~320 k densified points across all routes). Produces clean
    geometry because every clustered route snaps to the longest
    route's exact point sequence rather than to a wobbly centroid.
    """
    import math as _math

    from shapely.geometry import Point
    from shapely.strtree import STRtree

    # Sort routes longest-first so each cluster's canonical (smallest
    # index) lives on the longest route's geometry.
    routes_sorted: list[tuple[str, list[LineString]]] = []
    for sub_id, geom in sub_routes.items():
        comps_proj = [_project(line, _TO_ITM) for line in _components(geom)]
        if not comps_proj:
            continue
        total = sum(c.length for c in comps_proj)
        routes_sorted.append((sub_id, comps_proj, total))
    routes_sorted.sort(key=lambda kv: -kv[2])

    # Densify + quantize every route, accumulating unique points.
    per_route_points: list[tuple[str, list[tuple[float, float]]]] = []
    unique_pts: list[tuple[float, float]] = []
    pt_index: dict[tuple[float, float], int] = {}
    pt_route_sets: list[set[str]] = []

    for sub_id, comps_proj, _total in routes_sorted:
        for line_proj in comps_proj:
            densified = shapely.segmentize(line_proj, max_segment_length=_DENSIFY_M)
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

    # Build STRtree once over every unique point.
    geoms = [Point(p) for p in unique_pts]
    tree = STRtree(geoms)
    parent = list(range(len(unique_pts)))
    cluster_routes: dict[int, set[str]] = {
        i: set(pt_route_sets[i]) for i in range(len(unique_pts))
    }

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def try_union(a: int, b: int) -> bool:
        ra, rb = find(a), find(b)
        if ra == rb:
            return False
        if not cluster_routes[ra].isdisjoint(cluster_routes[rb]):
            return False
        # Merge: keep the smaller root so canonical (= smallest member)
        # remains stable.
        if rb < ra:
            ra, rb = rb, ra
        parent[rb] = ra
        cluster_routes[ra] |= cluster_routes[rb]
        del cluster_routes[rb]
        return True

    # Pair each point with its NEAREST eligible neighbour first. Order
    # matters: a long parallel pair where each A_i pairs with B_i (not
    # some skipped B_k) keeps each route's snapped sequence in order
    # along the corridor instead of jumping around.
    tol_sq = tolerance_m * tolerance_m
    for i, pt in enumerate(unique_pts):
        ix, iy = pt
        candidates = []
        for j in tree.query(geoms[i].buffer(tolerance_m)):
            if j == i:
                continue
            jx, jy = unique_pts[j]
            dx, dy = jx - ix, jy - iy
            d2 = dx * dx + dy * dy
            if d2 <= tol_sq:
                candidates.append((d2, j))
        candidates.sort()
        for _d, j in candidates:
            try_union(i, j)

    # Canonical position per cluster: the smallest-index member's
    # point — because routes were processed longest-first, this is
    # the longest route's point in the cluster.
    canonical_root_to_pt: dict[int, tuple[float, float]] = {}
    for i in range(len(unique_pts)):
        root = find(i)
        existing = canonical_root_to_pt.get(root)
        if existing is None:
            canonical_root_to_pt[root] = unique_pts[root]

    # Build edges from each route's snapped point sequence.
    edges: dict[frozenset, set[str]] = defaultdict(set)
    for sub_id, pts in per_route_points:
        snapped = [canonical_root_to_pt[find(pt_index[p])] for p in pts]
        cleaned: list[tuple[float, float]] = []
        for s in snapped:
            if not cleaned or cleaned[-1] != s:
                cleaned.append(s)
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
