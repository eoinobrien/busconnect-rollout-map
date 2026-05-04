"""Integration test for the full GTFS -> GeoJSON pipeline against a
synthetic mini-feed."""

from pathlib import Path

import pytest  # noqa: F401  (used as pytest.approx)

from gtfs_map.pipeline import build


# A tiny GTFS feed with three agencies (one we want to drop), three
# routes (one spine pair A1+A2, one numeric, one commuter we should
# drop), and a calendar with one weekday service and one Sunday-only
# service.
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
    return d


def test_build_excludes_commuter_agency_7778006(fake_gtfs):
    spines, routes, meta = build(fake_gtfs, "2026-05-05")  # Tuesday

    all_features = spines["features"] + routes["features"]
    for f in all_features:
        # Properties must never reveal a commuter route
        assert "120" != f["properties"].get("route_short_name", "")
        for sub in f["properties"].get("route_set", []):
            assert sub != "120"


def test_build_groups_spine_routes_under_their_letter(fake_gtfs):
    spines, _, _ = build(fake_gtfs, "2026-05-05")
    spine_letters = {f["properties"]["spine"] for f in spines["features"]}
    assert spine_letters == {"A"}


def test_build_produces_a_trunk_segment_for_shared_a1_a2(fake_gtfs):
    spines, _, _ = build(fake_gtfs, "2026-05-05")
    trunks = [f for f in spines["features"] if f["properties"]["kind"] == "trunk"]
    assert len(trunks) >= 1
    assert all(
        sorted(t["properties"]["route_set"]) == ["A1", "A2"] for t in trunks
    )


def test_build_includes_non_spine_route_46(fake_gtfs):
    _, routes, _ = build(fake_gtfs, "2026-05-05")
    short_names = {f["properties"]["route_short_name"] for f in routes["features"]}
    assert "46" in short_names


def test_build_filters_out_sunday_only_services_on_a_weekday(fake_gtfs):
    # If we asked for the same Tuesday, the SUN service shouldn't add
    # anything beyond what WK already provides.
    _, _, meta = build(fake_gtfs, "2026-05-05")
    assert "SUN" not in meta.get("active_services", []), (
        "Sunday service must not be active on a Tuesday"
    )


def test_build_meta_records_reference_date(fake_gtfs):
    _, _, meta = build(fake_gtfs, "2026-05-05")
    assert meta["reference_date"] == "2026-05-05"


def test_spine_features_are_coloured_red_and_categorised_spine(fake_gtfs):
    spines, _, _ = build(fake_gtfs, "2026-05-05")
    for f in spines["features"]:
        p = f["properties"]
        assert p["category"] == "spine"
        assert p["colour"].lower() == "#d62728"


def test_non_spine_features_carry_their_category_and_colour(fake_gtfs):
    _, routes, _ = build(fake_gtfs, "2026-05-05")
    # The fixture only has '46' (radial) so far.
    assert routes["features"], "fixture should produce some non-spine routes"
    cats = {f["properties"]["category"] for f in routes["features"]}
    assert "radial" in cats
    for f in routes["features"]:
        p = f["properties"]
        assert "colour" in p
        assert "category" in p


def test_build_emits_a_label_feature_collection_with_route_termini(fake_gtfs):
    spines, routes, _, labels = build(fake_gtfs, "2026-05-05", with_labels=True)
    # One label per (route_short_name, terminus) — we expect at least
    # one for each route in the fixture: A1, A2, 46.
    short_names = {f["properties"]["route_short_name"] for f in labels["features"]}
    assert {"A1", "A2", "46"} <= short_names

    # Each label is a Point with a colour and a category.
    for f in labels["features"]:
        assert f["geometry"]["type"] == "Point"
        assert "colour" in f["properties"]
        assert "category" in f["properties"]


def test_labels_for_a_route_sit_at_either_end_of_its_shape(fake_gtfs):
    _, _, _, labels = build(fake_gtfs, "2026-05-05", with_labels=True)
    a1_labels = [f for f in labels["features"] if f["properties"]["route_short_name"] == "A1"]
    # A1 should have at least its start and end as label positions.
    assert len(a1_labels) >= 2
    coords = sorted(tuple(f["geometry"]["coordinates"]) for f in a1_labels)
    # A1 in the fixture goes (-6.30, 53.30) -> ... -> (-6.20, 53.40)
    assert coords[0][0] == pytest.approx(-6.30, abs=0.01)
    assert coords[-1][1] == pytest.approx(53.40, abs=0.01)
