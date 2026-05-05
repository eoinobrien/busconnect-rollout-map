"""Bundling primitives.

share_corridor(a, b, ...) — pairwise: do these two routes ride
                            mostly the same road?
corridor_groups(routes_dict, ...) — given many routes, return
                            connected components (transitively
                            grouped via pairwise share_corridor).
"""

from __future__ import annotations

import pyproj
from shapely.geometry import LineString
from shapely.ops import transform


_WGS84 = "EPSG:4326"
_ITM = "EPSG:2157"
_TO_ITM = pyproj.Transformer.from_crs(_WGS84, _ITM, always_xy=True)


def _project_to_itm(geom):
    return transform(lambda x, y, z=None: _TO_ITM.transform(x, y), geom)


def share_corridor(
    a: LineString,
    b: LineString,
    tolerance_m: float = 10.0,
    overlap_threshold: float = 0.9,
) -> bool:
    """Do `a` and `b` ride mostly the same road?

    Returns True iff at least `overlap_threshold` (default 90%) of
    the SHORTER line lies within `tolerance_m` of the longer line.
    Direction-agnostic. Inputs in WGS84 lon/lat.

    Using the shorter line as the denominator catches the case where
    a short feeder route entirely follows a longer trunk — every
    metre of the short route is on the trunk's corridor.
    """
    a_itm = _project_to_itm(a)
    b_itm = _project_to_itm(b)
    if a_itm.length == 0 or b_itm.length == 0:
        return False

    if a_itm.length <= b_itm.length:
        short, long_ = a_itm, b_itm
    else:
        short, long_ = b_itm, a_itm

    inside = short.intersection(long_.buffer(tolerance_m))
    if inside.is_empty:
        return False
    return inside.length / short.length >= overlap_threshold


def corridor_groups(
    routes: dict[str, LineString],
    tolerance_m: float = 10.0,
    overlap_threshold: float = 0.9,
) -> list[frozenset[str]]:
    """Group route names into connected components based on
    pairwise `share_corridor`.

    Two routes that share a corridor are in the same group. Sharing
    is transitive — if A shares with B and B shares with C, all
    three end up grouped even when A and C don't share directly.

    Returns a list of frozensets, one per component. Singletons
    (routes that share with no one else) are included as size-1
    sets so the output covers every input.
    """
    names = list(routes)
    if not names:
        return []

    # Union-find over names.
    parent = {n: n for n in names}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    # Pre-project each route once so share_corridor doesn't redo it
    # in the inner loop. (share_corridor still works on WGS so we
    # just call it; speed isn't critical for now.)
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            if share_corridor(
                routes[names[i]], routes[names[j]],
                tolerance_m=tolerance_m,
                overlap_threshold=overlap_threshold,
            ):
                union(names[i], names[j])

    groups: dict[str, set[str]] = {}
    for n in names:
        root = find(n)
        groups.setdefault(root, set()).add(n)
    return [frozenset(g) for g in groups.values()]
