"""Bundling primitives.

share_corridor(a, b, tolerance_m, overlap_threshold) is the only
function for now. Higher-level grouping comes in later steps once
the primitive is solid.
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
