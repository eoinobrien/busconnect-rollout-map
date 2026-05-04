from __future__ import annotations

import datetime as _dt
from collections import defaultdict
from pathlib import Path

import pandas as pd
import pyproj
from shapely.geometry import LineString, mapping
from shapely.ops import transform

from shapely.geometry import shape as _shape

from shapely.geometry import Point as _Point

from .anchor import anchor_to_stops
from .bundle import bundle_routes
from .category import CATEGORY_COLOURS, categorise, category_colour
from .frequency import high_frequency_route_ids_from_files
from .merge import combine_directions
from .offset import CATEGORY_OFFSET_M, offset_line
from .services import active_services_for_date
from .shapes import build_linestrings, representative_shape_ids
from .smooth import smooth_line
from .stops import sample_stop_indices, stops_for_active_routes


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
# staircase artifacts left by 2 m quantization and the centroid-jitter
# from 18 m cross-route clustering.
SMOOTH_TOLERANCE_M = 4.0

# Cross-route bundling tolerance per category. Two routes' shapes
# within this many metres of each other are bundled as a shared
# corridor even when their GTFS shape points don't coincide exactly.
# Set to 0 to fall back to tight (2 m grid) bundling for that category.
CROSS_ROUTE_TOLERANCE_M: dict[str, float] = {
    "spine": 18.0,
    "orbital": 18.0,
    "local": 18.0,
    "peak": 18.0,
    "radial": 18.0,
}


_TO_ITM = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:2157", always_xy=True)
_TO_WGS = pyproj.Transformer.from_crs("EPSG:2157", "EPSG:4326", always_xy=True)


def _project_itm(geom):
    return transform(lambda x, y, z=None: _TO_ITM.transform(x, y), geom)


_LABEL_CLUSTER_M = 60.0


