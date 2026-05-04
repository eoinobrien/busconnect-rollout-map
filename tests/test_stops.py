"""Loading per-route stop sequences from GTFS for label placement."""

import io
from pathlib import Path

import pandas as pd

from gtfs_map.stops import (
    sample_stop_indices,
    stops_for_active_routes,
)


# --- sample_stop_indices ---------------------------------------------------


def test_first_and_last_indices_always_included():
    # 9 stops, 3 samples: first, middle, last.
    assert sample_stop_indices(9, target_k=3) == [0, 4, 8]
    # 50 stops, 6 samples: first, ~even gaps, last (rounded).
    out = sample_stop_indices(50, target_k=6)
    assert out[0] == 0 and out[-1] == 49
    assert len(out) == 6


def test_two_stop_route_returns_both_endpoints():
    assert sample_stop_indices(2, target_k=4) == [0, 1]


def test_target_capped_to_stop_count():
    # If a route has 3 stops but we ask for 10 samples, return all 3.
    assert sample_stop_indices(3, target_k=10) == [0, 1, 2]


def test_target_below_two_clamped_to_two():
    assert sample_stop_indices(5, target_k=1) == [0, 4]


# --- stops_for_active_routes ------------------------------------------------


STOPS_TXT = (
    "stop_id,stop_name,stop_lat,stop_lon\n"
    "S1,One,53.30,-6.30\n"
    "S2,Two,53.30,-6.25\n"
    "S3,Three,53.30,-6.20\n"
    "S4,Four,53.35,-6.20\n"
)

# Three trips: t1 has 4 stops, t2 has 2 stops, t3 has 3 stops.
STOP_TIMES_TXT = (
    "trip_id,arrival_time,departure_time,stop_id,stop_sequence\n"
    "t1,08:00:00,08:00:00,S1,1\n"
    "t1,08:05:00,08:05:00,S2,2\n"
    "t1,08:10:00,08:10:00,S3,3\n"
    "t1,08:20:00,08:20:00,S4,4\n"
    "t2,09:00:00,09:00:00,S1,1\n"
    "t2,09:10:00,09:10:00,S3,2\n"
    "t3,07:00:00,07:00:00,S2,1\n"
    "t3,07:05:00,07:05:00,S3,2\n"
    "t3,07:10:00,07:10:00,S4,3\n"
)


def _write_fixture(tmp_path: Path) -> Path:
    d = tmp_path / "gtfs"
    d.mkdir()
    (d / "stops.txt").write_text(STOPS_TXT)
    (d / "stop_times.txt").write_text(STOP_TIMES_TXT)
    return d


def test_stops_for_active_routes_picks_representative_trip_per_route(tmp_path):
    gtfs = _write_fixture(tmp_path)
    trips = pd.DataFrame([
        {"route_id": "R1", "direction_id": 0, "trip_id": "t1", "service_id": "WK"},
        {"route_id": "R1", "direction_id": 1, "trip_id": "t2", "service_id": "WK"},
        {"route_id": "R2", "direction_id": 0, "trip_id": "t3", "service_id": "WK"},
    ])
    out = stops_for_active_routes(gtfs, trips)
    # Each route_id maps to an ordered list of (lon, lat) pairs.
    assert "R1" in out and "R2" in out
    # R1 prefers direction_id=0 (trip t1) which has 4 stops.
    assert len(out["R1"]) == 4
    # First stop is S1 = (-6.30, 53.30)
    assert out["R1"][0] == (-6.30, 53.30)
    # Last stop is S4 = (-6.20, 53.35)
    assert out["R1"][-1] == (-6.20, 53.35)
    # R2 has 3 stops via t3.
    assert len(out["R2"]) == 3


def test_falls_back_to_direction_1_if_direction_0_missing(tmp_path):
    gtfs = _write_fixture(tmp_path)
    trips = pd.DataFrame([
        # Only direction 1 present
        {"route_id": "R3", "direction_id": 1, "trip_id": "t2", "service_id": "WK"},
    ])
    out = stops_for_active_routes(gtfs, trips)
    assert "R3" in out
    assert len(out["R3"]) == 2
