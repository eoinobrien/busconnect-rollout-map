"""CLI entrypoint: build GeoJSON outputs from the local GTFS feed."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from gtfs_map.pipeline import build
from gtfs_map.rail import build_rail_geojson


DEFAULT_DATE = "2026-05-05"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=DEFAULT_DATE)
    parser.add_argument("--gtfs", default="data/gtfs")
    parser.add_argument("--out", default="output")
    parser.add_argument("--no-labels", action="store_true")
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.no_labels:
        routes, meta = build(Path(args.gtfs), args.date, with_labels=False)
        labels = None
    else:
        routes, meta, labels = build(Path(args.gtfs), args.date, with_labels=True)

    (out_dir / "routes.geojson").write_text(json.dumps(routes))
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=2))
    if labels is not None:
        (out_dir / "labels.geojson").write_text(json.dumps(labels))

    rail = build_rail_geojson(Path(args.gtfs), args.date)
    (out_dir / "rail.geojson").write_text(json.dumps(rail))

    # Old artifacts from the bundled-pipeline era — clean up so the
    # viewer doesn't accidentally read stale shapes.
    for old in ("segments.geojson",):
        p = out_dir / old
        if p.exists():
            p.unlink()

    print(f"Reference date:           {meta['reference_date']}")
    print(f"Active services:          {meta['active_service_count']}")
    print(f"Routes rendered:          {meta['route_count']}")
    print(f"Features (segments):      {meta['feature_count']}")
    print(f"High-frequency routes:    {meta['high_frequency_route_count']}")
    print(f"Routes per category:      {meta['category_route_counts']}")
    if labels is not None:
        print(f"Label features:           {meta['label_feature_count']}")
    print(f"Rail/LUAS features:       {len(rail['features'])}")
    print(f"Wrote: {out_dir}/routes.geojson, meta.json, rail.geojson"
          + (", labels.geojson" if labels is not None else ""))


if __name__ == "__main__":
    main()
