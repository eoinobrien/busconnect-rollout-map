from __future__ import annotations

import pyproj
from shapely.geometry import LineString, MultiLineString


_WGS84 = "EPSG:4326"
_ITM = "EPSG:2157"
_TO_ITM = pyproj.Transformer.from_crs(_WGS84, _ITM, always_xy=True)
_TO_WGS = pyproj.Transformer.from_crs(_ITM, _WGS84, always_xy=True)


# Distance in metres each category is shifted perpendicular to its
# direction of travel. Higher-priority categories sit nearest the road
# centreline; lower ones step further off so cross-class overlap stays
# visible at typical zoom (12-15).
CATEGORY_OFFSET_M: dict[str, float] = {
    "spine": 0,
    "orbital": 5,
    "local": 9,
    "peak": 13,
    # Radial routes stay at true road position. They're the most
    # numerous, the most likely to be hidden under spines on shared
    # corridors anyway, and offsetting them produced visible "line
    # doesn't reach the stop" issues (e.g. route 99 + Parkgate Street).
    "radial": 0,
}


def offset_line(line: LineString, distance_m: float) -> LineString:
    """Return a parallel-offset copy of `line` shifted by distance_m
    metres to the right of its direction of travel.

    distance_m == 0 returns the input unchanged. If the offset
    operation fails (self-intersecting urban geometries, degenerate
    inputs) the original line is returned so the pipeline never loses
    data.
    """
    if distance_m == 0 or len(line.coords) < 2:
        return line

    proj_coords = [_TO_ITM.transform(x, y) for x, y in line.coords]
    proj = LineString(proj_coords)
    if proj.length < 1.0:
        return line

    try:
        offset = proj.parallel_offset(
            distance_m, side="right", join_style=2, mitre_limit=2.0
        )
    except Exception:
        return line

    if offset.is_empty:
        return line
    if isinstance(offset, MultiLineString):
        # Pick the longest piece.
        offset = max(offset.geoms, key=lambda g: g.length)
    if not isinstance(offset, LineString) or len(offset.coords) < 2:
        return line

    back = [_TO_WGS.transform(x, y) for x, y in offset.coords]
    return LineString(back)
