"""Tests for the future-routes merge / strip helpers.

These exercise `gtfs_map.future_merge.merge_future_routes`,
`strip_future_routes`, and `live_route_phase_map` against in-memory
routes + meta dicts and a small manual-future GeoJSON written to
tmp_path.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gtfs_map.future_merge import (
    live_route_phase_map,
    merge_future_routes,
    strip_future_routes,
)
from gtfs_map.pipeline import is_future_date
from pdf_map.manual import FUTURE_PHASE


# A LineString long enough to clear any min-segment filters downstream.
_LINE = [
    [-6.270, 53.345],
    [-6.275, 53.345],
    [-6.280, 53.345],
]


def _write_manual(tmp_path: Path, features: list[dict]) -> Path:
    p = tmp_path / "manual.geojson"
    p.write_text(json.dumps({"type": "FeatureCollection", "features": features}))
    return p


def _manual_feature(route_no: str) -> dict:
    return {
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": _LINE},
        "properties": {"route no": route_no},
    }


# ---------------------------------------------------------------------------
# is_future_date
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value,expected", [
    ("2026-05-05", False),
    ("2025-10-19", False),
    ("future", True),
    ("unknown", True),
    ("planned", True),
    ("tbd", True),
    ("", True),
    (None, True),
])
def test_is_future_date(value, expected):
    assert is_future_date(value) is expected


# ---------------------------------------------------------------------------
# merge_future_routes
# ---------------------------------------------------------------------------


def test_merge_returns_zero_when_manual_file_missing(tmp_path):
    routes = {"type": "FeatureCollection", "features": []}
    meta = {"rollout_phases": {}, "route_phase": {}, "phase_route_counts": {}}
    n = merge_future_routes(routes, meta, tmp_path / "nope.geojson")
    assert n == 0
    assert routes["features"] == []


def test_merge_appends_features_with_fallback_phase(tmp_path):
    """Routes not declared in any future phase get FUTURE_PHASE."""
    manual = _write_manual(tmp_path, [_manual_feature("Z99")])
    routes = {"type": "FeatureCollection", "features": []}
    meta = {
        "rollout_phases": {},
        "route_phase": {},
        "phase_route_counts": {},
        "route_count": 0,
        "feature_count": 0,
    }
    n = merge_future_routes(routes, meta, manual)
    assert n == 1
    assert routes["features"][0]["properties"]["phase"] == FUTURE_PHASE
    assert meta["route_phase"]["Z99"] == FUTURE_PHASE
    assert meta["phase_route_counts"][FUTURE_PHASE] == 1
    # Synthetic rollout_phases entry created for the fallback bucket
    assert FUTURE_PHASE in meta["rollout_phases"]
    assert meta["rollout_phases"][FUTURE_PHASE]["date"] == "future"
    assert "Z99" in meta["rollout_phases"][FUTURE_PHASE]["routes"]


def test_merge_assigns_route_to_declared_future_phase(tmp_path):
    """A route listed under a 'date=unknown' phase lands there, not in FUTURE_PHASE."""
    manual = _write_manual(tmp_path, [_manual_feature("A1")])
    routes = {"type": "FeatureCollection", "features": []}
    meta = {
        "rollout_phases": {
            "11 (Potential)": {"date": "unknown", "routes": ["A1"], "replacing": []},
        },
        "route_phase": {},
        "phase_route_counts": {},
        "route_count": 0,
        "feature_count": 0,
    }
    n = merge_future_routes(routes, meta, manual)
    assert n == 1
    assert routes["features"][0]["properties"]["phase"] == "11 (Potential)"
    assert meta["route_phase"]["A1"] == "11 (Potential)"
    # No synthetic fallback bucket since every route had a declared phase
    assert FUTURE_PHASE not in meta["rollout_phases"]


def test_merge_skips_live_phase_in_short_to_phase_lookup(tmp_path):
    """A manual route id that also appears in a live (date-set) phase
    should still go to the future fallback - live phases describe live
    GTFS routes, not the manual replacement geometry."""
    manual = _write_manual(tmp_path, [_manual_feature("80")])
    routes = {"type": "FeatureCollection", "features": []}
    meta = {
        "rollout_phases": {
            "7 (Oct 2025)": {"date": "2025-10-19", "routes": ["80"], "replacing": []},
        },
        "route_phase": {},
        "phase_route_counts": {},
        "route_count": 0,
        "feature_count": 0,
    }
    n = merge_future_routes(routes, meta, manual)
    assert n == 1
    # Live phase's `routes` list should NOT pull the manual feature in;
    # 80 falls back to FUTURE_PHASE because no future-dated phase claims it.
    assert routes["features"][0]["properties"]["phase"] == FUTURE_PHASE


def test_merge_increments_route_and_feature_counts(tmp_path):
    manual = _write_manual(tmp_path, [
        _manual_feature("A1"),
        _manual_feature("A2"),
    ])
    routes = {"type": "FeatureCollection", "features": [{"properties": {"phase": "legacy"}}]}
    meta = {
        "rollout_phases": {},
        "route_phase": {"13": "legacy"},
        "phase_route_counts": {"legacy": 1},
        "route_count": 1,
        "feature_count": 1,
    }
    merge_future_routes(routes, meta, manual)
    assert meta["route_count"] == 3  # 1 existing + 2 future
    assert meta["feature_count"] == 3


# ---------------------------------------------------------------------------
# strip_future_routes
# ---------------------------------------------------------------------------


def _feature_with_phase(phase: str) -> dict:
    return {
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": _LINE},
        "properties": {"phase": phase, "route": "X"},
    }


def test_strip_removes_only_future_phases():
    routes = {
        "type": "FeatureCollection",
        "features": [
            _feature_with_phase("legacy"),
            _feature_with_phase("7 (Oct 2025)"),
            _feature_with_phase("11 (Potential)"),
            _feature_with_phase(FUTURE_PHASE),
        ],
    }
    rollout = {
        "7 (Oct 2025)": {"date": "2025-10-19", "routes": [], "replacing": []},
        "11 (Potential)": {"date": "unknown", "routes": [], "replacing": []},
    }
    strip_future_routes(routes, rollout)
    phases = [f["properties"]["phase"] for f in routes["features"]]
    assert phases == ["legacy", "7 (Oct 2025)"]


def test_strip_handles_empty_rollout():
    """If rollout is empty, only the synthetic FUTURE_PHASE bucket is stripped."""
    routes = {
        "type": "FeatureCollection",
        "features": [
            _feature_with_phase("legacy"),
            _feature_with_phase(FUTURE_PHASE),
        ],
    }
    strip_future_routes(routes, {})
    phases = [f["properties"]["phase"] for f in routes["features"]]
    assert phases == ["legacy"]


# ---------------------------------------------------------------------------
# live_route_phase_map
# ---------------------------------------------------------------------------


def test_live_route_phase_map_drops_future_assignments():
    """Routes tagged with any future phase (declared or synthetic)
    are dropped; routes tagged with a live (date-set) phase are kept."""
    meta = {"route_phase": {
        "13": "1",
        "80": "7",
        "A1": "11 (Potential)",  # future, should drop
        "Z99": FUTURE_PHASE,     # synthetic future, should drop
    }}
    rollout = {
        "1": {"date": "2024-06-24", "routes": ["13"]},
        "7": {"date": "2025-10-19", "routes": ["80"]},
        "11 (Potential)": {"date": "unknown", "routes": ["A1"]},
        FUTURE_PHASE: {"date": "future", "routes": ["Z99"]},
    }
    out = live_route_phase_map(meta, rollout)
    assert out == {"13": "1", "80": "7"}


def test_live_route_phase_map_refreshes_from_rollout():
    """If a route's phase has been moved in rollout-phases.json, the
    live map should pick up the new assignment."""
    meta = {"route_phase": {"80": "6 (Jul 2025)"}}
    rollout = {
        "6 (Jul 2025)": {"date": "2025-07-13", "routes": []},
        "7 (Oct 2025)": {"date": "2025-10-19", "routes": ["80"]},
    }
    out = live_route_phase_map(meta, rollout)
    assert out["80"] == "7 (Oct 2025)"
