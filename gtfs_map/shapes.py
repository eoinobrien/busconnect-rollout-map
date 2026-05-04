from __future__ import annotations

import pandas as pd
from shapely.geometry import LineString


def representative_shape_ids(trips: pd.DataFrame) -> dict[tuple[str, int], str]:
    """For each (route_id, direction_id), pick the most-frequent shape_id.

    Reduces ~150k trips to one shape per direction so we render a single
    representative path per route+direction.
    """
    counts = (
        trips.groupby(["route_id", "direction_id", "shape_id"])
        .size()
        .reset_index(name="n")
    )
    # idxmax of n within each (route_id, direction_id) group
    idx = counts.groupby(["route_id", "direction_id"])["n"].idxmax()
    picked = counts.loc[idx]
    return {
        (row.route_id, int(row.direction_id)): row.shape_id
        for row in picked.itertuples(index=False)
    }


def build_linestrings(shapes: pd.DataFrame) -> dict[str, LineString]:
    """Group shape points into one LineString per shape_id.

    Points are sorted by shape_pt_sequence and emitted as (lon, lat)
    pairs to match the GeoJSON axis order.
    """
    out: dict[str, LineString] = {}
    sorted_shapes = shapes.sort_values(["shape_id", "shape_pt_sequence"])
    for shape_id, group in sorted_shapes.groupby("shape_id", sort=False):
        coords = list(zip(group["shape_pt_lon"], group["shape_pt_lat"]))
        if len(coords) >= 2:
            out[shape_id] = LineString(coords)
    return out
