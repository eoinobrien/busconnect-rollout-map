"""CLI entrypoint: build GeoJSON outputs from the local GTFS feed."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from gtfs_map.pipeline import build
from gtfs_map.rail import build_rail_geojson
from gtfs_map.pipeline import _is_future_date
from pdf_map.manual import (
    FUTURE_PHASE,
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

    Returns `(feature_count, label_count)`. Reads phase assignments
    from meta.rollout_phases (already populated from rollout-phases.json
    by the live pipeline), so a future route declared under
    "11 (Potential)" lands in that phase rather than a generic
    "future" bucket. Routes not in any explicit phase fall back to
    FUTURE_PHASE.

    Updates meta.route_phase and meta.phase_route_counts to include
    the manual features. Does NOT touch meta.rollout_phases - the
    user's JSON is the authoritative source for phase metadata,
    including the `replacing` arrays.
    """
    rollout = meta.get("rollout_phases", {})
    # Manual features represent the planned alignment, so look up
    # their phase from FUTURE phases only. Listing a live route id
    # in a future phase's `routes` array is fine - it doesn't drag
    # the live feature's phase tag forward.
    short_to_phase: dict[str, str] = {}
    for phase_id, info in rollout.items():
        if not _is_future_date(info.get("date")):
            continue
        for r in info.get("routes", []):
            short_to_phase.setdefault(r, phase_id)

    extra = load_manual_future_features(manual_path, short_to_phase)
    if not extra:
        return 0, 0
    routes["features"].extend(extra)

    rp = meta.setdefault("route_phase", {})
    counts = meta.setdefault("phase_route_counts", {})
    seen_routes_per_phase: dict[str, set[str]] = {}
    for f in extra:
        phase = f["properties"].get("phase", FUTURE_PHASE)
        for r in f["properties"].get("routes", []):
            rp.setdefault(r, phase)
            seen_routes_per_phase.setdefault(phase, set()).add(r)
    for phase, rs in seen_routes_per_phase.items():
        counts[phase] = max(counts.get(phase, 0), len(rs))

    future_route_count = sum(len(rs) for rs in seen_routes_per_phase.values())
    meta["route_count"] = meta.get("route_count", 0) + future_route_count
    meta["feature_count"] = len(routes["features"])

    # If any manual route had no explicit phase in rollout-phases.json,
    # synthesise a generic FUTURE_PHASE entry so the viewer knows to
    # apply future styling + default-hide to them. Without this they
    # leak into the legacy visibility bucket.
    fallback_routes = seen_routes_per_phase.get(FUTURE_PHASE)
    if fallback_routes:
        meta.setdefault("rollout_phases", {}).setdefault(FUTURE_PHASE, {
            "date": "future",
            "routes": sorted(fallback_routes),
            "replacing": [],
        })

    # Future-route labels are rendered client-side via
    # Leaflet.PolylineDecorator (see index.html), so we no longer
    # pre-sample them into labels.geojson. Return 0 for the label
    # count - the build summary still prints it for visibility.
    return len(extra), 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=DEFAULT_DATE)
    parser.add_argument("--gtfs", default="data/gtfs")
    parser.add_argument("--out", default="output")
    parser.add_argument("--no-labels", action="store_true")
    parser.add_argument(
        "--offset",
        action="store_true",
        help="Apply per-category perpendicular offset so cross-category routes "
             "on a shared road draw as parallel lines instead of stacking. Off "
             "by default - each route renders on its exact GTFS shape.",
    )
    parser.add_argument(
        "--no-offset",
        action="store_true",
        help="Deprecated and now the default. Accepted for backwards compat.",
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

    offset_categories = args.offset and not args.no_offset
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
