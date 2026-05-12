"""Merge the manually-edited future-routes GeoJSON into a live build.

`merge_future_routes` appends manual features to an in-memory routes
FeatureCollection and updates meta accounting. `strip_future_routes`
removes everything tagged with a future phase so a re-merge starts
clean. Both are pure dict operations - no file I/O - so they're
trivial to unit-test and reusable from any caller.
"""

from __future__ import annotations

from pathlib import Path

from pdf_map.manual import FUTURE_PHASE, load_manual_future_features

from .pipeline import is_future_date


def merge_future_routes(
    routes: dict,
    meta: dict,
    manual_path: Path,
) -> int:
    """Append manual future routes to `routes` and update `meta` in place.

    Returns the number of features added.

    Reads phase assignments from `meta["rollout_phases"]` (already
    populated from rollout-phases.json by the live pipeline), so a
    future route declared under "11 (Potential)" lands in that phase
    rather than the generic FUTURE_PHASE bucket. Routes not in any
    explicit phase fall back to FUTURE_PHASE.

    Updates meta.route_phase and meta.phase_route_counts to include
    the manual features. Does NOT touch meta.rollout_phases - the
    user's JSON is the authoritative source for phase metadata,
    including the `replacing` arrays - unless we have to synthesise
    a generic FUTURE_PHASE bucket for the fallback case.
    """
    rollout = meta.get("rollout_phases", {})
    # Manual features represent the planned alignment, so look up
    # their phase from FUTURE phases only. Listing a live route id
    # in a future phase's `routes` array is fine - it doesn't drag
    # the live feature's phase tag forward.
    short_to_phase: dict[str, str] = {}
    for phase_id, info in rollout.items():
        if not is_future_date(info.get("date")):
            continue
        for r in info.get("routes", []):
            short_to_phase.setdefault(r, phase_id)

    extra = load_manual_future_features(manual_path, short_to_phase)
    if not extra:
        return 0
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
    # apply future styling + default-hide. Without this they leak
    # into the legacy visibility bucket.
    fallback_routes = seen_routes_per_phase.get(FUTURE_PHASE)
    if fallback_routes:
        meta.setdefault("rollout_phases", {}).setdefault(FUTURE_PHASE, {
            "date": "future",
            "routes": sorted(fallback_routes),
            "replacing": [],
        })

    return len(extra)


def strip_future_routes(routes: dict, rollout: dict) -> None:
    """Drop features whose phase is a future entry in `rollout`.

    Used by the re-merge CLI to start from a clean slate when the
    manual GeoJSON or rollout-phases.json has changed: the live GTFS
    features stay, every future-phase feature is removed, then the
    caller re-runs `merge_future_routes` to add the current set.
    """
    future_phases = {
        pid for pid, info in rollout.items()
        if is_future_date(info.get("date"))
    }
    future_phases.add(FUTURE_PHASE)  # legacy synthetic bucket if any

    routes["features"] = [
        f for f in routes["features"]
        if f["properties"].get("phase") not in future_phases
    ]


def live_route_phase_map(meta: dict, rollout: dict) -> dict[str, str]:
    """Reset meta.route_phase to the GTFS-derived assignment only.

    Drops every route currently tagged with a future phase (those
    will be re-added when the manual file is re-merged) and refreshes
    the rest from the latest rollout-phases.json so user edits to
    the live phases take effect.
    """
    short_to_phase: dict[str, str] = {}
    for phase_id, info in rollout.items():
        for r in info.get("routes", []):
            short_to_phase[r] = phase_id
    out: dict[str, str] = {}
    for rid, p in meta.get("route_phase", {}).items():
        rp = rollout.get(p, {})
        if is_future_date(rp.get("date")):
            continue
        out[rid] = short_to_phase.get(rid, p)
    return out
