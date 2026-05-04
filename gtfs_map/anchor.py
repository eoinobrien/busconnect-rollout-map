from __future__ import annotations

import pyproj
from shapely.geometry import LineString
from shapely.ops import transform


_WGS84 = "EPSG:4326"
_ITM = "EPSG:2157"
_TO_ITM = pyproj.Transformer.from_crs(_WGS84, _ITM, always_xy=True)
_TO_WGS = pyproj.Transformer.from_crs(_ITM, _WGS84, always_xy=True)


def anchor_to_stops(
    line: LineString,
    stops: list[tuple[float, float]],
    max_distance_m: float = 30.0,
) -> LineString:
    """Pull each stop onto the rendered line by replacing the nearest
    line vertex with the stop coord. Stops further than max_distance_m
    from any line vertex are ignored so a divergent stop list doesn't
    yank the geometry off the road.

    Used after per-category perpendicular offsetting so the offset
    line still passes visibly through each route's actual stops
    (instead of trailing 16 m off-position).
    """
    coords = list(line.coords)
    if not stops or len(coords) < 2:
        return line

    line_itm_coords = [_TO_ITM.transform(x, y) for x, y in coords]
    new_coords = list(coords)

    for stop in stops:
        stop_itm = _TO_ITM.transform(stop[0], stop[1])
        # Closest line vertex to this stop, in metres.
        best_i = 0
        best_d = float("inf")
        for i, lc in enumerate(line_itm_coords):
            d = ((lc[0] - stop_itm[0]) ** 2 + (lc[1] - stop_itm[1]) ** 2) ** 0.5
            if d < best_d:
                best_d = d
                best_i = i
        if best_d <= max_distance_m:
            new_coords[best_i] = stop
            # Keep the ITM mirror in sync in case multiple stops compete
            # for the same vertex.
            line_itm_coords[best_i] = stop_itm

    return LineString(new_coords)
