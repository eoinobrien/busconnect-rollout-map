from __future__ import annotations

import datetime as _dt
import json as _json
from collections import defaultdict
from pathlib import Path

import pandas as pd
from shapely.geometry import LineString, mapping

from .category import CATEGORY_COLOURS, categorise, category_colour
from .direction_merge import merge_directions
from .frequency import high_frequency_route_ids_from_files
from .services import active_services_for_date
from .shapes import build_linestrings, representative_shape_ids
from .stops import sample_stop_indices, stops_for_active_routes


CITY_AGENCIES = {"7778019", "7778021"}
AGENCY_LABEL = {"7778019": "Dublin Bus", "7778021": "Go-Ahead"}

HIGH_FREQUENCY_THRESHOLD = 5
HIGH_FREQUENCY_HOUR = 6

# Two directions of the same route running within this many metres of
# each other are treated as the same logical corridor and rendered as
# one geometry. Outside this distance (e.g. one-way pairs on the
# Liffey quays) they stay as separate components in a MultiLineString.
DIRECTION_MERGE_THRESHOLD_M = 30.0

LEGACY_PHASE = "legacy"


def _load_rollout_phases(path: Path) -> tuple[dict, dict[str, str]]:
    if not path.exists():
        return {}, {}
    raw = _json.loads(path.read_text())
    short_to_phase: dict[str, str] = {}
    for phase_id, info in raw.items():
        for short in info.get("routes", []):
            short_to_phase[short] = phase_id
    return raw, short_to_phase


