"""CLI entrypoint: build GeoJSON outputs from the local GTFS feed."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from gtfs_map.pipeline import build
from gtfs_map.rail import build_rail_geojson
from pdf_map.manual import (
    FUTURE_PHASE,
    build_future_labels,
    distinct_routes as _future_distinct_routes,
    load_manual_future_features,
)


DEFAULT_DATE = "2026-05-05"
DEFAULT_MANUAL_FUTURE = "output/manual-future-routes.geojson"


def _merge_future_routes(
    routes: dict,
    meta: dict,
    labels: dict | None,
    manual_path: Path,
) -> tuple[int, int]:
    """Append the manually-edited future routes to the live build.

    Returns `(feature_count, label_count)`. Updates `meta` with the
    new phase, route_phase entries, and phase_route_counts so the
    viewer's BusConnects-phase panel automatically picks up a "future"
    row alongside the historical phases. When `labels` is provided
    (full build, not --no-labels), label seeds sampled along each
    future route are appended too.
    """
    extra = load_manual_future_features(manual_path)
    if not extra:
        return 0, 0
    routes["features"].extend(extra)

    future_routes = _future_distinct_routes(extra)
    meta.setdefault("rollout_phases", {})[FUTURE_PHASE] = {
        "date": "planned",
        "routes": future_routes,
    }
    rp = meta.setdefault("route_phase", {})
    for r in future_routes:
        rp.setdefault(r, FUTURE_PHASE)
    counts = meta.setdefault("phase_route_counts", {})
    counts[FUTURE_PHASE] = len(future_routes)
    meta["route_count"] = meta.get("route_count", 0) + len(future_routes)
    meta["feature_count"] = len(routes["features"])

    label_count = 0
    if labels is not None:
        extra_labels = build_future_labels(extra)
        if extra_labels:
            labels["features"].extend(extra_labels)
            meta["label_feature_count"] = len(labels["features"])
            label_count = len(extra_labels)
    return len(extra), label_count


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=DEFAULT_DATE)
    parser.add_argument("--gtfs", default="data/gtfs")
    parser.add_argument("--out", default="output")
    parser.add_argument("--no-labels", action="store_true")
    parser.add_argument(
        "--no-offset",
        action="store_true",
        help="Skip the perpendicular category offset that splits same-corridor "
             "routes from different categories into parallel lines. Useful when "
             "you want every route to ride the exact road centreline.",
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

    offset_categories = not args.no_offset
    if args.no_labels:
        routes, meta = build(
            Path(args.gtfs), args.date,
            with_labels=False,
            offset_categories=offset_categories,
        )
        labels = None
    else:
        routes, meta, labels = build(
            Path(args.gtfs), args.date,
            with_labels=True,
            offset_categories=offset_categories,
        )

    future_count = 0
    future_label_count = 0
    if args.manual_future:
        future_count, future_label_count = _merge_future_routes(
            routes, meta, labels, Path(args.manual_future)
        )

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
    if future_count:
        print(f"Future routes (manual):   {future_count} features, "
              f"{meta['phase_route_counts'].get(FUTURE_PHASE, 0)} routes, "
              f"{future_label_count} labels")
    print(f"Rail/LUAS features:       {len(rail['features'])}")
    print(f"Wrote: {out_dir}/routes.geojson, meta.json, rail.geojson"
          + (", labels.geojson" if labels is not None else ""))


if __name__ == "__main__":
    main()
