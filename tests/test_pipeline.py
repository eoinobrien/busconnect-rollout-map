"""Integration test for the full GTFS -> GeoJSON pipeline against a
synthetic mini-feed."""

from pathlib import Path

import pytest  # noqa: F401  (used as pytest.approx)

from gtfs_map.pipeline import build


AGENCY_TXT = (
    "agency_id,agency_name,agency_url,agency_timezone\n"
    "7778019,Dublin Bus,https://www.dublinbus.ie/,Europe/London\n"
    "7778021,Go-Ahead,https://www.goaheadireland.ie/,Europe/London\n"
    "7778006,Go-Ahead Commuter,https://www.goaheadireland.ie/,Europe/London\n"
)

ROUTES_TXT = (
    "route_id,agency_id,route_short_name,route_long_name,route_desc,route_type,route_url,route_color,route_text_color\n"
    "R_A1,7778019,A1,Dublin - Test East,,3,,,\n"
    "R_A2,7778019,A2,Dublin - Test North,,3,,,\n"
    "R_46,7778021,46,Numeric Route,,3,,,\n"
    "R_120,7778006,120,Dublin - Edenderry COMMUTER,,3,,,\n"
)

CALENDAR_TXT = (
    "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\n"
    "WK,1,1,1,1,1,0,0,20260101,20271231\n"
    "SUN,0,0,0,0,0,0,1,20260101,20271231\n"
)

CAL_DATES_TXT = "service_id,date,exception_type\n"

# A1: shares first leg with A2, then turns north
# A2: shares first leg with A1, then continues east
TRIPS_TXT = (
    "route_id,service_id,trip_id,trip_headsign,trip_short_name,direction_id,block_id,shape_id\n"
    "R_A1,WK,T_A1,East,,0,,SH_A1\n"
    "R_A2,WK,T_A2,North,,0,,SH_A2\n"
    "R_46,WK,T_46,Loop,,0,,SH_46\n"
    "R_120,WK,T_120,Edenderry,,0,,SH_120\n"
    "R_A1,SUN,T_A1_SUN,East,,0,,SH_A1\n"  # Sunday-only — must be filtered out
)

SHAPES_TXT = (
    "shape_id,shape_pt_lat,shape_pt_lon,shape_pt_sequence,shape_dist_traveled\n"
    "SH_A1,53.30,-6.30,1,0\n"
    "SH_A1,53.30,-6.20,2,1000\n"
    "SH_A1,53.40,-6.20,3,2000\n"
    "SH_A2,53.30,-6.30,1,0\n"
    "SH_A2,53.30,-6.20,2,1000\n"
    "SH_A2,53.30,-6.10,3,2000\n"
    "SH_46,53.35,-6.25,1,0\n"
    "SH_46,53.36,-6.24,2,500\n"
    "SH_120,53.30,-7.00,1,0\n"  # commuter, should never appear in output
    "SH_120,53.30,-7.10,2,1000\n"
)


# A minimal stop_times file. No row's first stop falls in 08:00-08:59,
# so no fixture route should be promoted to high-frequency.
STOP_TIMES_TXT = (
    "trip_id,arrival_time,departure_time,stop_id,stop_sequence,stop_headsign,pickup_type,drop_off_type,timepoint\n"
    "T_A1,07:00:00,07:00:00,X,1,,0,0,1\n"
    "T_A2,07:05:00,07:05:00,X,1,,0,0,1\n"
    "T_46,07:10:00,07:10:00,X,1,,0,0,1\n"
    "T_120,07:15:00,07:15:00,X,1,,0,0,1\n"
)


