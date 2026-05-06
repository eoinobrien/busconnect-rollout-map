from __future__ import annotations

import datetime as _dt
import json as _json
import re
from collections import defaultdict
from pathlib import Path

import pandas as pd
from shapely.geometry import LineString, mapping

from .category import CATEGORY_COLOURS, categorise, category_colour
from .direction_merge import merge_directions
from .frequency import high_frequency_route_ids_from_files
from .segment_bundle import segment_bundle
from .services import active_services_for_date
from .shapes import build_linestrings, representative_shape_ids
from .stops import sample_stop_indices, stops_for_active_routes


CITY_AGENCIES = {"7778019", "7778021"}
AGENCY_LABEL = {"7778019": "Dublin Bus", "7778021": "Go-Ahead"}

HIGH_FREQUENCY_THRESHOLD = 5
HIGH_FREQUENCY_HOUR = 12

# Two directions of the same route running within this many metres of
# each other are treated as the same logical corridor and rendered as
# one geometry. Outside this distance (e.g. one-way pairs on the
# Liffey quays) they stay as separate components in a MultiLineString.
DIRECTION_MERGE_THRESHOLD_M = 30.0

LEGACY_PHASE = "legacy"


_SPINE_RE = re.compile(r"^([A-H])\d+$")
_VARIANT_RE = re.compile(r"^(\d+)[A-Z]*$")


