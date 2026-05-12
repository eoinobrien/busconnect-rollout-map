from __future__ import annotations

import datetime as _dt
import json as _json
import re
from collections import defaultdict
from pathlib import Path

import pandas as pd
import pyproj
from shapely.geometry import LineString

from .category import CATEGORY_COLOURS, categorise, category_colour
from .direction_merge import merge_directions
from .frequency import high_frequency_route_ids_from_files
from .services import active_services_for_date
from .shapes import build_linestrings, representative_shape_ids


CITY_AGENCIES = {"7778019", "7778021"}
AGENCY_LABEL = {"7778019": "Dublin Bus", "7778021": "Go-Ahead"}

HIGH_FREQUENCY_THRESHOLD = 4
HIGH_FREQUENCY_HOUR = 12

# Two directions of the same route running within this many metres of
# each other are treated as the same logical corridor and rendered as
# one geometry. Outside this distance (e.g. one-way pairs on the
# Liffey quays) they stay as separate components in a MultiLineString.
DIRECTION_MERGE_THRESHOLD_M = 30.0

LEGACY_PHASE = "legacy"

# Per-category perpendicular offset. Each route stays its own
# feature (no segment bundling), so cross-category sharing of a
# metre of road would otherwise stack five colours on top of one
# another and only the front one would be visible. We push each
# category into its own slot in metres: spine on the centerline,
# orbital + radial just off either side, peak + local further out.
# Routes within the same category still overlap perfectly, which is
# fine - the category colour reads as a single line through the
# whole shared stretch.
OFFSET_SPACING_M = 6.0
_CATEGORY_SLOT: dict[str, int] = {
    "spine": 0,
    "orbital": 1,
    "radial": -1,
    "peak": 2,
    "local": -2,
}


_OFFSET_TO_ITM = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:2157", always_xy=True)
_OFFSET_TO_WGS = pyproj.Transformer.from_crs("EPSG:2157", "EPSG:4326", always_xy=True)


def _offset_line(line_wgs: LineString, offset_m: float) -> LineString:
    """Perpendicular-offset a WGS LineString by `offset_m` metres.

    Projects to ITM for the offset (so the distance is in real
    metres regardless of latitude), then projects back. Sign
    convention follows Shapely: positive offsets to the left of
    the line direction, negative to the right.
    """
    if offset_m == 0 or len(line_wgs.coords) < 2:
        return line_wgs
    coords_itm = [_OFFSET_TO_ITM.transform(x, y) for x, y in line_wgs.coords]
    line_itm = LineString(coords_itm)
    if line_itm.length < 1.0:
        return line_wgs
    try:
        offset_itm = line_itm.offset_curve(offset_m)
    except Exception:
        return line_wgs
    if offset_itm.is_empty:
        return line_wgs
    if offset_itm.geom_type == "MultiLineString":
        # offset_curve can split at sharp bends; keep the longest
        # piece so the rendered route stays mostly continuous.
        offset_itm = max(offset_itm.geoms, key=lambda g: g.length)
    if not isinstance(offset_itm, LineString):
        return line_wgs
    return LineString([_OFFSET_TO_WGS.transform(x, y) for x, y in offset_itm.coords])


_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def is_future_date(date_value) -> bool:
    """True when a rollout-phases.json entry has no ISO calendar date.

    "future", "unknown", "planned", "tbd" and any other non-YYYY-MM-DD
    value all count as future. Used to keep future phases from
    reassigning a live route's phase tag: route 80 stays in phase 7
    even though it appears in phase 10 (Potential)'s `routes` list.
    """
    return not _ISO_DATE_RE.match(str(date_value or ""))


def _load_rollout_phases(path: Path) -> tuple[dict, dict[str, str]]:
    if not path.exists():
        return {}, {}
    raw = _json.loads(path.read_text())
    short_to_phase: dict[str, str] = {}
    for phase_id, info in raw.items():
        if is_future_date(info.get("date")):
            # Future phases describe planned route compositions, not
            # current GTFS-shape phase membership; skip them here so
            # live routes keep their introducing live phase.
            continue
        for short in info.get("routes", []):
            short_to_phase[short] = phase_id
    return raw, short_to_phase


