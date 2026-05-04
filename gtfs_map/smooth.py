from __future__ import annotations

import pyproj
from shapely.geometry import LineString
from shapely.ops import transform


_TO_ITM = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:2157", always_xy=True)
_TO_WGS = pyproj.Transformer.from_crs("EPSG:2157", "EPSG:4326", always_xy=True)


def smooth_line(line: LineString, tolerance_m: float = 3.0) -> LineString:
    """Douglas-Peucker simplify in metres to remove the staircase
    artifacts left by 2 m quantization during bundling.

    Re-projects to ITM so `tolerance_m` is in actual metres rather than
    degrees, simplifies, then projects back. Falls back to the input
    on degenerate or single-segment inputs.
    """
    if len(line.coords) < 3:
        return line
    proj = transform(lambda x, y, z=None: _TO_ITM.transform(x, y), line)
    simp = proj.simplify(tolerance_m, preserve_topology=False)
    if simp.is_empty or len(simp.coords) < 2:
        return line
    return transform(lambda x, y, z=None: _TO_WGS.transform(x, y), simp)
