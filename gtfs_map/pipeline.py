from __future__ import annotations

import datetime as _dt
from collections import defaultdict
from pathlib import Path

import pandas as pd
from shapely.geometry import mapping

from .bundle import bundle_routes
from .category import CATEGORY_COLOURS, categorise, category_colour
from .frequency import high_frequency_route_ids_from_files
from .services import active_services_for_date
from .shapes import build_linestrings, representative_shape_ids


CITY_AGENCIES = {"7778019", "7778021"}
AGENCY_LABEL = {
    "7778019": "Dublin Bus",
    "7778021": "Go-Ahead",
}

CATEGORIES = ("spine", "orbital", "local", "peak", "radial")

# Frequency threshold for promoting a route to "spine" category (red).
HIGH_FREQUENCY_THRESHOLD = 5
HIGH_FREQUENCY_HOUR = 8


def _label_features(
    rep_shapes: dict[tuple[str, int], str],
    lines: dict,
    short_by_id: dict[str, str],
    long_by_id: dict[str, str],
    agency_by_id: dict[str, str],
    category_by_short: dict[str, str],
) -> list[dict]:
    """One label point at each end of each route's representative line.

    Generated *before* bundling so we get a label per route at its own
    terminus, even where the trunk is shared with other routes.
    Endpoints landing on the same ~30 m bucket are clustered into one
    badge that lists all routes terminating there.
    """
    bucket_grid = 0.0003  # ~33 m at Dublin's latitude
    bucket: dict[tuple[float, float], dict] = {}

    for (route_id, dir_id), shape_id in rep_shapes.items():
        line = lines.get(shape_id)
        if line is None:
            continue
        short = short_by_id[route_id]
        cat = category_by_short.get(short, categorise(short))
        coords = list(line.coords)
        for end in (coords[0], coords[-1]):
            key = (round(end[0] / bucket_grid) * bucket_grid,
                   round(end[1] / bucket_grid) * bucket_grid)
            entry = bucket.setdefault(
                key,
                {
                    "lon": end[0],
                    "lat": end[1],
                    "routes": [],
                    "categories": set(),
                },
            )
            if short not in entry["routes"]:
                entry["routes"].append(short)
                entry["categories"].add(cat)

    out: list[dict] = []
    for entry in bucket.values():
        # Pick the most "important" category present at this terminus
        # (spine > orbital > local > peak > radial) for the badge colour.
        cat = next((c for c in CATEGORIES if c in entry["categories"]), "radial")
        # Sort routes so spines first, then alphanumeric
        routes = sorted(
            entry["routes"],
            key=lambda r: (CATEGORIES.index(category_by_short.get(r, "radial")), r),
        )
        out.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [entry["lon"], entry["lat"]],
                },
                "properties": {
                    "routes": routes,
                    # Show first 3 routes joined; if more, append "+N".
                    "label": (
                        ", ".join(routes[:3])
                        + (f" +{len(routes) - 3}" if len(routes) > 3 else "")
                    ),
                    "route_count": len(routes),
                    "category": cat,
                    "colour": category_colour(cat),
                },
            }
        )
    return out


def build(
    gtfs_dir: Path, date_iso: str, with_labels: bool = False
):
    """Run the full GTFS -> GeoJSON pipeline.

    Returns:
      with_labels=False (default): (segments, _, meta)
      with_labels=True:             (segments, _, meta, labels)
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

    # High-frequency promotion: routes with >=5 trip-starts at peak hour.
    active_trip_ids = set(trips["trip_id"])
    if (gtfs_dir / "stop_times.txt").exists() and active_trip_ids:
        hf_route_ids = high_frequency_route_ids_from_files(
            gtfs_dir,
            active_trip_ids,
            threshold=HIGH_FREQUENCY_THRESHOLD,
            hour=HIGH_FREQUENCY_HOUR,
        )
    else:
        hf_route_ids = set()

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

    # Collect one representative line per (route_short_name) per category,
    # preferring direction 0. Bundling operates on route names so the
    # left/right direction lines of the same route don't get treated as
    # two separate routes.
    per_category: dict[str, dict[str, object]] = defaultdict(dict)
    category_by_short: dict[str, str] = {}

    for (route_id, dir_id), shape_id in rep_shapes.items():
        line = lines.get(shape_id)
        if line is None:
            continue
        short = short_by_id[route_id]
        cat = categorise(short, high_frequency=route_id in hf_route_ids)
        category_by_short[short] = cat
        existing = per_category[cat].get(short)
        if existing is None or dir_id == 0:
            per_category[cat][short] = line

    # Bundle each category and emit a single Feature collection.
    all_segments: list[dict] = []
    for cat in CATEGORIES:
        routes_in_cat = per_category.get(cat, {})
        if not routes_in_cat:
            continue
        feats = bundle_routes(routes_in_cat)
        colour = category_colour(cat)
        for f in feats:
            f["properties"]["category"] = cat
            f["properties"]["colour"] = colour
        all_segments.extend(feats)

    segments_geojson = {"type": "FeatureCollection", "features": all_segments}
    # Keep the legacy `routes` slot empty: the viewer reads from
    # segments_geojson now. Returned as an empty FC for compat.
    routes_legacy = {"type": "FeatureCollection", "features": []}

    meta = {
        "build_iso": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "reference_date": date_iso,
        "category_colours": dict(CATEGORY_COLOURS),
        "route_count": int(len(routes_df)),
        "active_service_count": int(len(active_services)),
        "active_services": sorted(active_services),
        "high_frequency_threshold": HIGH_FREQUENCY_THRESHOLD,
        "high_frequency_hour": HIGH_FREQUENCY_HOUR,
        "high_frequency_route_count": len(hf_route_ids),
        "category_route_counts": {
            cat: len(per_category.get(cat, {})) for cat in CATEGORIES
        },
        "segment_feature_count": len(all_segments),
    }

    if not with_labels:
        return segments_geojson, routes_legacy, meta

    labels = {
        "type": "FeatureCollection",
        "features": _label_features(
            rep_shapes, lines, short_by_id, long_by_id, agency_by_id,
            category_by_short,
        ),
    }
    meta["label_feature_count"] = len(labels["features"])
    return segments_geojson, routes_legacy, meta, labels