def build(
    gtfs_dir: Path,
    date_iso: str,
    rollout_phases_path: Path | None = None,
    *,
    offset_categories: bool = False,
):
    """GTFS -> per-route GeoJSON LineString features.

    For each active route on `date_iso` we pick the most-frequent
    shape per direction and clean fold-back vertices. We emit one
    Feature per (route, direction), each carrying a singleton
    `routes` list so the front-end can toggle each route
    independently. When `offset_categories` is set, non-spine
    categories are pushed onto perpendicular slots so cross-category
    overlaps render as parallel lines instead of stacking.

    Feature properties: route, routes, route_long_name, agency,
    category, colour, phase, direction_id.
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

    # Per route: pick the geometry to render (merge_directions when
    # both directions are active) and a single LineString to feed into
    # the corridor-bundler. Cross-route bundling then groups routes
    # within each category whose corridor matches.
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

    # One Feature per (route, direction). Each route's two-direction
    # GTFS shapes get merged-direction cleanup; tight pairs collapse to
    # one cleaned geometry, Liffey-style one-way pairs stay as two.
    # Every emitted feature carries a SINGLETON `routes` list so the
    # front-end can toggle each route independently - the bundling
    # that used to merge same-corridor routes into one feature was
    # removed because runtime filters (future-phase replacement,
    # per-route search hides) need single-route granularity. Same-
    # category routes still overlap visually at shared corridors;
    # cross-category overlap is broken up by the perpendicular slot
    # offset below.
    short_to_category: dict[str, str] = {}
    features: list[dict] = []
    rendered_shorts: set[str] = set()

    for short in sorted(by_short_dir):
        dirs = by_short_dir[short]
        d0 = dirs.get(0)
        d1 = dirs.get(1)
        if d0 is None and d1 is None:
            continue
        if d0 is not None and d1 is not None:
            merged = merge_directions(d0, d1, threshold_m=DIRECTION_MERGE_THRESHOLD_M)
            if isinstance(merged, _MLS):
                cleaned_d0, cleaned_d1 = merged.geoms[0], merged.geoms[1]
            else:
                cleaned_d0, cleaned_d1 = merged, None
        elif d0 is not None:
            cleaned_d0 = merge_directions(d0, None)
            cleaned_d1 = None
        else:
            cleaned_d0 = None
            cleaned_d1 = merge_directions(d1, None)

        cat = categorise(short, high_frequency=short in hf_shorts)
        short_to_category[short] = cat
        route_id = routes_for_short[short]
        colour = category_colour(cat)
        phase = short_to_phase.get(short, LEGACY_PHASE)
        slot = _CATEGORY_SLOT.get(cat, 0)

        for direction_id, line in ((0, cleaned_d0), (1, cleaned_d1)):
            if line is None:
                continue
            if offset_categories and slot != 0:
                line = _offset_line(line, slot * OFFSET_SPACING_M)
            features.append({
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": [list(c) for c in line.coords],
                },
                "properties": {
                    "route": short,
                    "routes": [short],
                    "route_long_name": long_by_id.get(route_id, ""),
                    "agency": AGENCY_LABEL.get(
                        agency_by_id.get(route_id, ""), ""
                    ),
                    "category": cat,
                    "colour": colour,
                    "phase": phase,
                    "direction_id": direction_id,
                },
            })
            rendered_shorts.add(short)

    features.sort(
        key=lambda f: (
            f["properties"]["category"],
            f["properties"]["route"],
            f["properties"].get("direction_id", 0),
        )
    )
    routes_geojson = {"type": "FeatureCollection", "features": features}

    phase_route_counts: dict[str, int] = defaultdict(int)
    for s in rendered_shorts:
        phase_route_counts[short_to_phase.get(s, LEGACY_PHASE)] += 1

    # Unique routes per category. Each route is counted once under
    # its own category (not the walker's), so a bundle that mixes a
    # HF-promoted spine 39 with its radial variant 39A counts 39 in
    # spine and 39A in radial.
    routes_by_category: dict[str, set[str]] = defaultdict(set)
    for s in rendered_shorts:
        routes_by_category[short_to_category[s]].add(s)
    category_route_counts = {k: len(v) for k, v in routes_by_category.items()}

    meta = {
        "build_iso": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "reference_date": date_iso,
        "category_colours": dict(CATEGORY_COLOURS),
        "route_count": len(rendered_shorts),
        "feature_count": len(features),
        "active_service_count": int(len(active_services)),
        "active_services": sorted(active_services),
        "high_frequency_threshold": HIGH_FREQUENCY_THRESHOLD,
        "high_frequency_hour": HIGH_FREQUENCY_HOUR,
        "high_frequency_route_count": len(hf_shorts),
        "category_route_counts": dict(category_route_counts),
        "rollout_phases": phases_meta,
        "route_phase": {s: short_to_phase.get(s, LEGACY_PHASE) for s in rendered_shorts},
        "phase_route_counts": dict(phase_route_counts),
    }

    return routes_geojson, meta
