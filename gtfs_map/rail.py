"""Build a simple GeoJSON layer for LUAS and Irish Rail.

These run as background context on the Dublin city bus map — no
bundling, no labels, no per-route interactivity. Each route emits
one Feature carrying its merged-direction geometry plus a `mode`
property ('luas' or 'rail') so the front-end can pick distinct
colours.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import pandas as pd
from shapely.geometry import LineString, MultiLineString, mapping

from .direction_merge import merge_directions
from .segment_bundle import segment_bundle
from .services import active_services_for_date
from .shapes import build_linestrings, representative_shape_ids


# Two operators with track infrastructure on the Dublin map. The
# bus pipeline filters them out; we read them here separately.
_LUAS = "7778014"
_RAIL = "7778017"
_AGENCY_MODE = {_LUAS: "luas", _RAIL: "rail"}
_AGENCY_NAME = {_LUAS: "LUAS", _RAIL: "Iarnród Éireann"}

# DART runs Greystones↔Malahide on track shared with the Drogheda,
# Wexford, and Maynooth commuter services — drawing it there is
# just an extra stripe over those. The unique-to-DART stretch is
# the Howth spur east of Howth Junction. Bbox is the Howth
# peninsula band, with the western edge nudged to lon -6.118 so
# it includes the Howth Junction platform vertices (-6.116↔-6.117)
# rather than ending 300 m short of the station.
_DART_SPUR_BBOX = (-6.118, 53.385, -6.060, 53.400)


def build_rail_geojson(gtfs_dir: Path, date_iso: str) -> dict:
    """Return a FeatureCollection of LUAS + Irish Rail lines active
    on `date_iso`. Each feature has properties:
      - mode: 'luas' or 'rail'
      - agency: human-readable agency name
      - short: route short name (informational)
    Geometry is a (Multi)LineString from the most-frequent shape per
    direction, lightly cleaned via the same fold-back trim used for
    bus shapes.
    """
    gtfs_dir = Path(gtfs_dir)

    with open(gtfs_dir / "calendar.txt") as cal, open(
        gtfs_dir / "calendar_dates.txt"
    ) as cal_dates:
        active_services = active_services_for_date(cal, cal_dates, date_iso)

    routes_df = pd.read_csv(gtfs_dir / "routes.txt", dtype=str)
    routes_df = routes_df[routes_df["agency_id"].isin(_AGENCY_MODE)].copy()
    # Iarnród Éireann's GTFS lumps every long-distance InterCity
    # service under agency 7778017 alongside DART and Commuter.
    # Most are also labelled `short='rail'` rather than 'Commuter',
    # so a short-name filter alone misses Maynooth/M3 Parkway,
    # Drogheda/Dundalk, and the Wexford (Rosslare Europort) lines.
    # Match by long-name destination instead, keeping the four
    # Dublin commuter spokes plus DART. Long-distance InterCity
    # destinations (Cork, Galway, Sligo, Tralee, etc.) drop out.
    rail_keep = routes_df["route_long_name"].fillna("").str.contains(
        r"Drogheda|Dundalk|Maynooth|Portlaoise|Europort",
        regex=True,
        case=False,
    )
    rail_keep |= routes_df["route_short_name"].isin({"DART", "Commuter"})
    routes_df = routes_df[
        (routes_df["agency_id"] != _RAIL) | rail_keep
    ]
    if routes_df.empty:
        return {"type": "FeatureCollection", "features": []}

    short_by_id = dict(zip(routes_df["route_id"], routes_df["route_short_name"].fillna("")))
    agency_by_id = dict(zip(routes_df["route_id"], routes_df["agency_id"]))
    kept_route_ids = set(routes_df["route_id"])

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
    if trips.empty:
        return {"type": "FeatureCollection", "features": []}

    # Pick one canonical shape per distinct (route, terminus pair).
    # LUAS Red has 27 active shapes, but only a handful of distinct
    # endpoint combinations (Connolly↔Tallaght, Connolly↔Saggart,
    # Connolly↔Belgard turn-back, etc.). Maynooth has Connolly↔
    # Maynooth and Connolly↔M3 Parkway as the two real branches.
    # Feeding all 27 shapes into segment_bundle is combinatorial
    # work for the same handful of corridors — an endpoint-pair
    # dedupe collapses duplicates while preserving every branch.
    shape_to_route = dict(zip(trips["shape_id"], trips["route_id"]))
    needed_shape_ids = set(shape_to_route)
    shapes_df = pd.read_csv(
        gtfs_dir / "shapes.txt",
        dtype={"shape_id": str},
        usecols=["shape_id", "shape_pt_lat", "shape_pt_lon", "shape_pt_sequence"],
    )
    shapes_df = shapes_df[shapes_df["shape_id"].isin(needed_shape_ids)]
    lines = build_linestrings(shapes_df)

    # Cluster shapes by (route, bbox at 3-decimal precision). Two
    # rail shapes with the same rough bbox (~110m granularity) are
    # almost always the same physical corridor in different
    # direction or with platform-level endpoint variation. Within
    # each cluster keep the longest shape as the canonical line.
    def _endpoint_key(line: LineString) -> tuple:
        # 2-decimal bbox (~1 km) — coarse enough that platform-level
        # endpoint differences (0.001° / ~100 m) don't split a single
        # corridor into multiple clusters, fine enough that distinct
        # branches like Tallaght vs Saggart still bbox-differ.
        minx, miny, maxx, maxy = line.bounds
        return (round(minx, 2), round(miny, 2), round(maxx, 2), round(maxy, 2))

    cluster_best: dict[tuple, tuple[str, LineString]] = {}
    for shape_id, line in lines.items():
        route_id = shape_to_route.get(shape_id)
        if route_id is None:
            continue
        key = (route_id, _endpoint_key(line))
        prev = cluster_best.get(key)
        if prev is None or line.length > prev[1].length:
            cluster_best[key] = (shape_id, line)

    # No length filter: the Iarnród Éireann GTFS reuses a single
    # `Dublin - Maynooth` route_id for both Maynooth commuter
    # services AND Dublin–Sligo InterCity (which travels via the
    # Maynooth line for the first stretch). The Sligo shapes are
    # 3× longer than Maynooth proper, so an 80%-of-longest filter
    # would keep only Sligo and silently drop the Maynooth and M3
    # Parkway branches we actually want. Keep all bbox clusters.

    # Build per-mode canonical lines, keyed by an ID that survives
    # into segment_bundle. Trim DART to the Howth spur only.
    per_mode: dict[str, dict[str, LineString]] = defaultdict(dict)
    synth_meta: dict[str, dict] = {}
    for (route_id, _ep), (shape_id, line) in cluster_best.items():
        agency_id = agency_by_id.get(route_id, "")
        mode = _AGENCY_MODE.get(agency_id, "")
        if not mode:
            continue
        cleaned = merge_directions(line, None)
        short = short_by_id.get(route_id, "")
        if mode == "rail" and short == "DART":
            x0, y0, x1, y1 = _DART_SPUR_BBOX
            spur = [(x, y) for x, y in cleaned.coords if x0 <= x <= x1 and y0 <= y <= y1]
            if len(spur) < 2:
                continue
            cleaned = LineString(spur)
        per_mode[mode][shape_id] = cleaned
        synth_meta[shape_id] = {
            "agency": _AGENCY_NAME.get(agency_id, ""),
            "short": short,
        }

    # Run segment_bundle per mode so corridor sharing within the
    # mode collapses. With only the canonical clusters fed in
    # (~7 LUAS shapes, ~20 rail shapes) this finishes in seconds —
    # very different cost profile from feeding all raw shape
    # variants. Tolerance is wider than the bus default: rail
    # shapes can sit ~30 m apart on the same physical track.
    features: list[dict] = []
    for mode, frags in per_mode.items():
        if not frags:
            continue
        bundled = segment_bundle(
            frags,
            tolerance_m=30.0,
            sample_step_m=20.0,
            min_segment_m=150.0,
        )
        # Keep one geometry per unique membership set — that's the
        # single representation of each shared corridor. Drop
        # near-degenerate sub-segments (< 50 m or < 3 vertices)
        # left over from the sliver-merge logic.
        seen: set[frozenset[str]] = set()
        for sub, members, walker in bundled:
            if members in seen:
                continue
            if len(sub.coords) < 3:
                continue
            seen.add(members)
            meta = synth_meta.get(walker, {})
            features.append({
                "type": "Feature",
                "geometry": mapping(sub),
                "properties": {
                    "mode": mode,
                    "agency": meta.get("agency", ""),
                    "short": meta.get("short", ""),
                },
            })

    return {"type": "FeatureCollection", "features": features}
