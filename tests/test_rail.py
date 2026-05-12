"""Tests for the LUAS + Irish Rail GeoJSON builder."""

from __future__ import annotations

import pytest

from gtfs_map.rail import build_rail_geojson
from tests.gtfs_fixtures import (
    AGENCY,
    CAL_DATES_EMPTY,
    CALENDAR_WEEKDAY,
    routes_csv,
    shapes_csv,
    stop_times_csv,
    stops_csv,
    trips_csv,
    write_gtfs,
)


# Dublin city centre + extended west, in lon/lat. Each point is
# spaced ~250m so the bundled output clears the 150 m segment
# minimum that rail.py uses.
_LUAS_RED_COORDS = [
    (-6.270, 53.345),
    (-6.273, 53.345),
    (-6.276, 53.345),
    (-6.279, 53.345),
    (-6.282, 53.345),
    (-6.285, 53.345),
]
_DART_HOWTH_COORDS = [
    (-6.090, 53.388),  # inside Howth-spur bbox
    (-6.080, 53.390),
    (-6.070, 53.391),
    (-6.065, 53.391),
]
_REF_DATE = "2026-05-05"  # Tuesday


def _base_files(routes, trips, shapes):
    return dict(
        agency=AGENCY,
        calendar=CALENDAR_WEEKDAY,
        calendar_dates=CAL_DATES_EMPTY,
        routes=routes,
        trips=trips,
        shapes=shapes,
        stops=stops_csv([("S1", -6.27, 53.345)]),
        stop_times=stop_times_csv([
            {"trip_id": "T1", "stop_id": "S1", "stop_sequence": 1},
        ]),
    )


def test_empty_gtfs_yields_empty_feature_collection(tmp_path):
    """No rail or LUAS routes → empty FeatureCollection, no crash."""
    d = write_gtfs(
        tmp_path / "gtfs",
        **_base_files(
            routes=routes_csv([{
                "route_id": "R1", "agency_id": "7778019",
                "route_short_name": "13", "route_type": 3,
            }]),
            trips=trips_csv([{
                "route_id": "R1", "service_id": "WK",
                "trip_id": "T1", "shape_id": "S1",
            }]),
            shapes=shapes_csv({"S1": _LUAS_RED_COORDS}),
        ),
    )
    out = build_rail_geojson(d, _REF_DATE)
    assert out == {"type": "FeatureCollection", "features": []}


def test_luas_route_emits_feature_with_mode_luas(tmp_path):
    d = write_gtfs(
        tmp_path / "gtfs",
        **_base_files(
            routes=routes_csv([{
                "route_id": "LR", "agency_id": "7778014",
                "route_short_name": "Red", "route_long_name": "Red Line",
                "route_type": 0,
            }]),
            trips=trips_csv([{
                "route_id": "LR", "service_id": "WK",
                "trip_id": "T1", "shape_id": "LS1",
            }]),
            shapes=shapes_csv({"LS1": _LUAS_RED_COORDS}),
        ),
    )
    out = build_rail_geojson(d, _REF_DATE)
    assert len(out["features"]) >= 1
    modes = {f["properties"]["mode"] for f in out["features"]}
    assert modes == {"luas"}
    agencies = {f["properties"]["agency"] for f in out["features"]}
    assert agencies == {"LUAS"}


def test_dart_route_is_trimmed_to_howth_spur(tmp_path):
    """DART shape with vertices both inside and outside the spur bbox
    should keep only the spur portion. With no spur vertices the route
    is dropped entirely."""
    # Mix of in-bbox (Howth) and out-of-bbox (city centre) vertices.
    mixed = [
        (-6.300, 53.345),  # city centre - out
        (-6.250, 53.360),  # out
        (-6.090, 53.388),  # in spur bbox
        (-6.080, 53.390),  # in
        (-6.070, 53.391),  # in
    ]
    d = write_gtfs(
        tmp_path / "gtfs",
        **_base_files(
            routes=routes_csv([{
                "route_id": "DR", "agency_id": "7778017",
                "route_short_name": "DART",
                "route_long_name": "Dublin - Howth",
                "route_type": 2,
            }]),
            trips=trips_csv([{
                "route_id": "DR", "service_id": "WK",
                "trip_id": "T1", "shape_id": "DS1",
            }]),
            shapes=shapes_csv({"DS1": mixed}),
        ),
    )
    out = build_rail_geojson(d, _REF_DATE)
    # All emitted features should be rail mode, geometry coords all
    # within the spur bbox.
    for f in out["features"]:
        assert f["properties"]["mode"] == "rail"
        coords = f["geometry"]["coordinates"]
        if f["geometry"]["type"] == "LineString":
            flat = coords
        else:  # MultiLineString
            flat = [pt for part in coords for pt in part]
        for x, y in flat:
            assert -6.118 <= x <= -6.060
            assert 53.385 <= y <= 53.400


def test_intercity_route_without_keyword_match_is_excluded(tmp_path):
    """Iarnród Éireann's Cork InterCity service is on agency 7778017
    but its long_name doesn't match the Dublin-commuter keyword list,
    so it should drop out of the rail layer entirely."""
    d = write_gtfs(
        tmp_path / "gtfs",
        **_base_files(
            routes=routes_csv([{
                "route_id": "IC", "agency_id": "7778017",
                "route_short_name": "InterCity",
                "route_long_name": "Dublin Heuston - Cork Kent",
                "route_type": 2,
            }]),
            trips=trips_csv([{
                "route_id": "IC", "service_id": "WK",
                "trip_id": "T1", "shape_id": "IS1",
            }]),
            shapes=shapes_csv({"IS1": _LUAS_RED_COORDS}),
        ),
    )
    out = build_rail_geojson(d, _REF_DATE)
    assert out["features"] == []


def test_commuter_keyword_route_kept(tmp_path):
    """A 7778017 route with 'Maynooth' in the long_name is kept."""
    d = write_gtfs(
        tmp_path / "gtfs",
        **_base_files(
            routes=routes_csv([{
                "route_id": "MY", "agency_id": "7778017",
                "route_short_name": "Commuter",
                "route_long_name": "Dublin Connolly - Maynooth",
                "route_type": 2,
            }]),
            trips=trips_csv([{
                "route_id": "MY", "service_id": "WK",
                "trip_id": "T1", "shape_id": "MS1",
            }]),
            shapes=shapes_csv({"MS1": _LUAS_RED_COORDS}),
        ),
    )
    out = build_rail_geojson(d, _REF_DATE)
    assert len(out["features"]) >= 1
    assert all(f["properties"]["mode"] == "rail" for f in out["features"])


def test_inactive_service_returns_no_features(tmp_path):
    """A LUAS route whose only service is SUN should not appear on a Tuesday."""
    d = write_gtfs(
        tmp_path / "gtfs",
        **_base_files(
            routes=routes_csv([{
                "route_id": "LR", "agency_id": "7778014",
                "route_short_name": "Red", "route_type": 0,
            }]),
            trips=trips_csv([{
                "route_id": "LR", "service_id": "SUN",
                "trip_id": "T1", "shape_id": "LS1",
            }]),
            shapes=shapes_csv({"LS1": _LUAS_RED_COORDS}),
        ),
    )
    out = build_rail_geojson(d, _REF_DATE)  # Tuesday
    assert out == {"type": "FeatureCollection", "features": []}