def _bundle_key(short: str, hf_shorts: set[str]) -> str | None:
    """Routes that should be visually bundled into one corridor.

    - Lettered spine routes (A1, A2, B1, ..., H3) bundle within
      their letter — that's the BusConnects spine corridor.
    - High-frequency numeric routes bundle with their letter-suffix
      variants (39 + 39A + 39X) only when the bare-numeric parent is
      itself high-frequency. Without the HF anchor we leave them as
      separate lines.

    All other routes return None and are rendered as their own
    independent feature.
    """
    m = _SPINE_RE.match(short)
    if m:
        return f"spine-{m.group(1)}"
    m = _VARIANT_RE.match(short)
    if m:
        prefix = m.group(1)
        if prefix in hf_shorts:
            return f"num-{prefix}"
    return None


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
    """GTFS -> GeoJSON pipeline with per-category segment bundling.

    For each active route on `date_iso` we pick the most-frequent
    shape per direction, clean fold-back vertices, and run
    `segment_bundle` per category over per-direction fragments. The
    output is one Feature per shared sub-segment: where multiple
    routes ride the same metre of road, exactly one Feature carries
    all their names in `routes`. Routes that diverge produce
    separate Features for each unique stretch.

    Feature properties: routes (list[str]), route_long_name, agency,
    category, colour, phase, direction_id (only when the segment is
    unambiguously one route in one direction).
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

    # Build per-direction fragments. A "fragment" is one direction's
    # cleaned LineString; synthetic names are "{short}#{dir_id}". All
    # fragments go into ONE big dict so segment_bundle sees the full
    # picture of cross-route overlap.
    short_to_category: dict[str, str] = {}
    synth_to_short: dict[str, str] = {}
    synth_to_dir: dict[str, int] = {}
    all_frags: dict[str, _LS] = {}
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
        for direction_id, line in ((0, cleaned_d0), (1, cleaned_d1)):
            if line is None:
                continue
            synth = f"{short}#{direction_id}"
            synth_to_short[synth] = short
            synth_to_dir[synth] = direction_id
            all_frags[synth] = line

    # Run segment_bundle, then emit one Feature per walker
    # sub-segment. Each walker's path is a continuous tiling of its
    # own GTFS shape, so the rendered geometry of any single route
    # never jumps across tolerance-equivalent neighbours.
    #
    # The `routes` property carries only same-category companions at
    # this stretch. When N spine routes share a corridor, all N
    # walkers emit a feature here, all painted in spine red with the
    # same width — they stack into a single visually-thicker red
    # line. Other categories at the same metre of road are emitted
    # by their own walkers in their own colour; CATEGORY_ORDER on
    # the front-end decides which is on top.
    bundled = segment_bundle(all_frags)
    features: list[dict] = []
    rendered_shorts: set[str] = set()
    for sub, synth_members, walker_synth in bundled:
        walker_short = synth_to_short[walker_synth]
        walker_route_id = routes_for_short[walker_short]
        cat = short_to_category[walker_short]
        cat_synths = [
            s for s in synth_members
            if short_to_category[synth_to_short[s]] == cat
        ]
        cat_shorts = sorted({synth_to_short[s] for s in cat_synths})
        dirs_in_cat = {synth_to_dir[s] for s in cat_synths}
        colour = category_colour(cat)
        phase = short_to_phase.get(walker_short, LEGACY_PHASE)

        properties: dict = {
            # `route` (singular): the walker whose own GTFS shape
            # produced this geometry. A line click resolves to one
            # specific route via this field — without it the click
            # would have to seed the full `routes` array, which
            # under per-category emission lights up every companion
            # spine's full path, not just the clicked route.
            "route": walker_short,
            "routes": cat_shorts,
            "route_long_name": long_by_id.get(walker_route_id, ""),
            "agency": AGENCY_LABEL.get(
                agency_by_id.get(walker_route_id, ""), ""
            ),
            "category": cat,
            "colour": colour,
            "phase": phase,
        }
        if len(cat_shorts) == 1 and len(dirs_in_cat) == 1:
            properties["direction_id"] = next(iter(dirs_in_cat))

        geometry = {
            "type": "LineString",
            "coordinates": [list(c) for c in sub.coords],
        }
        features.append(
            {"type": "Feature", "geometry": geometry, "properties": properties}
        )
        rendered_shorts.update(cat_shorts)

    features.sort(
        key=lambda f: (f["properties"]["category"], f["properties"]["routes"])
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

    if not with_labels:
        return routes_geojson, meta

    # Optional: per-route stop labels at first/last/sampled stops.
    stops_by_route_id = stops_for_active_routes(gtfs_dir, trips)
    short_to_route_id = {
        short_by_id[rid]: rid for rid in stops_by_route_id if rid in short_by_id
    }

    # Per-route label seeds (sampled stops along each route), then
    # cluster by location so that labels at the same physical stop
    # combine into one badge listing every route that stops there.
    # Each seed uses the route's OWN category and colour — not a
    # walker feature's, which can bleed across categories in
    # HF-parent + variant bundles (41 is spine via HF promotion, but
    # 41B/41C/41D are radial).
    _CAT_PRIORITY = {"spine": 0, "orbital": 1, "local": 2, "peak": 3, "radial": 4}

    label_seeds: list[dict] = []
    for short in sorted(rendered_shorts):
        rid = short_to_route_id.get(short)
        if rid is None:
            continue
        stops = stops_by_route_id.get(rid, [])
        if len(stops) < 2:
            continue
        cat = short_to_category[short]
        colour = category_colour(cat)
        bk = _bundle_key(short, hf_shorts)
        phase = short_to_phase.get(short, LEGACY_PHASE)
        idxs = sample_stop_indices(len(stops), target_k=max(2, len(stops) // 8))
        for i in idxs:
            lon, lat = stops[i]
            label_seeds.append({
                "lon": lon,
                "lat": lat,
                "short": short,
                "bundle_key": bk,
                "category": cat,
                "colour": colour,
                "phase": phase,
            })

    # Cluster only when routes share a bundle group (spines, or HF +
    # variants). Routes in different bundles — or any singleton —
    # stay as separate labels even when their sampled stops collide.
    clusters: dict[tuple, list[dict]] = defaultdict(list)
    for s in label_seeds:
        # Singletons cluster only with themselves (key includes the
        # short name); bundleable routes cluster by bundle_key.
        cluster_key = s["bundle_key"] or f"single-{s['short']}"
        key = (round(s["lon"], 5), round(s["lat"], 5), cluster_key)
        clusters[key].append(s)

    label_features: list[dict] = []
    for key, items in clusters.items():
        # Build per-route entries (sorted by short) so the rendered
        # badge can show each route in its own category colour.
        by_short: dict[str, dict] = {}
        for it in items:
            by_short.setdefault(it["short"], it)
        shorts = sorted(by_short)
        colours = [by_short[s]["colour"] for s in shorts]
        # Highest-priority category for fallback / single-colour use.
        chosen = min(
            items,
            key=lambda it: _CAT_PRIORITY.get(it["category"], 99),
        )
        phases = {it["phase"] for it in items}
        phase = next(iter(phases)) if len(phases) == 1 else ""
        label_features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [items[0]["lon"], items[0]["lat"]]},
            "properties": {
                "routes": shorts,
                "colours": colours,
                "category": chosen["category"],
                "colour": chosen["colour"],
                "phase": phase,
            },
        })

    labels = {"type": "FeatureCollection", "features": label_features}
    meta["label_feature_count"] = len(label_features)
    return routes_geojson, meta, labels
