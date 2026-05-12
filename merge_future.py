"""CLI: re-merge the manual future-routes file into an existing build.

The full pipeline takes 15+ minutes because of the GTFS segment-
bundling. When only rollout-phases.json or the manual GeoJSON has
changed, the GTFS-derived features in routes.geojson don't need
to be regenerated - we just strip the previous future overlay,
re-apply with the current data, and rewrite outputs in under a
second.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from gtfs_map.future_merge import (
    live_route_phase_map,
    merge_future_routes,
    strip_future_routes,
)
from gtfs_map.pipeline import is_future_date
from pdf_map.manual import FUTURE_PHASE


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="output")
    parser.add_argument(
        "--manual-future",
        default="output/manual-future-routes.geojson",
    )
    parser.add_argument(
        "--rollout-phases",
        default="data/rollout-phases.json",
    )
    args = parser.parse_args()

    out = Path(args.out)
    routes_path = out / "routes.geojson"
    meta_path = out / "meta.json"

    if not routes_path.exists() or not meta_path.exists():
        raise SystemExit("Run `python build.py` first; merge needs existing outputs.")

    routes = json.loads(routes_path.read_text())
    meta = json.loads(meta_path.read_text())

    # Refresh rollout_phases from disk so user edits take effect.
    rollout = json.loads(Path(args.rollout_phases).read_text())
    meta["rollout_phases"] = rollout

    strip_future_routes(routes, rollout)
    meta["route_phase"] = live_route_phase_map(meta, rollout)
    # Reset phase counts for future phases - merge will repopulate.
    counts = meta.get("phase_route_counts", {})
    for pid in list(counts):
        if is_future_date(rollout.get(pid, {}).get("date")) or pid == FUTURE_PHASE:
            counts.pop(pid, None)
    meta["phase_route_counts"] = counts
    # Reset rendered feature count; merge will append.
    meta["route_count"] = len({
        r for r, p in meta["route_phase"].items()
        if not is_future_date(rollout.get(p, {}).get("date"))
    })
    meta["feature_count"] = len(routes["features"])

    feat_count = merge_future_routes(
        routes, meta, Path(args.manual_future)
    )

    routes_path.write_text(json.dumps(routes))
    meta_path.write_text(json.dumps(meta, indent=2))

    print(f"Future routes (manual):   {feat_count} features, "
          f"{meta['phase_route_counts'].get(FUTURE_PHASE, 0)} unattached fallback")
    print(f"  per declared phase:     {{")
    for pid, info in sorted(rollout.items()):
        if not is_future_date(info.get("date")):
            continue
        print(f"    {pid!r}: {meta['phase_route_counts'].get(pid, 0)} routes / "
              f"{len(info.get('replacing', []))} replacing")
    print(f"  }}")
    print(f"Total features:           {meta['feature_count']}")
    print(f"Wrote: {routes_path}, {meta_path}")


if __name__ == "__main__":
    main()
