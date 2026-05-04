from __future__ import annotations

import datetime as _dt
import json as _json
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

LEGACY_PHASE = "legacy"


def _load_rollout_phases(path: Path) -> tuple[dict, dict[str, str]]:
    """Read rollout-phases.json. Returns (phases_meta, short_to_phase).

    phases_meta:    {phase_id: {"date": iso, "routes": [...]}, ...}
    short_to_phase: {route_short_name: phase_id}
    """
    if not path.exists():
        return {}, {}
    raw = _json.loads(path.read_text())
    short_to_phase: dict[str, str] = {}
    for phase_id, info in raw.items():
        for short in info.get("routes", []):
            short_to_phase[short] = phase_id
    return raw, short_to_phase
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

# Douglas-Peucker tolerance applied to each canonical edge after the
# global bundle. With a single shared canonical there's no per-category
# drift to flatten, so we can simplify aggressively without re-
# introducing jitter — anything below this is collinear-ish enough to
# drop without the human eye noticing.
SMOOTH_TOLERANCE_M = 4.0

# Tolerance used by the single GLOBAL bundle across every route in the
# city. Routes whose densified, projected points sit within this many
# metres of each other share canonical edges. Tuned wider than typical
# road width (~10 m) to absorb GTFS shape sampling noise (~5-10 m) and
# bus lane offsets (~3-5 m), but tighter than typical one-way pair
# spacing (Dublin's quays at ~50 m) so different streets stay distinct.
GLOBAL_BUNDLE_TOLERANCE_M = 25.0


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
    short_to_phase: dict[str, str],
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
            phases = sorted({
                short_to_phase.get(r, LEGACY_PHASE) for r in routes
            })
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
                        "phases": phases,
                    },
                }
            )
    return out


def build(
    gtfs_dir: Path,
    date_iso: str,
    with_labels: bool = False,
    rollout_phases_path: Path | None = None,
):
    """Run the full GTFS -> GeoJSON pipeline.

    Returns:
      with_labels=False (default): (segments, _, meta)
      with_labels=True:             (segments, _, meta, labels)
    """
    gtfs_dir = Path(gtfs_dir)

    if rollout_phases_path is None:
        # Best-effort: look for it next to gtfs/ or in the parent dir.
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

    # Build a route_short_name -> category mapping and collect each
    # route's TRUE-position geometry (no offset, no anchor). The
    # global bundle below works on real road positions; offset is
    # purely a render-time concern applied at the final split step.
    all_routes_by_short: dict[str, list[LineString]] = {}
    for short, dirs in shapes_by_route.items():
        cat = categorise(short, high_frequency=short in hf_shorts)
        category_by_short[short] = cat
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
        all_routes_by_short[short] = components

    # ---- Stage 1: global canonical road graph ----------------------------
    # One bundle pass across every route, regardless of category. Routes
    # within tolerance share the SAME canonical edge — the bundled
    # geometry is type-agnostic, eliminating per-category drift.
    canonical_features = bundle_routes(
        all_routes_by_short,
        tolerance_m=GLOBAL_BUNDLE_TOLERANCE_M,
    )

    # Smooth each canonical edge once. With a global graph there's no
    # per-category centroid wobble, so a tighter tolerance preserves
    # detail without re-introducing jitter.
    for f in canonical_features:
        geom = _shape(f["geometry"])
        smoothed = smooth_line(geom, tolerance_m=SMOOTH_TOLERANCE_M)
        f["geometry"] = {
            "type": "LineString",
            "coordinates": [list(c) for c in smoothed.coords],
        }

    # ---- Stage 2: per-category split with render-time offset -------------
    # For each canonical edge, emit one Feature per category present.
    # Each feature gets that category's perpendicular offset applied
    # to the canonical geometry so the rendered map shows parallel
    # lines on shared corridors instead of one line covering many.
    all_segments: list[dict] = []
    for f in canonical_features:
        full_routes = list(f["properties"]["route_set"])
        cats_present = sorted({
            category_by_short[r] for r in full_routes if r in category_by_short
        })
        canonical_geom = _shape(f["geometry"])

        for cat in cats_present:
            offset_m = CATEGORY_OFFSET_M.get(cat, 0)
            if offset_m:
                offset_geom = offset_line(canonical_geom, offset_m)
                geom_dict = {
                    "type": "LineString",
                    "coordinates": [list(c) for c in offset_geom.coords],
                }
            else:
                geom_dict = f["geometry"]
            cat_routes = sorted(
                r for r in full_routes if category_by_short.get(r) == cat
            )
            phases = sorted({
                short_to_phase.get(r, LEGACY_PHASE) for r in cat_routes
            })
            all_segments.append(
                {
                    "type": "Feature",
                    "geometry": geom_dict,
                    "properties": {
                        "category": cat,
                        "colour": category_colour(cat),
                        "route_set": cat_routes,
                        "full_route_set": sorted(full_routes),
                        "route_count": len(cat_routes),
                        "kind": "shared" if len(cat_routes) >= 2 else "single",
                        "phases": phases,
                    },
                }
            )

    # Per-category routes_by_category for label generation. Labels
    # project onto the OFFSET geometry per category, so they sit on
    # the same parallel line the user sees rendered.
    routes_by_category: dict[str, dict[str, list[LineString]]] = defaultdict(dict)
    for short, components in all_routes_by_short.items():
        cat = category_by_short.get(short, "radial")
        offset_m = CATEGORY_OFFSET_M.get(cat, 0)
        if offset_m:
            offset_components = [offset_line(c, offset_m) for c in components]
            stops = stops_per_short_full.get(short, [])
            if stops and len(stops) >= 2:
                anchors = [stops[0], stops[-1]]
                offset_components = [
                    anchor_to_stops(c, anchors, max_distance_m=offset_m + 8)
                    for c in offset_components
                ]
            routes_by_category[cat][short] = offset_components
        else:
            routes_by_category[cat][short] = components

    segments_geojson = {"type": "FeatureCollection", "features": all_segments}
    routes_legacy = {"type": "FeatureCollection", "features": []}

    # Phase route counts (only of routes actually rendered today).
    rendered_shorts = {
        s for cat in CATEGORIES for s in routes_by_category.get(cat, {})
    }
    phase_route_counts: dict[str, int] = defaultdict(int)
    for s in rendered_shorts:
        phase_route_counts[short_to_phase.get(s, LEGACY_PHASE)] += 1

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
        # Rollout-phase metadata: phases dict + per-route-short mapping
        # so the viewer can drive a phase-highlight filter.
        "rollout_phases": phases_meta,
        "route_phase": {s: short_to_phase.get(s, LEGACY_PHASE) for s in rendered_shorts},
        "phase_route_counts": dict(phase_route_counts),
    }

    if not with_labels:
        return segments_geojson, routes_legacy, meta

    # Reuse the stops already resolved for line-extension above.
    stops_per_short = stops_per_short_full

    labels = {
        "type": "FeatureCollection",
        "features": _label_features(
            routes_by_category, category_by_short, stops_per_short,
            pre_offset_length_m, short_to_phase,
        ),
    }
    meta["label_feature_count"] = len(labels["features"])
    return segments_geojson, routes_legacy, meta, labels