@pytest.fixture
def fake_gtfs(tmp_path: Path) -> Path:
    d = tmp_path / "gtfs"
    d.mkdir()
    (d / "agency.txt").write_text(AGENCY_TXT)
    (d / "routes.txt").write_text(ROUTES_TXT)
    (d / "calendar.txt").write_text(CALENDAR_TXT)
    (d / "calendar_dates.txt").write_text(CAL_DATES_TXT)
    (d / "trips.txt").write_text(TRIPS_TXT)
    (d / "shapes.txt").write_text(SHAPES_TXT)
    (d / "stop_times.txt").write_text(STOP_TIMES_TXT)
    return d


def _features(segments):
    return segments["features"]


def test_build_excludes_commuter_agency_7778006(fake_gtfs):
    segments, _, _ = build(fake_gtfs, "2026-05-05")
    for f in _features(segments):
        for r in f["properties"].get("route_set", []):
            assert r != "120", "commuter route 120 leaked into output"


def test_spine_routes_appear_in_segments_with_spine_category(fake_gtfs):
    segments, _, _ = build(fake_gtfs, "2026-05-05")
    spine_features = [f for f in _features(segments) if f["properties"]["category"] == "spine"]
    assert spine_features, "expected spine features for A1+A2"
    # Every spine feature should be coloured red.
    for f in spine_features:
        assert f["properties"]["colour"].lower() == "#d62728"


def test_a1_a2_share_a_segment(fake_gtfs):
    segments, _, _ = build(fake_gtfs, "2026-05-05")
    shared = [
        f for f in _features(segments)
        if f["properties"]["category"] == "spine"
        and f["properties"]["kind"] == "shared"
    ]
    assert shared, "A1+A2 should produce at least one shared segment"
    for f in shared:
        assert sorted(f["properties"]["route_set"]) == ["A1", "A2"]


def test_radial_route_46_appears_with_radial_category(fake_gtfs):
    segments, _, _ = build(fake_gtfs, "2026-05-05")
    radial = [f for f in _features(segments) if f["properties"]["category"] == "radial"]
    routes_in_radial = {r for f in radial for r in f["properties"]["route_set"]}
    assert "46" in routes_in_radial


def test_build_filters_out_sunday_only_services_on_a_weekday(fake_gtfs):
    _, _, meta = build(fake_gtfs, "2026-05-05")
    assert "SUN" not in meta.get("active_services", [])


def test_build_meta_records_reference_date(fake_gtfs):
    _, _, meta = build(fake_gtfs, "2026-05-05")
    assert meta["reference_date"] == "2026-05-05"


def test_build_meta_records_high_frequency_settings(fake_gtfs):
    _, _, meta = build(fake_gtfs, "2026-05-05")
    assert meta["high_frequency_threshold"] == 5
    assert meta["high_frequency_hour"] == 8
    # Fixture has no 8am trips, so no fixture route should have been
    # promoted to spine via frequency.
    assert meta["high_frequency_route_count"] == 0


def test_labels_collection_has_one_feature_per_terminus_with_routes_list(fake_gtfs):
    _, _, _, labels = build(fake_gtfs, "2026-05-05", with_labels=True)
    assert labels["features"]
    for f in labels["features"]:
        assert f["geometry"]["type"] == "Point"
        assert "routes" in f["properties"]
        assert isinstance(f["properties"]["routes"], list)
        assert "label" in f["properties"]
        assert "category" in f["properties"]
        assert "colour" in f["properties"]


def test_routes_sharing_a_terminus_get_one_combined_label(fake_gtfs):
    # A1 and A2 both start at (-6.30, 53.30). The terminus clustering
    # should produce a single label whose `routes` list contains both.
    _, _, _, labels = build(fake_gtfs, "2026-05-05", with_labels=True)
    shared_origin = [
        f for f in labels["features"]
        if {"A1", "A2"} <= set(f["properties"]["routes"])
    ]
    assert shared_origin, "expected A1+A2 to share a clustered start label"


def test_route_46_appears_in_some_label(fake_gtfs):
    _, _, _, labels = build(fake_gtfs, "2026-05-05", with_labels=True)
    assert any("46" in f["properties"]["routes"] for f in labels["features"])
