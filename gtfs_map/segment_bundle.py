"""Segment-level corridor bundling.

`segment_route_by_others(name, line, others, tol)` walks one route's
geometry and splits it whenever the set of *other* routes within
`tol` metres changes. Each sub-segment is annotated with its
membership frozenset (always including `name`).

`segment_bundle(routes, tol)` runs that walk for every route,
dedupes shared sub-segments by primary (lex-smallest member), and
caches projections + an STRtree spatial index so the inner loop
only checks lines whose bbox is near the query line.

Performance note: we test "is this point within `tol` of line L?"
via `point.distance(L) < tol` rather than buffering L into a
polygon and using point-in-polygon. A buffered polyline polygon
has 30-50x as many vertices as the original line, and point-in-
polygon costs scale with vertex count.
"""

from __future__ import annotations

import pyproj
from shapely.geometry import LineString
from shapely.ops import substring
from shapely.strtree import STRtree


_WGS84 = "EPSG:4326"
_ITM = "EPSG:2157"
_TO_ITM = pyproj.Transformer.from_crs(_WGS84, _ITM, always_xy=True)
_FROM_ITM = pyproj.Transformer.from_crs(_ITM, _WGS84, always_xy=True)


def _project_to_itm(line: LineString) -> LineString:
    return LineString([_TO_ITM.transform(x, y) for x, y in line.coords])


def _segment_one(
    name: str,
    line_wgs: LineString,
    line_itm: LineString,
    members_lookup,
    sample_step_m: float,
    min_segment_m: float,
) -> list[tuple[LineString, frozenset[str], str]]:
    """Inner walk. `members_lookup(point)` returns set[str] of route
    names already verified within tolerance of `point` (the lookup
    owns the tolerance check, so different callers can pick fast or
    exact strategies).

    Returns (sub_line, members, walker) tuples.
    """
    L = line_itm.length
    if L == 0:
        return [(line_wgs, frozenset({name}), name)]

    n_samples = max(2, int(L / sample_step_m) + 1)
    distances = [i * L / (n_samples - 1) for i in range(n_samples)]
    raw: list[set[str]] = []
    for d in distances:
        pt = line_itm.interpolate(d)
        members = members_lookup(pt) | {name}
        raw.append(set(members))

    # Drop transient memberships. A route m only counts as
    # "corridor-shared" with the walker if m is within tolerance
    # for at least `min_segment_m` of consecutive walker travel.
    # Perpendicular crossings (e.g. an N-S spine briefly within
    # tolerance of an E-W quay route at a bridge) span only 2-3
    # samples and get filtered out — without this they'd pollute
    # the quay route's `routes` list with every spine that crosses
    # the river.
    min_persistence = max(1, int(min_segment_m / sample_step_m))
    memberships: list[frozenset[str]] = []
    for i in range(len(raw)):
        kept = {name}
        for m in raw[i]:
            if m == name:
                continue
            run = 1
            j = i - 1
            while j >= 0 and m in raw[j]:
                run += 1
                j -= 1
            j = i + 1
            while j < len(raw) and m in raw[j]:
                run += 1
                j += 1
            if run >= min_persistence:
                kept.add(m)
        memberships.append(frozenset(kept))

    segments: list[list] = []
    seg_start = 0.0
    seg_members = memberships[0]
    for i in range(1, len(memberships)):
        if memberships[i] != seg_members:
            seg_end = (distances[i - 1] + distances[i]) / 2
            segments.append([seg_start, seg_end, seg_members])
            seg_start = seg_end
            seg_members = memberships[i]
    segments.append([seg_start, L, seg_members])

    segments = _merge_slivers(segments, min_segment_m)

    # Single-segment fast path: no membership changes, so the output
    # is just the original WGS line. Avoids the WGS↔ITM round-trip
    # and preserves vertex coords exactly.
    if len(segments) == 1:
        return [(line_wgs, segments[0][2], name)]

    # Substring on the ITM line then project back to WGS. Doing the
    # substring on `line_wgs` directly would interpret f0/f1 as
    # fractions of the WGS line's *degree-length*, which is not the
    # same scale as the ITM metre-length we used for `s` and `e` —
    # 1° latitude and 1° longitude don't span the same metres at
    # Dublin's latitude, so a route that mixes E-W and N-S travel
    # has different fraction-to-physical-position mappings in the
    # two projections. The mismatch can shift a sub-segment's
    # rendered location by hundreds of metres, e.g. a real shared
    # stretch at O'Connell Bridge being drawn 800m west on the south
    # quay where the route doesn't actually run.
    wgs_first = line_wgs.coords[0]
    wgs_last = line_wgs.coords[-1]
    out: list[tuple[LineString, frozenset[str], str]] = []
    for idx, (s, e, members) in enumerate(segments):
        f0 = max(0.0, min(1.0, s / L))
        f1 = max(0.0, min(1.0, e / L))
        sub_itm = substring(line_itm, f0, f1, normalized=True)
        coords = [_FROM_ITM.transform(x, y) for x, y in sub_itm.coords]
        # Pin the very first / very last vertex to the original WGS
        # coords so callers that depend on exact route-endpoint
        # matching see no projection round-trip drift.
        if idx == 0:
            coords[0] = wgs_first
        if idx == len(segments) - 1:
            coords[-1] = wgs_last
        out.append((LineString(coords), members, name))
    return out


