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


def offset_line(geom, distance_m: float, taper_m: float = 0.0):
    """Return a parallel-offset copy of `geom` shifted by distance_m
    metres to the right of its direction of travel.

    The offset distance tapers smoothly to 0 within `taper_m` metres
    of each endpoint so different-category lines converge at the
    exact same junction node instead of ending at different
    perpendicular positions. The middle of the line still gets the
    full offset; only the ends pull back to the canonical position.

    Accepts either a LineString or a MultiLineString. For a
    MultiLineString, each component is offset independently.

    Uses a manual perpendicular shift per vertex (averaging the
    surrounding segment normals) rather than shapely.parallel_offset,
    which drops pieces at sharp corners.

    distance_m == 0 returns the input unchanged.
    """
    if distance_m == 0:
        return geom
    if isinstance(geom, MultiLineString):
        offset_pieces = [offset_line(g, distance_m, taper_m) for g in geom.geoms]
        return MultiLineString([p for p in offset_pieces if p is not None and not p.is_empty])
    line = geom
    if len(line.coords) < 2:
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

    # Cumulative distance from the start, used for endpoint taper.
    cum = [0.0]
    for i in range(1, n):
        dx = proj[i][0] - proj[i - 1][0]
        dy = proj[i][1] - proj[i - 1][1]
        cum.append(cum[-1] + (dx * dx + dy * dy) ** 0.5)
    total_len = cum[-1]

    # Per-vertex normal: average of adjacent segments. Multiplied by
    # a taper factor that goes from 0 at each endpoint to 1 once
    # we're more than `taper_m` from any endpoint.
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

        if taper_m > 0 and total_len > 0:
            d_start = cum[i]
            d_end = total_len - cum[i]
            taper = min(1.0, min(d_start, d_end) / taper_m)
        else:
            taper = 1.0

        x, y = proj[i]
        out_proj.append((x + nx * distance_m * taper, y + ny * distance_m * taper))

    return LineString([_TO_WGS.transform(x, y) for x, y in out_proj])
