from __future__ import annotations

import datetime as _dt
from collections import defaultdict
from pathlib import Path

import pandas as pd
from shapely.geometry import mapping

from .bundle import bundle_spine
from .category import CATEGORY_COLOURS, categorise, category_colour
from .classify import classify_route
from .services import active_services_for_date
from .shapes import build_linestrings, representative_shape_ids


CITY_AGENCIES = {"7778019", "7778021"}
AGENCY_LABEL = {
    "7778019": "Dublin Bus",
    "7778021": "Go-Ahead",
}


def _label_features(
    rep_shapes: dict[tuple[str, int], str],
    lines: dict,
    short_by_id: dict[str, str],
    long_by_id: dict[str, str],
    agency_by_id: dict[str, str],
) -> list[dict]:
    """One label point at each end of each route's representative line.

    Generated *before* bundling so we get a label per sub-route at its
    own terminus, even where the trunk is shared with other sub-routes.
    """
    out: list[dict] = []
    seen: set[tuple[str, tuple[float, float]]] = set()
    for (route_id, dir_id), shape_id in rep_shapes.items():
        line = lines.get(shape_id)
        if line is None:
            continue
        short = short_by_id[route_id]
        cat = categorise(short)
        colour = category_colour(cat)
        coords = list(line.coords)
        endpoints = [coords[0], coords[-1]]
        for end in endpoints:
            # Dedupe identical labels from the two directions of the
            # same route landing on the same terminus.
            key = (short, (round(end[0], 5), round(end[1], 5)))
            if key in seen:
                continue
            seen.add(key)
            out.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [end[0], end[1]]},
                    "properties": {
                        "route_short_name": short,
                        "route_long_name": long_by_id.get(route_id, ""),
                        "agency": AGENCY_LABEL.get(agency_by_id.get(route_id, ""), ""),
                        "category": cat,
                        "colour": colour,
                    },
                }
            )
    return out


def build(
    gtfs_dir: Path, date_iso: str, with_labels: bool = False
):
    """Run the full GTFS -> GeoJSON pipeline.

    Returns:
      with_labels=False (default): (spines, routes, meta)
      with_labels=True:             (spines, routes, meta, labels)
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
        low_memory=False,
    )
    if "direction_id" in trips.columns:
        trips["direction_id"] = trips["direction_id"].fillna(0).astype(int)
    else:
        trips["direction_id"] = 0
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
            if existing is None or dir_id == 0:
                spine_lines[letter][short] = line
        else:
            cat = categorise(short)
            other_features.append(
                {
                    "type": "Feature",
                    "geometry": mapping(line),
                    "properties": {
                        "route_short_name": short,
                        "route_long_name": long_by_id[route_id],
                        "agency": AGENCY_LABEL[agency_by_id[route_id]],
                        "direction_id": int(dir_id),
                        "category": cat,
                        "colour": category_colour(cat),
                    },
                }
            )

    spine_features: list[dict] = []
    for letter in sorted(spine_lines):
        feats = bundle_spine(spine_lines[letter])
        for f in feats:
            f["properties"]["spine"] = letter
            f["properties"]["category"] = "spine"
            f["properties"]["colour"] = category_colour("spine")
        spine_features.extend(feats)

    spines_geojson = {"type": "FeatureCollection", "features": spine_features}
    routes_geojson = {"type": "FeatureCollection", "features": other_features}

    meta = {
        "build_iso": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "reference_date": date_iso,
        "category_colours": dict(CATEGORY_COLOURS),
        "route_count": int(len(routes_df)),
        "active_service_count": int(len(active_services)),
        "active_services": sorted(active_services),
        "spine_letters_present": sorted(spine_lines),
        "other_feature_count": len(other_features),
        "spine_feature_count": len(spine_features),
    }

    if not with_labels:
        return spines_geojson, routes_geojson, meta

    labels = {
        "type": "FeatureCollection",
        "features": _label_features(
            rep_shapes, lines, short_by_id, long_by_id, agency_by_id
        ),
    }
    meta["label_feature_count"] = len(labels["features"])
    return spines_geojson, routes_geojson, meta, labels
