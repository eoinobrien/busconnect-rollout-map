from __future__ import annotations

import datetime as _dt
from collections import defaultdict
from pathlib import Path

import pandas as pd
from shapely.geometry import mapping

from .bundle import bundle_spine
from .classify import classify_route
from .colour import SPINE_COLOURS, route_colour, spine_colour
from .services import active_services_for_date
from .shapes import build_linestrings, representative_shape_ids


CITY_AGENCIES = {"7778019", "7778021"}
AGENCY_LABEL = {
    "7778019": "Dublin Bus",
    "7778021": "Go-Ahead",
}


def build(
    gtfs_dir: Path, date_iso: str
) -> tuple[dict, dict, dict]:
    """Run the full GTFS -> GeoJSON pipeline.

    Returns three dicts:
      - spines: GeoJSON FeatureCollection of bundled spine segments
      - routes: GeoJSON FeatureCollection of non-spine route lines
      - meta:   build metadata (date, palette, counts)
    """
    gtfs_dir = Path(gtfs_dir)

    with open(gtfs_dir / "calendar.txt") as cal, open(
        gtfs_dir / "calendar_dates.txt"
    ) as cal_dates:
        active_services = active_services_for_date(cal, cal_dates, date_iso)

    routes_df = pd.read_csv(gtfs_dir / "routes.txt", dtype=str)
    routes_df = routes_df[routes_df["agency_id"].isin(CITY_AGENCIES)].copy()
    short_by_id = dict(zip(routes_df["route_id"], routes_df["route_short_name"]))
    long_by_id = dict(
        zip(routes_df["route_id"], routes_df["route_long_name"].fillna(""))
    )
    agency_by_id = dict(zip(routes_df["route_id"], routes_df["agency_id"]))
    kept_route_ids = set(short_by_id)

    trips = pd.read_csv(
        gtfs_dir / "trips.txt",
        dtype={"route_id": str, "service_id": str, "shape_id": str},
    )
    trips["direction_id"] = (
        trips["direction_id"].fillna(0).astype(int)
        if "direction_id" in trips.columns
        else 0
    )
    trips = trips[
        trips["route_id"].isin(kept_route_ids)
        & trips["service_id"].isin(active_services)
    ]

    rep_shapes = representative_shape_ids(trips)
    needed_shape_ids = set(rep_shapes.values())

    shapes_df = pd.read_csv(
        gtfs_dir / "shapes.txt",
        dtype={"shape_id": str},
        usecols=[
            "shape_id",
            "shape_pt_lat",
            "shape_pt_lon",
            "shape_pt_sequence",
        ],
    )
    shapes_df = shapes_df[shapes_df["shape_id"].isin(needed_shape_ids)]
    lines = build_linestrings(shapes_df)

    # Group: spines keep one shape per sub-route (prefer direction 0);
    # other routes keep every (route, direction) so loops/asymmetries
    # both render.
    spine_lines: dict[str, dict[str, object]] = defaultdict(dict)
    other_features: list[dict] = []

    for (route_id, dir_id), shape_id in rep_shapes.items():
        line = lines.get(shape_id)
        if line is None:
            continue
        short = short_by_id[route_id]
        kind, letter = classify_route(short)
        if kind == "spine":
            existing = spine_lines[letter].get(short)
            # Prefer direction 0 if both directions appear.
            if existing is None or dir_id == 0:
                spine_lines[letter][short] = line
        else:
            other_features.append(
                {
                    "type": "Feature",
                    "geometry": mapping(line),
                    "properties": {
                        "route_short_name": short,
                        "route_long_name": long_by_id[route_id],
                        "agency": AGENCY_LABEL[agency_by_id[route_id]],
                        "direction_id": int(dir_id),
                        "colour": route_colour(short),
                    },
                }
            )

    spine_features: list[dict] = []
    for letter in sorted(spine_lines):
        feats = bundle_spine(spine_lines[letter])
        for f in feats:
            f["properties"]["spine"] = letter
            f["properties"]["colour"] = spine_colour(letter)
        spine_features.extend(feats)

    spines_geojson = {"type": "FeatureCollection", "features": spine_features}
    routes_geojson = {"type": "FeatureCollection", "features": other_features}

    meta = {
        "build_iso": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "reference_date": date_iso,
        "spine_colours": dict(SPINE_COLOURS),
        "route_count": int(len(routes_df)),
        "active_service_count": int(len(active_services)),
        "active_services": sorted(active_services),
        "spine_letters_present": sorted(spine_lines),
        "other_feature_count": len(other_features),
        "spine_feature_count": len(spine_features),
    }
    return spines_geojson, routes_geojson, meta
