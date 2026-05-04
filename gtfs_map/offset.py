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

    Uses a manual perpendicular shift per vertex (averaging the
    surrounding segment normals) rather than shapely.parallel_offset,
    which has a habit of dropping pieces at sharp corners and
    returning a fragmented MultiLineString. The manual version is
    guaranteed to keep the same vertex count as the input, so there
    are no gaps in the output.

    distance_m == 0 returns the input unchanged.
    """
    if distance_m == 0 or len(line.coords) < 2:
        return line

    # Project to ITM so distances are in metres.
    proj = [_TO_ITM.transform(x, y) for x, y in line.coords]
    n = len(proj)

    # Per-segment unit normal pointing right of travel.
    # For a segment (x1,y1)->(x2,y2): direction (dx,dy)/L, right normal
    # is (dy,-dx)/L.
    seg_normals: list[tuple[float, float]] = []
    for i in range(n - 1):
        x1, y1 = proj[i]
        x2, y2 = proj[i + 1]
        dx, dy = x2 - x1, y2 - y1
        L = (dx * dx + dy * dy) ** 0.5
        if L < 1e-9:
            seg_normals.append((0.0, 0.0))
        else:
            seg_normals.append((dy / L, -dx / L))

    # Per-vertex normal: average of adjacent segments.
    out_proj: list[tuple[float, float]] = []
    for i in range(n):
        if i == 0:
            nx, ny = seg_normals[0]
        elif i == n - 1:
            nx, ny = seg_normals[-1]
        else:
            a = seg_normals[i - 1]
            b = seg_normals[i]
            nx = a[0] + b[0]
            ny = a[1] + b[1]
            mag = (nx * nx + ny * ny) ** 0.5
            if mag < 1e-9:
                nx, ny = b
            else:
                nx /= mag
                ny /= mag
        x, y = proj[i]
        out_proj.append((x + nx * distance_m, y + ny * distance_m))

    return LineString([_TO_WGS.transform(x, y) for x, y in out_proj])