def _spatial_cluster(points_itm: list[tuple[float, float]]) -> list[int]:
    """Union-find clustering: any two points within _LABEL_CLUSTER_M
    metres of each other end up in the same cluster. Returns a parallel
    list assigning each input point a cluster id (root index)."""
    if not points_itm:
        return []
    from shapely.geometry import Point
    from shapely.strtree import STRtree

    geoms = [Point(p) for p in points_itm]
    tree = STRtree(geoms)
    parent = list(range(len(points_itm)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for i, g in enumerate(geoms):
        # Buffer-then-query gets every point whose envelope overlaps;
        # filter exact distance to avoid false unions on diagonal cases.
        for j in tree.query(g.buffer(_LABEL_CLUSTER_M)):
            if j == i:
                continue
            if g.distance(geoms[j]) <= _LABEL_CLUSTER_M:
                union(i, j)
    return [find(i) for i in range(len(points_itm))]


def _label_features(
    routes_by_category: dict[str, dict[str, list[LineString]]],
    category_by_short: dict[str, str],
    stops_per_short: dict[str, list[tuple[float, float]]],
    length_per_short: dict[str, float],
) -> list[dict]:
    """Pick label points for each route from its real stop list.

    The first and last stops are always included so a route's
    advertised termini are guaranteed to carry a badge. In between we
    pick `K` evenly-spaced stops where K is roughly `length / 3 km`,
    so long radials get a few mid-route badges and short loops still
    get ≥2 (start/end). Sample points falling in the same ~33 m
    bucket across all routes are clustered into one combined badge
    listing every route serving that stop.

    Falls back to interpolation along the line if a route has no
    stops resolved (e.g. its representative trip's stop_times rows
    weren't loaded).
    """
    out: list[dict] = []

    # Cluster within each category INDEPENDENTLY. Mixing categories in
    # one bubble would mean toggling that category off in the legend
    # also hides another category's labels — which the user doesn't
    # want. So a junction served by spines + orbitals + radials
    # produces three side-by-side badges, one per category.
    for cat in CATEGORIES:
        cat_routes = routes_by_category.get(cat, {})
        if not cat_routes:
            continue

        cat_samples: list[tuple[float, float, str]] = []  # lon, lat, short
        for short, components in cat_routes.items():
            stops = stops_per_short.get(short, [])
            total_m = length_per_short.get(short, 0.0)
            # Project each stop onto the route's already-offset primary
            # line so the label sits where the line actually is, not at
            # the true road centre. The line offset alone then keeps
            # cross-category labels from stacking on shared corridors.
            primary_itm = _project_itm(components[0]) if components else None
            if stops and primary_itm is not None:
                target_k = max(2, round(total_m / LABEL_INTERVAL_M) + 1)
                for idx in sample_stop_indices(len(stops), target_k):
                    stop_lon, stop_lat = stops[idx]
                    sx, sy = _TO_ITM.transform(stop_lon, stop_lat)
                    nearest = primary_itm.interpolate(primary_itm.project(_Point(sx, sy)))
                    lon, lat = _TO_WGS.transform(nearest.x, nearest.y)
                    cat_samples.append((lon, lat, short))
            else:
                for line in components:
                    line_itm = _project_itm(line)
                    length_m = line_itm.length
                    if length_m < 50:
                        continue
                    n_samples = max(2, round(length_m / LABEL_INTERVAL_M) + 1)
                    for i in range(n_samples):
                        d = length_m * i / (n_samples - 1)
                        pt_itm = line_itm.interpolate(d)
                        lon, lat = _TO_WGS.transform(pt_itm.x, pt_itm.y)
                        cat_samples.append((lon, lat, short))

        if not cat_samples:
            continue

        points_itm = [_TO_ITM.transform(lon, lat) for lon, lat, _ in cat_samples]
        cluster_ids = _spatial_cluster(points_itm)

        clusters: dict[int, dict] = {}
        for (lon, lat, short), cid in zip(cat_samples, cluster_ids):
            entry = clusters.setdefault(
                cid,
                {"sum_lon": 0.0, "sum_lat": 0.0, "n": 0, "routes": []},
            )
            entry["sum_lon"] += lon
            entry["sum_lat"] += lat
            entry["n"] += 1
            if short not in entry["routes"]:
                entry["routes"].append(short)

        for entry in clusters.values():
            routes = sorted(entry["routes"])
            lon = entry["sum_lon"] / entry["n"]
            lat = entry["sum_lat"] / entry["n"]
            out.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [lon, lat]},
                    "properties": {
                        "routes": routes,
                        "label": ", ".join(routes),
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
    pre_offset_length_m: dict[str, float] = {}
    category_by_short: dict[str, str] = {}

    # Map a route_short_name to whether it's high-frequency by route_id.
    hf_shorts: set[str] = {
        short_by_id[rid] for rid in hf_route_ids if rid in short_by_id
    }

    # Resolve each route's first and last stop so we can extend the
    # rendered geometry to actually touch them.
    stops_by_route_id = stops_for_active_routes(gtfs_dir, trips)
    stops_per_short_full: dict[str, list[tuple[float, float]]] = {}
    for rid, coords in stops_by_route_id.items():
        short = short_by_id.get(rid)
        if short and coords:
            stops_per_short_full[short] = coords

    for short, dirs in shapes_by_route.items():
        cat = categorise(short, high_frequency=short in hf_shorts)
        category_by_short[short] = cat

        # Pass both directions to the bundle as separate components
        # under the same sub_id. The cross-route snap-to-canonical
        # bundle then handles bidirectional merging too: if dir0 and
        # dir1 sit within 18 m of each other they snap to one trunk;
        # if they diverge (e.g. one-way pair on the Liffey quays,
        # 50 m apart) they stay as two parallel canonicals. Avoids
        # combine_directions picking an inconsistent "primary"
        # across routes that ends up confusing the bundling.
        components: list[LineString] = []
        for dir_id in (0, 1):
            line = dirs.get(dir_id)
            if line is not None:
                components.append(line)
        if not components:
            continue
        pre_offset_length_m[short] = sum(
            _project_itm(c).length for c in components
        )
        offset_m = CATEGORY_OFFSET_M.get(cat, 0)
        if offset_m:
            components = [offset_line(c, offset_m) for c in components]
            stops = stops_per_short_full.get(short, [])
            if stops and len(stops) >= 2:
                anchors = [stops[0], stops[-1]]
                components = [
                    anchor_to_stops(c, anchors, max_distance_m=offset_m + 8)
                    for c in components
                ]

        routes_by_category[cat][short] = components

    # Bundle each category and emit a single Feature collection.
    all_segments: list[dict] = []
    for cat in CATEGORIES:
        routes_in_cat = routes_by_category.get(cat, {})
        if not routes_in_cat:
            continue
        tol = CROSS_ROUTE_TOLERANCE_M.get(cat, 0)
        feats = bundle_routes(routes_in_cat, tolerance_m=tol if tol > 0 else None)
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

    # Reuse the stops already resolved for line-extension above.
    stops_per_short = stops_per_short_full

    labels = {
        "type": "FeatureCollection",
        "features": _label_features(
            routes_by_category, category_by_short, stops_per_short,
            pre_offset_length_m,
        ),
    }
    meta["label_feature_count"] = len(labels["features"])
    return segments_geojson, routes_legacy, meta, labels