def segment_route_by_others(
    name: str,
    line: LineString,
    others: dict[str, LineString],
    tolerance_m: float = 10.0,
    sample_step_m: float = 5.0,
    min_segment_m: float = 50.0,
) -> list[tuple[LineString, frozenset[str], str]]:
    """Split `line` into sub-segments by membership.

    At each sample point along `line`, membership is the set
    `{name}` plus every key in `others` whose line lies within
    `tolerance_m` of that sample point. Walks the line; whenever the
    membership set changes, emits a new sub-segment.

    Sub-segments shorter than `min_segment_m` are merged into their
    longer neighbour, suppressing tiny junction slivers.

    The returned sub-LineStrings together cover `line` end-to-end
    with no gaps and no overlaps. Endpoints of consecutive segments
    coincide exactly.
    """
    line_itm = _project_to_itm(line)
    others_itm = {n: _project_to_itm(g) for n, g in others.items()}

    def lookup(pt):
        return {
            n for n, g in others_itm.items()
            if pt.distance(g) < tolerance_m
        }

    return _segment_one(
        name,
        line,
        line_itm,
        lookup,
        sample_step_m,
        min_segment_m,
    )


def segment_bundle(
    routes: dict[str, LineString],
    tolerance_m: float = 10.0,
    sample_step_m: float = 10.0,
    min_segment_m: float = 50.0,
) -> list[tuple[LineString, frozenset[str], str]]:
    """Apply per-route segmentation to every route in `routes`.

    Each route's full path is emitted as a sequence of sub-segments
    using its OWN geometry. A shared corridor between A and B
    therefore appears twice in the output — once from A's walk,
    once from B's walk — both carrying `members={A, B}`. The two
    overlapping sub-segments are within tolerance of each other but
    follow each route's own GTFS shape, so each route stays
    geometrically continuous (no gaps where a primary's geometry
    would have taken over).

    Tooltip / highlight code can dedupe visually by matching on
    `members` — but the geometry stays per-route for continuity.

    Optimisations that matter for Dublin-scale data:
      - project each route once
      - subdivide each ITM line into ~CHUNK_M chunks and STRtree
        the chunks. Per-sample distance is then to a 100m chunk,
        not a 30km line — Shapely's distance loop scales with the
        number of vertices it has to traverse, so chunk size is the
        single biggest knob for the Dublin build.
      - default sample_step_m at 10m (still well below the 50m
        sliver threshold).
    """
    if not routes:
        return []

    CHUNK_M = 100.0

    names = sorted(routes)
    itm_lines = {n: _project_to_itm(routes[n]) for n in names}

    chunk_geoms: list[LineString] = []
    chunk_owners: list[str] = []
    for n in names:
        line = itm_lines[n]
        L = line.length
        if L == 0:
            continue
        n_chunks = max(1, int(L / CHUNK_M) + 1)
        for i in range(n_chunks):
            f0 = i / n_chunks
            f1 = (i + 1) / n_chunks
            chunk = substring(line, f0, f1, normalized=True)
            chunk_geoms.append(chunk)
            chunk_owners.append(n)
    tree = STRtree(chunk_geoms)

    def lookup(pt):
        # Bbox query: chunks whose envelope intersects pt's
        # tolerance buffer. Then exact distance per chunk; aggregate
        # by owner. Cost per sample is O(few chunks × short line).
        buf = pt.buffer(tolerance_m)
        idxs = tree.query(buf)
        members: set[str] = set()
        for i in idxs:
            owner = chunk_owners[i]
            if owner in members:
                continue
            if pt.distance(chunk_geoms[i]) < tolerance_m:
                members.add(owner)
        return members

    out: list[tuple[LineString, frozenset[str], str]] = []
    for name in names:
        sub_segments = _segment_one(
            name,
            routes[name],
            itm_lines[name],
            lookup,
            sample_step_m,
            min_segment_m,
        )
        out.extend(sub_segments)
    return out


def _merge_slivers(segments, min_len):
    """Iteratively merge any segment shorter than min_len into its
    longer neighbour, then coalesce adjacent same-membership runs."""
    if len(segments) <= 1:
        return segments
    while True:
        idx = None
        smallest = min_len
        for i, (s, e, _m) in enumerate(segments):
            if e - s < smallest:
                smallest = e - s
                idx = i
        if idx is None:
            return segments

        left = segments[idx - 1] if idx > 0 else None
        right = segments[idx + 1] if idx + 1 < len(segments) else None
        if left is None and right is None:
            return segments
        if left is None:
            target_members = right[2]
        elif right is None:
            target_members = left[2]
        else:
            l_len = left[1] - left[0]
            r_len = right[1] - right[0]
            target_members = left[2] if l_len >= r_len else right[2]

        segments[idx][2] = target_members
        coalesced: list[list] = [list(segments[0])]
        for s, e, m in segments[1:]:
            if coalesced[-1][2] == m:
                coalesced[-1][1] = e
            else:
                coalesced.append([s, e, m])
        segments = coalesced
        if len(segments) <= 1:
            return segments
