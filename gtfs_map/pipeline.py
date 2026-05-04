from __future__ import annotations

import datetime as _dt
from collections import defaultdict
from pathlib import Path

import pandas as pd
import pyproj
from shapely.geometry import LineString, mapping
from shapely.ops import transform

from shapely.geometry import shape as _shape

from .bundle import bundle_routes
from .category import CATEGORY_COLOURS, categorise, category_colour
from .frequency import high_frequency_route_ids_from_files
from .merge import combine_directions
from .offset import CATEGORY_OFFSET_M, offset_line
from .services import active_services_for_date
from .shapes import build_linestrings, representative_shape_ids
from .smooth import smooth_line


CITY_AGENCIES = {"7778019", "7778021"}
AGENCY_LABEL = {
    "7778019": "Dublin Bus",
    "7778021": "Go-Ahead",
}

CATEGORIES = ("spine", "orbital", "local", "peak", "radial")

HIGH_FREQUENCY_THRESHOLD = 5
HIGH_FREQUENCY_HOUR = 8

# Within this distance the inbound and outbound shapes of a route are
# treated as the same road and merged into one line.
DIRECTION_MERGE_THRESHOLD_M = 30.0

# Approximate spacing between mid-route badges (in metres). Termini are
# always sampled regardless.
LABEL_INTERVAL_M = 3000.0

# Douglas-Peucker tolerance applied after bundling to clean up the
# staircase artifacts left by 2 m quantization.
SMOOTH_TOLERANCE_M = 3.0


_TO_ITM = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:2157", always_xy=True)
_TO_WGS = pyproj.Transformer.from_crs("EPSG:2157", "EPSG:4326", always_xy=True)


def _project_itm(geom):
    return transform(lambda x, y, z=None: _TO_ITM.transform(x, y), geom)


def _label_features(
    routes_by_category: dict[str, dict[str, list[LineString]]],
    category_by_short: dict[str, str],
) -> list[dict]:
    """Sample each route's geometry at termini + every ~LABEL_INTERVAL_M
    metres, then cluster sample points falling in the same ~30 m bucket
    so a busy intersection shared by N routes shows one combined badge
    instead of N stacked ones.
    """
    bucket_grid = 0.0003  # ~33 m at Dublin's latitude
    bucket: dict[tuple[float, float], dict] = {}

    def _add(lon: float, lat: float, short: str, cat: str) -> None:
        key = (
            round(lon / bucket_grid) * bucket_grid,
            round(lat / bucket_grid) * bucket_grid,
        )
        entry = bucket.setdefault(
            key,
            {"lon": lon, "lat": lat, "routes": [], "categories": set()},
        )
        if short not in entry["routes"]:
            entry["routes"].append(short)
            entry["categories"].add(cat)

    for cat in CATEGORIES:
        for short, components in routes_by_category.get(cat, {}).items():
            for line in components:
                line_itm = _project_itm(line)
                length_m = line_itm.length
                if length_m < 50:
                    # Skip ultra-short residuals — labels would just clutter
                    continue
                n_samples = max(2, round(length_m / LABEL_INTERVAL_M) + 1)
                for i in range(n_samples):
                    d = length_m * i / (n_samples - 1)
                    pt_itm = line_itm.interpolate(d)
                    lon, lat = _TO_WGS.transform(pt_itm.x, pt_itm.y)
                    _add(lon, lat, short, cat)

    out: list[dict] = []
    for entry in bucket.values():
        # Pick the most "important" category present here for the badge colour.
        cat = next(
            (c for c in CATEGORIES if c in entry["categories"]),
            "radial",
        )
        # Sort: spines first, then alphanumeric.
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

    # Group rep shapes by route_short_name + direction.
    shapes_by_route: dict[str, dict[int, LineString]] = defaultdict(dict)
    for (route_id, dir_id), shape_id in rep_shapes.items():
        line = lines.get(shape_id)
        if line is None:
            continue
        short = short_by_id[route_id]
        shapes_by_route[short][dir_id] = line
        # Side-effect: cache mapping for HF / agency lookups.

    # Combine inbound + outbound where they're within 30 m of each other.
    routes_by_category: dict[str, dict[str, list[LineString]]] = defaultdict(dict)
    category_by_short: dict[str, str] = {}

    # Map a route_short_name to whether it's high-frequency by route_id.
    hf_shorts: set[str] = {
        short_by_id[rid] for rid in hf_route_ids if rid in short_by_id
    }

    for short, dirs in shapes_by_route.items():
        cat = categorise(short, high_frequency=short in hf_shorts)
        category_by_short[short] = cat
        primary = dirs.get(0) or dirs.get(1)
        secondary = dirs.get(1) if primary is dirs.get(0) else None
        components = combine_directions(
            primary, secondary, threshold_m=DIRECTION_MERGE_THRESHOLD_M
        )
        # Apply per-category perpendicular offset so a corridor served
        # by routes of different classes shows them as parallel
        # neighbours rather than over-painting one another.
        offset_m = CATEGORY_OFFSET_M.get(cat, 0)
        if offset_m:
            components = [offset_line(c, offset_m) for c in components]
        routes_by_category[cat][short] = components

    # Bundle each category and emit a single Feature collection.
    all_segments: list[dict] = []
    for cat in CATEGORIES:
        routes_in_cat = routes_by_category.get(cat, {})
        if not routes_in_cat:
            continue
        feats = bundle_routes(routes_in_cat)
        colour = category_colour(cat)
        for f in feats:
            # Smooth out staircase from 2 m bundling grid.
            geom = _shape(f["geometry"])
            smoothed = smooth_line(geom, tolerance_m=SMOOTH_TOLERANCE_M)
            f["geometry"] = {
                "type": "LineString",
                "coordinates": [list(c) for c in smoothed.coords],
            }
            f["properties"]["category"] = cat
            f["properties"]["colour"] = colour
        all_segments.extend(feats)

    segments_geojson = {"type": "FeatureCollection", "features": all_segments}
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
        "high_frequency_route_count": len(hf_shorts),
        "category_route_counts": {
            cat: len(routes_by_category.get(cat, {})) for cat in CATEGORIES
        },
        "segment_feature_count": len(all_segments),
        "direction_merge_threshold_m": DIRECTION_MERGE_THRESHOLD_M,
        "label_interval_m": LABEL_INTERVAL_M,
    }

    if not with_labels:
        return segments_geojson, routes_legacy, meta

    labels = {
        "type": "FeatureCollection",
        "features": _label_features(routes_by_category, category_by_short),
    }
    meta["label_feature_count"] = len(labels["features"])
    return segments_geojson, routes_legacy, meta, labels
