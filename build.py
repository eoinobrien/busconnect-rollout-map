"""CLI entrypoint: build GeoJSON outputs from the local GTFS feed."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from gtfs_map.pipeline import build


DEFAULT_DATE = "2026-05-05"  # Tuesday after the May Day bank holiday


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--date",
        default=DEFAULT_DATE,
        help="Reference date YYYY-MM-DD (default: %(default)s).",
    )
    parser.add_argument(
        "--gtfs",
        default="data/gtfs",
        help="Directory containing the extracted GTFS .txt files.",
    )
    parser.add_argument(
        "--out",
        default="output",
        help="Directory to write segments.geojson, labels.geojson, meta.json.",
    )
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    segments, _, meta, labels = build(Path(args.gtfs), args.date, with_labels=True)

    (out_dir / "segments.geojson").write_text(json.dumps(segments))
    (out_dir / "labels.geojson").write_text(json.dumps(labels))
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=2))

    print(f"Reference date:           {meta['reference_date']}")
    print(f"Active services:          {meta['active_service_count']}")
    print(f"City routes (post-filter): {meta['route_count']}")
    print(f"High-frequency promotion: {meta['high_frequency_route_count']} routes >= "
          f"{meta['high_frequency_threshold']} trips at hour {meta['high_frequency_hour']}:00")
    print(f"Routes per category:      {meta['category_route_counts']}")
    print(f"Segment features:         {meta['segment_feature_count']}")
    print(f"Label features:           {meta['label_feature_count']}")
    print(f"Wrote: {out_dir}/segments.geojson, labels.geojson, meta.json")


if __name__ == "__main__":
    main()
