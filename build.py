"""CLI entrypoint: build GeoJSON outputs from the local GTFS feed."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from gtfs_map.future_merge import merge_future_routes
from gtfs_map.pipeline import build
from gtfs_map.rail import build_rail_geojson
from pdf_map.manual import FUTURE_PHASE


DEFAULT_DATE = "2026-05-05"
DEFAULT_MANUAL_FUTURE = "output/manual-future-routes.geojson"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=DEFAULT_DATE)
    parser.add_argument("--gtfs", default="data/gtfs")
    parser.add_argument("--out", default="output")
    parser.add_argument(
        "--offset",
        action="store_true",
        help="Apply per-category perpendicular offset so cross-category routes "
             "on a shared road draw as parallel lines instead of stacking. Off "
             "by default - each route renders on its exact GTFS shape.",
    )
    parser.add_argument(
        "--manual-future",
        default=DEFAULT_MANUAL_FUTURE,
        help="GeoJSON of road-snapped future BusConnects routes; merged "
             "into routes.geojson if present. Pass empty string to skip.",
    )
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    routes, meta = build(
        Path(args.gtfs), args.date,
        offset_categories=args.offset,
    )

    future_count = 0
    if args.manual_future:
        future_count = merge_future_routes(
            routes, meta, Path(args.manual_future)
        )

    (out_dir / "routes.geojson").write_text(json.dumps(routes))
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=2))

    rail = build_rail_geojson(Path(args.gtfs), args.date)
    (out_dir / "rail.geojson").write_text(json.dumps(rail))

    print(f"Reference date:           {meta['reference_date']}")
    print(f"Active services:          {meta['active_service_count']}")
    print(f"Routes rendered:          {meta['route_count']}")
    print(f"Features (segments):      {meta['feature_count']}")
    print(f"High-frequency routes:    {meta['high_frequency_route_count']}")
    print(f"Routes per category:      {meta['category_route_counts']}")
    if future_count:
        print(f"Future routes (manual):   {future_count} features, "
              f"{meta['phase_route_counts'].get(FUTURE_PHASE, 0)} routes")
    print(f"Rail/LUAS features:       {len(rail['features'])}")
    print(f"Wrote: {out_dir}/routes.geojson, meta.json, rail.geojson")


if __name__ == "__main__":
    main()