def build(
    gtfs_dir: Path,
    date_iso: str,
    with_labels: bool = False,
    rollout_phases_path: Path | None = None,
):
    """Minimal GTFS -> GeoJSON pipeline.

    For each active route on `date_iso`:
      - pick the representative shape (most frequent shape_id of trips
        active that day) for direction 0, falling back to direction 1
      - emit ONE Feature with that shape's geometry and properties:
          route_short_name, route_long_name, agency, category, colour,
          phase, direction_id

    No bundling, no offset, no smoothing, no consolidation. Each route
    appears as its own line on the map.
    """
    gtfs_dir = Path(gtfs_dir)

    if rollout_phases_path is None:
        candidate = gtfs_dir.parent / "rollout-phases.json"
        if candidate.exists():
            rollout_phases_path = candidate
    phases_meta, short_to_phase = _load_rollout_phases(
        rollout_phases_path or Path("/dev/null/missing")
    )

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
    hf_shorts: set[str] = {
        short_by_id[rid] for rid in hf_route_ids if rid in short_by_id
    }

    rep_shapes = representative_shape_ids(trips)
    needed_shape_ids = set(rep_shapes.values())

    shapes_df = pd.read_csv(
        gtfs_dir / "shapes.txt",
        dtype={"shape_id": str},
        usecols=["shape_id", "shape_pt_lat", "shape_pt_lon", "shape_pt_sequence"],
    )
    shapes_df = shapes_df[shapes_df["shape_id"].isin(needed_shape_ids)]
    lines = build_linestrings(shapes_df)

    # Emit one Feature per route. If both directions are active
    # today, merge_directions collapses them into a single geometry
    # (LineString when same-corridor, MultiLineString when divergent
    # one-way pairs).
    from shapely.geometry import LineString as _LS, MultiLineString as _MLS

    routes_for_short: dict[str, str] = {}  # short -> route_id used
    by_short_dir: dict[str, dict[int, _LS]] = defaultdict(dict)
    for (route_id, dir_id), shape_id in rep_shapes.items():
        line = lines.get(shape_id)
        if line is None:
            continue
        short = short_by_id[route_id]
        by_short_dir[short][int(dir_id)] = line
        routes_for_short.setdefault(short, route_id)

    features: list[dict] = []
    rendered_shorts: set[str] = set()
    for short in sorted(by_short_dir):
        dirs = by_short_dir[short]
        d0 = dirs.get(0)
        d1 = dirs.get(1)
        if d0 is None and d1 is None:
            continue
        if d0 is not None and d1 is not None:
            geom = merge_directions(d0, d1, threshold_m=DIRECTION_MERGE_THRESHOLD_M)
            sole_dir: int | None = None  # both directions merged
        else:
            geom = d0 if d0 is not None else d1
            sole_dir = 0 if d0 is not None else 1

        # Convert shapely -> GeoJSON dict (lists, not tuples).
        if isinstance(geom, _LS):
            geometry = {
                "type": "LineString",
                "coordinates": [list(c) for c in geom.coords],
            }
        elif isinstance(geom, _MLS):
            geometry = {
                "type": "MultiLineString",
                "coordinates": [
                    [list(c) for c in line.coords] for line in geom.geoms
                ],
            }
        else:
            continue

        route_id = routes_for_short[short]
        cat = categorise(short, high_frequency=short in hf_shorts)
        colour = category_colour(cat)
        phase = short_to_phase.get(short, LEGACY_PHASE)
        properties: dict = {
            "route_short_name": short,
            "route_long_name": long_by_id.get(route_id, ""),
            "agency": AGENCY_LABEL.get(agency_by_id.get(route_id, ""), ""),
            "category": cat,
            "colour": colour,
            "phase": phase,
        }
        # Only single-direction features carry a direction_id; merged-
        # both-directions features omit it because they represent the
        # logical corridor regardless of direction.
        if sole_dir is not None:
            properties["direction_id"] = sole_dir
        features.append({"type": "Feature", "geometry": geometry, "properties": properties})
        rendered_shorts.add(short)

    routes_geojson = {"type": "FeatureCollection", "features": features}

    phase_route_counts: dict[str, int] = defaultdict(int)
    for s in rendered_shorts:
        phase_route_counts[short_to_phase.get(s, LEGACY_PHASE)] += 1

    category_route_counts: dict[str, int] = defaultdict(int)
    for f in features:
        category_route_counts[f["properties"]["category"]] += 1

    meta = {
        "build_iso": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "reference_date": date_iso,
        "category_colours": dict(CATEGORY_COLOURS),
        "route_count": len(features),
        "active_service_count": int(len(active_services)),
        "active_services": sorted(active_services),
        "high_frequency_threshold": HIGH_FREQUENCY_THRESHOLD,
        "high_frequency_hour": HIGH_FREQUENCY_HOUR,
        "high_frequency_route_count": len(hf_shorts),
        "category_route_counts": dict(category_route_counts),
        "rollout_phases": phases_meta,
        "route_phase": {
            f["properties"]["route_short_name"]: f["properties"]["phase"]
            for f in features
        },
        "phase_route_counts": dict(phase_route_counts),
    }

    if not with_labels:
        return routes_geojson, meta

    # Optional: per-route stop labels at first/last/sampled stops.
    stops_by_route_id = stops_for_active_routes(gtfs_dir, trips)
    short_to_route_id = {
        short_by_id[rid]: rid for rid in stops_by_route_id if rid in short_by_id
    }

    label_features: list[dict] = []
    for f in features:
        short = f["properties"]["route_short_name"]
        rid = short_to_route_id.get(short)
        if rid is None:
            continue
        stops = stops_by_route_id.get(rid, [])
        if len(stops) < 2:
            continue
        idxs = sample_stop_indices(len(stops), target_k=max(2, len(stops) // 8))
        for i in idxs:
            lon, lat = stops[i]
            label_features.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [lon, lat]},
                    "properties": {
                        "route_short_name": short,
                        "category": f["properties"]["category"],
                        "colour": f["properties"]["colour"],
                        "phase": f["properties"]["phase"],
                    },
                }
            )

    labels = {"type": "FeatureCollection", "features": label_features}
    meta["label_feature_count"] = len(label_features)
    return routes_geojson, meta, labels
