from __future__ import annotations

from collections import defaultdict

from shapely.geometry import LineString, MultiLineString, mapping
from shapely.ops import linemerge


def _endpoint_key(coord) -> tuple[float, float]:
    # Round to ~0.5 m precision in lat/lon (~5e-6 degrees) so two
    # endpoints that ended up at slightly-different floats still
    # cluster as the same junction.
    return (round(coord[0], 6), round(coord[1], 6))


def _jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    union = len(a | b)
    if union == 0:
        return 0.0
    return len(a & b) / union


def consolidate_features(
    features: list[dict],
    jaccard_threshold: float = 0.5,
) -> list[dict]:
    """Merge canonical bundle features that share an endpoint and have
    largely-overlapping route_sets into one feature with the union of
    all participating routes.

    Reduces visual fragmentation where small along-corridor variations
    in cluster pairing produced many tiny features with slightly-
    different route_sets (e.g. {C1-C6}, {C1-C5}, {C1-C4}, {4,C1-C6}
    along the same Heuston quay). After consolidation those collapse
    to one feature per coherent corridor stretch.
    """
    if not features:
        return features

    # Endpoint -> list of feature indices touching that endpoint.
    endpoints: dict[tuple[float, float], list[int]] = defaultdict(list)
    for i, f in enumerate(features):
        coords = f["geometry"]["coordinates"]
        if len(coords) < 2:
            continue
        endpoints[_endpoint_key(coords[0])].append(i)
        endpoints[_endpoint_key(coords[-1])].append(i)

    parent = list(range(len(features)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    # For each shared-endpoint group, union pairs whose route_sets are
    # similar enough.
    for ep, idxs in endpoints.items():
        if len(idxs) < 2:
            continue
        # Pre-compute route_sets once per index in the group.
        sets = [set(features[i]["properties"]["route_set"]) for i in idxs]
        for a in range(len(idxs)):
            for b in range(a + 1, len(idxs)):
                if _jaccard(sets[a], sets[b]) >= jaccard_threshold:
                    union(idxs[a], idxs[b])

    # Group features by their root.
    groups: dict[int, list[int]] = defaultdict(list)
    for i in range(len(features)):
        groups[find(i)].append(i)

    out: list[dict] = []
    for group_ids in groups.values():
        if len(group_ids) == 1:
            out.append(features[group_ids[0]])
            continue
        # Union all routes across the merged group.
        merged_routes: set[str] = set()
        lines: list[LineString] = []
        for gi in group_ids:
            f = features[gi]
            merged_routes.update(f["properties"]["route_set"])
            lines.append(LineString(f["geometry"]["coordinates"]))

        # linemerge stitches contiguous lines together; pieces that
        # don't share endpoints (e.g. across a degree-3+ junction)
        # stay as a MultiLineString. Emitting them as ONE Feature
        # with MultiLineString geometry keeps all of them attributed
        # to the same merged route_set without producing parallel
        # ghost lines from per-piece emission.
        merged_geom = linemerge(MultiLineString(lines)) if len(lines) > 1 else lines[0]
        if isinstance(merged_geom, LineString):
            geom_dict = mapping(merged_geom)
        else:
            geom_dict = mapping(merged_geom)  # MultiLineString -> GeoJSON MultiLineString

        proto_props = features[group_ids[0]]["properties"]
        out.append({
            "type": "Feature",
            "geometry": geom_dict,
            "properties": {
                **proto_props,
                "route_set": sorted(merged_routes),
                "route_count": len(merged_routes),
                "kind": "shared" if len(merged_routes) >= 2 else "single",
            },
        })
    return out
