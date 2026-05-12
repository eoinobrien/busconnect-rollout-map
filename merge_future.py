"""CLI: re-merge the manual future-routes file into an existing build.

The full pipeline takes 15+ minutes because of the GTFS segment-
bundling. When only rollout-phases.json or the manual GeoJSON has
changed, the GTFS-derived features in routes.geojson don't need
to be regenerated - we just need to strip the previous future
overlay, re-apply with the current data, and rewrite outputs.

Reads existing output/routes.geojson, meta.json, labels.geojson,
removes everything tagged phase in any "date=future" rollout-phases
entry (so a stale merge doesn't leak across), refreshes the rollout
metadata from data/rollout-phases.json, and runs the merge step in
under a second.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from build import _merge_future_routes, FUTURE_PHASE


_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _is_future_date(date_value) -> bool:
    """A phase whose `date` isn't an ISO calendar date counts as future.

    "future", "unknown", "planned", "tbd" all qualify - any non-
    YYYY-MM-DD value means the rollout hasn't been scheduled.
    """
    return not _ISO_DATE_RE.match(str(date_value or ""))


def _live_route_phase_map(meta: dict, rollout: dict) -> dict[str, str]:
    """Reset meta.route_phase to the GTFS-derived assignment only."""
    short_to_phase: dict[str, str] = {}
    for phase_id, info in rollout.items():
        for r in info.get("routes", []):
            short_to_phase[r] = phase_id
    out: dict[str, str] = {}
    for rid, p in meta.get("route_phase", {}).items():
        # Drop future-phase assignments; we'll rebuild them from the
        # manual merge. Keep GTFS routes with their existing phase.
        rp = rollout.get(p, {})
        if _is_future_date(rp.get("date")):
            continue
        out[rid] = short_to_phase.get(rid, p)
    return out


def _strip_future(routes: dict, labels: dict, rollout: dict) -> None:
    """Drop features and labels whose phase is a future entry."""
    future_phases = {
        pid for pid, info in rollout.items()
        if _is_future_date(info.get("date"))
    }
    future_phases.add(FUTURE_PHASE)  # legacy synthetic bucket if any

    routes["features"] = [
        f for f in routes["features"]
        if f["properties"].get("phase") not in future_phases
    ]
    if labels is not None:
        labels["features"] = [
            f for f in labels["features"]
            if f["properties"].get("phase") not in future_phases
        ]


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
    labels_path = out / "labels.geojson"

    if not routes_path.exists() or not meta_path.exists():
        raise SystemExit("Run `python build.py` first; merge needs existing outputs.")

    routes = json.loads(routes_path.read_text())
    meta = json.loads(meta_path.read_text())
    labels = json.loads(labels_path.read_text()) if labels_path.exists() else None

    # Refresh rollout_phases from disk so user edits take effect.
    rollout = json.loads(Path(args.rollout_phases).read_text())
    meta["rollout_phases"] = rollout

    _strip_future(routes, labels, rollout)
    meta["route_phase"] = _live_route_phase_map(meta, rollout)
    # Reset phase counts for future phases - merge will repopulate.
    counts = meta.get("phase_route_counts", {})
    for pid in list(counts):
        if rollout.get(pid, {}).get("date") == "future" or pid == FUTURE_PHASE:
            counts.pop(pid, None)
    meta["phase_route_counts"] = counts
    # Reset rendered feature count; merge will append.
    meta["route_count"] = len({
        r for r, p in meta["route_phase"].items()
        if not _is_future_date(rollout.get(p, {}).get("date"))
    })
    meta["feature_count"] = len(routes["features"])
    if labels is not None:
        meta["label_feature_count"] = len(labels["features"])

    feat_count, label_count = _merge_future_routes(
        routes, meta, labels, Path(args.manual_future)
    )

    routes_path.write_text(json.dumps(routes))
    meta_path.write_text(json.dumps(meta, indent=2))
    if labels is not None:
        labels_path.write_text(json.dumps(labels))

    print(f"Future routes (manual):   {feat_count} features, "
          f"{meta['phase_route_counts'].get(FUTURE_PHASE, 0)} unattached fallback")
    print(f"  per declared phase:     {{")
    for pid, info in sorted(rollout.items()):
        if not _is_future_date(info.get("date")):
            continue
        print(f"    {pid!r}: {meta['phase_route_counts'].get(pid, 0)} routes / "
              f"{len(info.get('replacing', []))} replacing")
    print(f"  }}")
    print(f"  labels written:         {label_count}")
    print(f"Total features:           {meta['feature_count']}")
    print(f"Total labels:             {meta.get('label_feature_count', 0)}")
    print(f"Wrote: {routes_path}, {meta_path}"
          + (f", {labels_path}" if labels is not None else ""))


if __name__ == "__main__":
    main()
