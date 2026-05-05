"""Quantitative diagnosis of bundle output vs original GTFS shapes.

Builds a buffer around every route's true shape and checks how far
each bundle feature lies from it. A high deviation means the bundle
has snapped a route's geometry onto someone else's road — visible
as crisscrossing or off-road lines on the map.

Run after build.py to get hard numbers on what's actually wrong.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path

import pandas as pd
import pyproj
from shapely.geometry import LineString, MultiLineString, Point, shape


_TO_ITM = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:2157", always_xy=True)


def _project(g):
    if isinstance(g, LineString):
        return LineString([_TO_ITM.transform(x, y) for x, y in g.coords])
    if isinstance(g, MultiLineString):
        return MultiLineString([_project(c) for c in g.geoms])
    if isinstance(g, Point):
        return Point(_TO_ITM.transform(g.x, g.y))
    raise TypeError(g.geom_type)


def _component_lines(g):
    if isinstance(g, LineString):
        return [g]
    return list(g.geoms)


def load_original_shapes(gtfs_dir: Path) -> dict[str, MultiLineString]:
    """Build a MultiLineString per route_short_name covering EVERY
    shape_id used by that route. A route can have multiple variants
    (per direction, short-working, etc.) and the bundle uses all of
    them, so the diagnostic must compare to all of them too —
    otherwise legitimate routes get flagged as off-route just because
    we only loaded one variant.
    """
    routes = pd.read_csv(gtfs_dir / "routes.txt", dtype=str)
    routes = routes[routes["agency_id"].isin({"7778019", "7778021"})]
    short_by_id = dict(zip(routes["route_id"], routes["route_short_name"]))

    trips = pd.read_csv(
        gtfs_dir / "trips.txt",
        dtype={"route_id": str, "shape_id": str},
        low_memory=False,
    )
    trips = trips[trips["route_id"].isin(short_by_id)]

    # All distinct (route_id, shape_id) pairs in active trips.
    pairs = trips[["route_id", "shape_id"]].drop_duplicates()
    needed = set(pairs["shape_id"])

    shapes = pd.read_csv(
        gtfs_dir / "shapes.txt",
        dtype={"shape_id": str},
        usecols=["shape_id", "shape_pt_lat", "shape_pt_lon", "shape_pt_sequence"],
    )
    shapes = shapes[shapes["shape_id"].isin(needed)].sort_values(
        ["shape_id", "shape_pt_sequence"]
    )

    shape_lines: dict[str, LineString] = {}
    for sid, group in shapes.groupby("shape_id", sort=False):
        coords = list(zip(group["shape_pt_lon"], group["shape_pt_lat"]))
        if len(coords) >= 2:
            shape_lines[sid] = _project(LineString(coords))

    by_route: dict[str, list[LineString]] = {}
    for _, row in pairs.iterrows():
        line = shape_lines.get(row["shape_id"])
        if line is None:
            continue
        short = short_by_id[row["route_id"]]
        by_route.setdefault(short, []).append(line)

    out: dict[str, MultiLineString] = {
        short: MultiLineString(lines) for short, lines in by_route.items()
    }
    return out


def diagnose(
    segments_path: Path = Path("output/segments.geojson"),
    gtfs_dir: Path = Path("data/gtfs"),
    sample_step_m: float = 25.0,
    deviation_threshold_m: float = 30.0,
) -> dict:
    """For every output feature, sample points every sample_step_m
    along the geometry and check the minimum distance from each route
    in the feature's route_set to the sampled point. A point > threshold
    from ALL of its route's actual shapes is "off-road" — the bundle
    pulled it somewhere it shouldn't be.
    """
    segs = json.load(open(segments_path))
    originals = load_original_shapes(gtfs_dir)
    print(f"Loaded {len(originals)} original route shapes")

    features = segs["features"]
    print(f"Diagnosing {len(features)} segment features")

    bad_samples: list[tuple] = []  # (route, dist_m, lon, lat)
    sample_count = 0
    feat_with_bad = 0

    for f in features:
        rs = f["properties"].get("route_set", [])
        if not rs:
            continue
        feat_geom = _project(shape(f["geometry"]))
        feat_bad = False
        for line in _component_lines(feat_geom):
            length = line.length
            if length < sample_step_m:
                step_pts = [line.interpolate(0), line.interpolate(length)]
            else:
                n = max(2, int(length / sample_step_m) + 1)
                step_pts = [line.interpolate(length * i / (n - 1)) for i in range(n)]
            for pt in step_pts:
                sample_count += 1
                # Min distance from any route in route_set to this pt
                best = float("inf")
                best_route = None
                for r in rs:
                    orig = originals.get(r)
                    if orig is None:
                        continue
                    d = pt.distance(orig)
                    if d < best:
                        best = d
                        best_route = r
                if best > deviation_threshold_m:
                    # Re-project to WGS for reporting
                    lon, lat = pyproj.Transformer.from_crs(
                        "EPSG:2157", "EPSG:4326", always_xy=True
                    ).transform(pt.x, pt.y)
                    bad_samples.append((best_route or rs[0], best, lon, lat))
                    feat_bad = True
        if feat_bad:
            feat_with_bad += 1

    print()
    print(f"Total sample points checked: {sample_count}")
    print(
        f"Off-route samples (>{deviation_threshold_m} m from any route's true shape): "
        f"{len(bad_samples)} ({len(bad_samples)/sample_count*100:.2f}%)"
    )
    print(f"Features with at least one off-route sample: {feat_with_bad}")
    print()
    bad_samples.sort(key=lambda x: -x[1])
    print("Top 10 worst-deviating samples:")
    for r, d, lon, lat in bad_samples[:10]:
        print(f"  {d:6.0f} m  route={r:8s}  near ({lat:.5f}, {lon:.5f})")

    # Group bad samples by approximate location to find bad hotspots
    grid = 0.001  # ~110 m grid for clustering
    hotspots: dict[tuple, int] = defaultdict(int)
    for r, d, lon, lat in bad_samples:
        key = (round(lat / grid) * grid, round(lon / grid) * grid)
        hotspots[key] += 1
    top = sorted(hotspots.items(), key=lambda kv: -kv[1])[:10]
    print()
    print("Top 10 bad-sample hotspots (110 m grid cells):")
    for (lat, lon), n in top:
        print(f"  {n:4d} bad samples at ({lat:.4f}, {lon:.4f})")

    return {
        "total_samples": sample_count,
        "bad_samples": len(bad_samples),
        "feat_with_bad": feat_with_bad,
    }


if __name__ == "__main__":
    diagnose()
