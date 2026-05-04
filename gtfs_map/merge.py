from __future__ import annotations

import pyproj
from shapely.geometry import LineString, MultiLineString, Point
from shapely.ops import nearest_points, transform


_WGS84 = "EPSG:4326"
_ITM = "EPSG:2157"
_TO_ITM = pyproj.Transformer.from_crs(_WGS84, _ITM, always_xy=True)
_TO_WGS = pyproj.Transformer.from_crs(_ITM, _WGS84, always_xy=True)

# Don't keep leftover slivers shorter than this when computing the
# divergent part of dir1.
_MIN_RESIDUAL_M = 25.0


def _project_to_itm(geom):
    return transform(lambda x, y, z=None: _TO_ITM.transform(x, y), geom)


def _project_to_wgs(geom):
    return transform(lambda x, y, z=None: _TO_WGS.transform(x, y), geom)


def _connect_to_primary(
    leftover: LineString, primary: LineString, max_connect_m: float = 60.0
) -> LineString:
    """Stretch a leftover divergent fragment so its endpoints sit on
    `primary`. Only closes gaps where the leftover endpoint is within
    `max_connect_m` of the primary; otherwise leaves that end alone so
    we don't draw a spike back to the route on routes that genuinely
    diverge for kilometres.
    """
    coords = list(leftover.coords)
    if len(coords) < 2:
        return leftover
    new_coords: list[tuple[float, float]] = list(coords)

    start_pt = Point(coords[0])
    nearest_start = nearest_points(primary, start_pt)[0]
    if start_pt.distance(nearest_start) <= max_connect_m:
        new_coords = [(nearest_start.x, nearest_start.y)] + new_coords

    end_pt = Point(coords[-1])
    nearest_end = nearest_points(primary, end_pt)[0]
    if end_pt.distance(nearest_end) <= max_connect_m:
        new_coords = new_coords + [(nearest_end.x, nearest_end.y)]

    return LineString(new_coords)


def combine_directions(
    dir_a: LineString,
    dir_b: LineString | None,
    threshold_m: float = 30.0,
) -> list[LineString]:
    """Merge two direction shapes of the same route.

    Returns a list of WGS84 LineStrings. The full `dir_a` is always
    kept; any portion of `dir_b` that lies within `threshold_m` metres
    of `dir_a` is dropped, and the leftover (truly divergent) parts of
    `dir_b` are appended.

    If `dir_b` is None, returns just [dir_a].
    """
    if dir_b is None:
        return [dir_a]

    a_itm = _project_to_itm(dir_a)
    b_itm = _project_to_itm(dir_b)

    corridor = a_itm.buffer(threshold_m)
    leftover = b_itm.difference(corridor)

    out: list[LineString] = [dir_a]
    if leftover.is_empty:
        return out

    pieces: list[LineString]
    if isinstance(leftover, LineString):
        pieces = [leftover]
    elif isinstance(leftover, MultiLineString):
        pieces = list(leftover.geoms)
    else:
        # GeometryCollection or other — pull out any LineStrings.
        pieces = [g for g in getattr(leftover, "geoms", []) if isinstance(g, LineString)]

    for piece in pieces:
        if piece.length < _MIN_RESIDUAL_M:
            continue
        connected = _connect_to_primary(piece, a_itm)
        out.append(_project_to_wgs(connected))
    return out
