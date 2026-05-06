"""Pipeline tests for the minimal one-feature-per-route build.

Each test sets up a tiny synthetic GTFS feed in tmp_path and runs
gtfs_map.pipeline.build() against it. The fixture gives each scenario
its own minimal data so failures are easy to diagnose.

Discipline: don't loosen these assertions when something fails.
A failing test means the implementation or a fixture is wrong; the
test states the contract.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pyproj
import pytest

from gtfs_map.pipeline import build


_TO_ITM = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:2157", always_xy=True)


def _itm(coords):
    return [_TO_ITM.transform(x, y) for x, y in coords]


def _max_edge_m(coords):
    pts = _itm(coords)
    return max(
        math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1])
        for i in range(1, len(pts))
    )


# --------------------------------------------------------------------------
# Fixture builders
# --------------------------------------------------------------------------


def _write_gtfs(d: Path, **files):
    """files keyed by filename (no .txt) -> string contents."""
    d.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (d / f"{name}.txt").write_text(content)
    return d


_AGENCY = (
    "agency_id,agency_name,agency_url,agency_timezone\n"
    "7778019,Dublin Bus,https://dublinbus.ie/,Europe/London\n"
    "7778021,Go-Ahead,https://goaheadireland.ie/,Europe/London\n"
    "7778006,Go-Ahead Commuter,https://goaheadireland.ie/,Europe/London\n"
)

_CALENDAR_WEEKDAY = (
    "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\n"
    "WK,1,1,1,1,1,0,0,20260101,20271231\n"
    "SUN,0,0,0,0,0,0,1,20260101,20271231\n"
)

_CAL_DATES_EMPTY = "service_id,date,exception_type\n"


def _routes_csv(rows):
    """rows: list of dicts with at least route_id, agency_id, route_short_name."""
    header = (
        "route_id,agency_id,route_short_name,route_long_name,route_desc,"
        "route_type,route_url,route_color,route_text_color\n"
    )
    body = ""
    for r in rows:
        body += (
            f"{r['route_id']},{r['agency_id']},{r['route_short_name']},"
            f"{r.get('route_long_name', '')},,3,,,\n"
        )
    return header + body


def _trips_csv(rows):
    """rows: list of dicts with route_id, service_id, trip_id, direction_id, shape_id."""
    header = (
        "route_id,service_id,trip_id,trip_headsign,trip_short_name,"
        "direction_id,block_id,shape_id\n"
    )
    body = ""
    for t in rows:
        body += (
            f"{t['route_id']},{t['service_id']},{t['trip_id']},,," +
            f"{t.get('direction_id', 0)},,{t['shape_id']}\n"
        )
    return header + body


def _shapes_csv(shapes: dict[str, list[tuple[float, float]]]):
    """shapes: shape_id -> list of (lon, lat) in order."""
    header = "shape_id,shape_pt_lat,shape_pt_lon,shape_pt_sequence,shape_dist_traveled\n"
    body = ""
    for sid, coords in shapes.items():
        for i, (lon, lat) in enumerate(coords, start=1):
            body += f"{sid},{lat},{lon},{i},{i*100}\n"
    return header + body


def _stop_times_csv(rows):
    """rows: list of dicts with trip_id, stop_id, stop_sequence, departure_time."""
    header = (
        "trip_id,arrival_time,departure_time,stop_id,stop_sequence,stop_headsign,"
        "pickup_type,drop_off_type,timepoint\n"
    )
    body = ""
    for r in rows:
        t = r.get("departure_time", "07:00:00")
        body += (
            f"{r['trip_id']},{t},{t},{r['stop_id']},{r['stop_sequence']},,0,0,1\n"
        )
    return header + body


def _stops_csv(stops):
    """stops: list of (stop_id, lon, lat)."""
    header = "stop_id,stop_name,stop_lat,stop_lon\n"
    body = ""
    for sid, lon, lat in stops:
        body += f"{sid},{sid},{lat},{lon}\n"
    return header + body


# --------------------------------------------------------------------------
# E: pipeline output shape
# --------------------------------------------------------------------------


def test_E1_empty_gtfs_yields_empty_feature_collection(tmp_path):
    d = _write_gtfs(
        tmp_path / "gtfs",
        agency=_AGENCY,
        calendar=_CALENDAR_WEEKDAY,
        calendar_dates=_CAL_DATES_EMPTY,
        routes=_routes_csv([]),
        trips=_trips_csv([]),
        shapes=_shapes_csv({}),
        stop_times=_stop_times_csv([]),
        stops=_stops_csv([]),
    )
    routes, meta = build(d, "2026-05-05")
    assert routes == {"type": "FeatureCollection", "features": []}
    assert meta["route_count"] == 0


def test_E2_one_active_route_produces_one_feature(tmp_path):
    d = _write_gtfs(
        tmp_path / "gtfs",
        agency=_AGENCY,
        calendar=_CALENDAR_WEEKDAY,
        calendar_dates=_CAL_DATES_EMPTY,
        routes=_routes_csv([
            {"route_id": "R1", "agency_id": "7778019", "route_short_name": "13",
             "route_long_name": "City Centre - North"}
        ]),
        trips=_trips_csv([
            {"route_id": "R1", "service_id": "WK", "trip_id": "T1",
             "direction_id": 0, "shape_id": "S1"}
        ]),
        shapes=_shapes_csv({"S1": [(-6.30, 53.30), (-6.20, 53.30)]}),
        stop_times=_stop_times_csv([]),
        stops=_stops_csv([]),
    )
    routes, _ = build(d, "2026-05-05")
    assert len(routes["features"]) == 1
    p = routes["features"][0]["properties"]
    assert p["routes"] == ["13"]
    assert p["agency"] == "Dublin Bus"
    assert p["direction_id"] == 0


def test_E3_route_with_both_directions_renders_both(tmp_path):
    """Both directions of the route appear in the output. With segment
    bundling, tight-corridor directions collapse into one feature;
    distant ones (Liffey-style one-way pairs) stay as two separate
    features. Either way, every feature carries `routes=["13"]`."""
    d = _write_gtfs(
        tmp_path / "gtfs",
        agency=_AGENCY,
        calendar=_CALENDAR_WEEKDAY,
        calendar_dates=_CAL_DATES_EMPTY,
        routes=_routes_csv([
            {"route_id": "R1", "agency_id": "7778019", "route_short_name": "13"}
        ]),
        trips=_trips_csv([
            {"route_id": "R1", "service_id": "WK", "trip_id": "T0",
             "direction_id": 0, "shape_id": "S0"},
            {"route_id": "R1", "service_id": "WK", "trip_id": "T1",
             "direction_id": 1, "shape_id": "S1"},
        ]),
        shapes=_shapes_csv({
            "S0": [(-6.30, 53.30), (-6.20, 53.30)],
            "S1": [(-6.20, 53.301), (-6.30, 53.301)],
        }),
        stop_times=_stop_times_csv([]),
        stops=_stops_csv([]),
    )
    routes, _ = build(d, "2026-05-05")
    feats = routes["features"]
    assert len(feats) >= 1
    for f in feats:
        assert f["properties"]["routes"] == ["13"]


def test_E4_route_with_only_direction_one_renders(tmp_path):
    """A route that only runs in dir 1 today emits a single dir-1
    Feature."""
    d = _write_gtfs(
        tmp_path / "gtfs",
        agency=_AGENCY,
        calendar=_CALENDAR_WEEKDAY,
        calendar_dates=_CAL_DATES_EMPTY,
        routes=_routes_csv([
            {"route_id": "R1", "agency_id": "7778019", "route_short_name": "13"}
        ]),
        trips=_trips_csv([
            {"route_id": "R1", "service_id": "WK", "trip_id": "T1",
             "direction_id": 1, "shape_id": "S1"}
        ]),
        shapes=_shapes_csv({"S1": [(-6.20, 53.30), (-6.30, 53.30)]}),
        stop_times=_stop_times_csv([]),
        stops=_stops_csv([]),
    )
    routes, _ = build(d, "2026-05-05")
    assert len(routes["features"]) == 1
    assert routes["features"][0]["properties"]["direction_id"] == 1


def test_E4b_route_with_only_direction_zero_renders(tmp_path):
    """Mirror of E4 — only dir 0 today emits a single dir-0 Feature."""
    d = _write_gtfs(
        tmp_path / "gtfs",
        agency=_AGENCY,
        calendar=_CALENDAR_WEEKDAY,
        calendar_dates=_CAL_DATES_EMPTY,
        routes=_routes_csv([
            {"route_id": "R1", "agency_id": "7778019", "route_short_name": "13"}
        ]),
        trips=_trips_csv([
            {"route_id": "R1", "service_id": "WK", "trip_id": "T0",
             "direction_id": 0, "shape_id": "S0"}
        ]),
        shapes=_shapes_csv({"S0": [(-6.30, 53.30), (-6.20, 53.30)]}),
        stop_times=_stop_times_csv([]),
        stops=_stops_csv([]),
    )
    routes, _ = build(d, "2026-05-05")
    assert len(routes["features"]) == 1
    assert routes["features"][0]["properties"]["direction_id"] == 0


def test_E3b_two_directions_emit_one_feature_per_direction(tmp_path):
    """Pipeline integration: route 13's two directions ~5.5m apart
    (well inside segment_bundle tolerance) emit TWO Features — one
    per walker — both stacked at the same category colour. Direction
    info is preserved per feature only when the walker's segment
    covers a single direction; here both directions sit within
    tolerance of each other, so neither feature carries direction_id."""
    d = _write_gtfs(
        tmp_path / "gtfs",
        agency=_AGENCY,
        calendar=_CALENDAR_WEEKDAY,
        calendar_dates=_CAL_DATES_EMPTY,
        routes=_routes_csv([
            {"route_id": "R1", "agency_id": "7778019", "route_short_name": "13"}
        ]),
        trips=_trips_csv([
            {"route_id": "R1", "service_id": "WK", "trip_id": "T0",
             "direction_id": 0, "shape_id": "S0"},
            {"route_id": "R1", "service_id": "WK", "trip_id": "T1",
             "direction_id": 1, "shape_id": "S1"},
        ]),
        shapes=_shapes_csv({
            "S0": [(-6.30, 53.30), (-6.20, 53.30)],
            "S1": [(-6.20, 53.30005), (-6.30, 53.30005)],
        }),
        stop_times=_stop_times_csv([]),
        stops=_stops_csv([]),
    )
    routes, _ = build(d, "2026-05-05")
    feats = routes["features"]
    assert len(feats) == 2
    for f in feats:
        assert f["geometry"]["type"] == "LineString"
        assert f["properties"]["routes"] == ["13"]
        assert "direction_id" not in f["properties"]


def test_E3c_liffey_style_one_way_pair_emits_two_linestring_features(tmp_path):
    """Pipeline integration: a Liffey-quay-style one-way pair (two
    parallel streets >tolerance apart for several km) emits TWO
    Feature LineStrings — each direction on its own line — both
    carrying routes=['13']."""
    d = _write_gtfs(
        tmp_path / "gtfs",
        agency=_AGENCY,
        calendar=_CALENDAR_WEEKDAY,
        calendar_dates=_CAL_DATES_EMPTY,
        routes=_routes_csv([
            {"route_id": "R1", "agency_id": "7778019", "route_short_name": "13"}
        ]),
        trips=_trips_csv([
            {"route_id": "R1", "service_id": "WK", "trip_id": "T0",
             "direction_id": 0, "shape_id": "S0"},
            {"route_id": "R1", "service_id": "WK", "trip_id": "T1",
             "direction_id": 1, "shape_id": "S1"},
        ]),
        shapes=_shapes_csv({
            "S0": [(-6.30, 53.30000), (-6.20, 53.30000)],
            "S1": [(-6.20, 53.30050), (-6.30, 53.30050)],
        }),
        stop_times=_stop_times_csv([]),
        stops=_stops_csv([]),
    )
    routes, _ = build(d, "2026-05-05")
    feats = routes["features"]
    assert len(feats) == 2
    for f in feats:
        assert f["geometry"]["type"] == "LineString"
        assert f["properties"]["routes"] == ["13"]
    # Each feature is unambiguously one direction.
    dirs = sorted(f["properties"]["direction_id"] for f in feats)
    assert dirs == [0, 1]


def test_E4c_each_direction_picks_its_most_frequent_shape(tmp_path):
    """The pipeline picks the most-frequent shape per direction (not
    per route). With dir 0 / dir 1 on parallel streets ~110 m apart,
    segment bundling emits two Features (one per direction). The
    runner-up dir-0 shape S0_B must NOT appear in either feature.
    """
    d = _write_gtfs(
        tmp_path / "gtfs",
        agency=_AGENCY,
        calendar=_CALENDAR_WEEKDAY,
        calendar_dates=_CAL_DATES_EMPTY,
        routes=_routes_csv([
            {"route_id": "R1", "agency_id": "7778019", "route_short_name": "13"}
        ]),
        trips=_trips_csv([
            {"route_id": "R1", "service_id": "WK", "trip_id": f"T0a{i}",
             "direction_id": 0, "shape_id": "S0_A"} for i in range(3)
        ] + [
            {"route_id": "R1", "service_id": "WK", "trip_id": "T0b",
             "direction_id": 0, "shape_id": "S0_B"},
            {"route_id": "R1", "service_id": "WK", "trip_id": "T1",
             "direction_id": 1, "shape_id": "S1"},
        ]),
        shapes=_shapes_csv({
            "S0_A": [(-6.30, 53.30100), (-6.20, 53.30100)],
            "S0_B": [(-6.30, 53.30200), (-6.20, 53.30200)],
            "S1":   [(-6.20, 53.30000), (-6.30, 53.30000)],
        }),
        stop_times=_stop_times_csv([]),
        stops=_stops_csv([]),
    )
    routes, _ = build(d, "2026-05-05")
    feats = routes["features"]
    assert len(feats) == 2
    lats = {round(c[1], 5) for f in feats for c in f["geometry"]["coordinates"]}
    # S0_B's lat (53.30200) is the runner-up shape — must not appear.
    assert 53.30200 not in lats
    # Canonical (S0_A at 53.30100) and the other-direction (S1 at
    # 53.30000) should both appear.
    assert 53.30100 in lats
    assert 53.30000 in lats


def test_E5_commuter_agency_7778006_excluded(tmp_path):
    d = _write_gtfs(
        tmp_path / "gtfs",
        agency=_AGENCY,
        calendar=_CALENDAR_WEEKDAY,
        calendar_dates=_CAL_DATES_EMPTY,
        routes=_routes_csv([
            {"route_id": "R1", "agency_id": "7778006", "route_short_name": "120"},
            {"route_id": "R2", "agency_id": "7778019", "route_short_name": "13"},
        ]),
        trips=_trips_csv([
            {"route_id": "R1", "service_id": "WK", "trip_id": "T1",
             "direction_id": 0, "shape_id": "S1"},
            {"route_id": "R2", "service_id": "WK", "trip_id": "T2",
             "direction_id": 0, "shape_id": "S2"},
        ]),
        shapes=_shapes_csv({
            "S1": [(-7.00, 53.30), (-7.10, 53.30)],
            "S2": [(-6.30, 53.30), (-6.20, 53.30)],
        }),
        stop_times=_stop_times_csv([]),
        stops=_stops_csv([]),
    )
    routes, _ = build(d, "2026-05-05")
    shorts = {s for f in routes["features"] for s in f["properties"]["routes"]}
    assert shorts == {"13"}


def test_E6_sunday_only_service_not_active_on_tuesday(tmp_path):
    d = _write_gtfs(
        tmp_path / "gtfs",
        agency=_AGENCY,
        calendar=_CALENDAR_WEEKDAY,
        calendar_dates=_CAL_DATES_EMPTY,
        routes=_routes_csv([
            {"route_id": "R1", "agency_id": "7778019", "route_short_name": "13"}
        ]),
        trips=_trips_csv([
            {"route_id": "R1", "service_id": "SUN", "trip_id": "T_SUN",
             "direction_id": 0, "shape_id": "S1"}
        ]),
        shapes=_shapes_csv({"S1": [(-6.30, 53.30), (-6.20, 53.30)]}),
        stop_times=_stop_times_csv([]),
        stops=_stops_csv([]),
    )
    routes, _ = build(d, "2026-05-05")
    assert routes["features"] == []


def test_E7_route_with_no_active_trips_today_excluded(tmp_path):
    """Route exists in routes.txt but every trip uses an out-of-window
    service. Pipeline should drop the route from the output."""
    d = _write_gtfs(
        tmp_path / "gtfs",
        agency=_AGENCY,
        calendar=(
            "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\n"
            "EXPIRED,1,1,1,1,1,0,0,20240101,20241231\n"
        ),
        calendar_dates=_CAL_DATES_EMPTY,
        routes=_routes_csv([
            {"route_id": "R1", "agency_id": "7778019", "route_short_name": "13"}
        ]),
        trips=_trips_csv([
            {"route_id": "R1", "service_id": "EXPIRED", "trip_id": "T1",
             "direction_id": 0, "shape_id": "S1"}
        ]),
        shapes=_shapes_csv({"S1": [(-6.30, 53.30), (-6.20, 53.30)]}),
        stop_times=_stop_times_csv([]),
        stops=_stops_csv([]),
    )
    routes, _ = build(d, "2026-05-05")
    assert routes["features"] == []


# --------------------------------------------------------------------------
# G: geometry fidelity
# --------------------------------------------------------------------------


def test_G1_feature_geometry_matches_representative_shape_exactly(tmp_path):
    """The feature's coordinates should be the exact GTFS shape points
    in their original order — no smoothing, no quantization, no
    densification."""
    coords = [(-6.30, 53.30), (-6.28, 53.305), (-6.26, 53.31), (-6.20, 53.30)]
    d = _write_gtfs(
        tmp_path / "gtfs",
        agency=_AGENCY,
        calendar=_CALENDAR_WEEKDAY,
        calendar_dates=_CAL_DATES_EMPTY,
        routes=_routes_csv([
            {"route_id": "R1", "agency_id": "7778019", "route_short_name": "13"}
        ]),
        trips=_trips_csv([
            {"route_id": "R1", "service_id": "WK", "trip_id": "T1",
             "direction_id": 0, "shape_id": "S1"}
        ]),
        shapes=_shapes_csv({"S1": coords}),
        stop_times=_stop_times_csv([]),
        stops=_stops_csv([]),
    )
    routes, _ = build(d, "2026-05-05")
    output = routes["features"][0]["geometry"]["coordinates"]
    assert output == [list(c) for c in coords]


def test_G2_loop_route_renders_correctly(tmp_path):
    """A loop route (start == end) should emit one Feature whose
    geometry is the loop, not split or rejected."""
    coords = [
        (-6.30, 53.30), (-6.28, 53.32), (-6.26, 53.32),
        (-6.26, 53.30), (-6.30, 53.30),
    ]
    d = _write_gtfs(
        tmp_path / "gtfs",
        agency=_AGENCY,
        calendar=_CALENDAR_WEEKDAY,
        calendar_dates=_CAL_DATES_EMPTY,
        routes=_routes_csv([
            {"route_id": "R1", "agency_id": "7778019", "route_short_name": "99",
             "route_long_name": "Phoenix Park Loop"}
        ]),
        trips=_trips_csv([
            {"route_id": "R1", "service_id": "WK", "trip_id": "T1",
             "direction_id": 0, "shape_id": "S1"}
        ]),
        shapes=_shapes_csv({"S1": coords}),
        stop_times=_stop_times_csv([]),
        stops=_stops_csv([]),
    )
    routes, _ = build(d, "2026-05-05")
    assert len(routes["features"]) == 1
    out = routes["features"][0]["geometry"]["coordinates"]
    # First and last points coincide
    assert out[0] == out[-1]


def test_G3_degenerate_zero_length_segment_does_not_crash(tmp_path):
    """A shape with two consecutive identical points (zero-length
    segment) should still produce a valid feature."""
    coords = [(-6.30, 53.30), (-6.30, 53.30), (-6.20, 53.30)]
    d = _write_gtfs(
        tmp_path / "gtfs",
        agency=_AGENCY,
        calendar=_CALENDAR_WEEKDAY,
        calendar_dates=_CAL_DATES_EMPTY,
        routes=_routes_csv([
            {"route_id": "R1", "agency_id": "7778019", "route_short_name": "13"}
        ]),
        trips=_trips_csv([
            {"route_id": "R1", "service_id": "WK", "trip_id": "T1",
             "direction_id": 0, "shape_id": "S1"}
        ]),
        shapes=_shapes_csv({"S1": coords}),
        stop_times=_stop_times_csv([]),
        stops=_stops_csv([]),
    )
    routes, _ = build(d, "2026-05-05")
    assert len(routes["features"]) == 1


def test_G4_long_route_has_no_huge_jumps(tmp_path):
    """A route with densely-sampled shape points (every ~50 m) should
    have output edges no longer than ~150 m. Catches accidental
    smoothing/snapping that could leave a jump."""
    # 100 sample points east at ~50 m spacing (0.0007 deg lon)
    coords = [(-6.30 + i * 0.0007, 53.30) for i in range(100)]
    d = _write_gtfs(
        tmp_path / "gtfs",
        agency=_AGENCY,
        calendar=_CALENDAR_WEEKDAY,
        calendar_dates=_CAL_DATES_EMPTY,
        routes=_routes_csv([
            {"route_id": "R1", "agency_id": "7778019", "route_short_name": "13"}
        ]),
        trips=_trips_csv([
            {"route_id": "R1", "service_id": "WK", "trip_id": "T1",
             "direction_id": 0, "shape_id": "S1"}
        ]),
        shapes=_shapes_csv({"S1": coords}),
        stop_times=_stop_times_csv([]),
        stops=_stops_csv([]),
    )
    routes, _ = build(d, "2026-05-05")
    out = routes["features"][0]["geometry"]["coordinates"]
    assert _max_edge_m(out) < 150


# --------------------------------------------------------------------------
# C: categorisation in the pipeline
# --------------------------------------------------------------------------


def _build_with_route(tmp_path: Path, short: str, **kw):
    d = _write_gtfs(
        tmp_path / "gtfs",
        agency=_AGENCY,
        calendar=_CALENDAR_WEEKDAY,
        calendar_dates=_CAL_DATES_EMPTY,
        routes=_routes_csv([
            {"route_id": "R1", "agency_id": "7778019", "route_short_name": short}
        ]),
        trips=_trips_csv([
            {"route_id": "R1", "service_id": "WK", "trip_id": "T1",
             "direction_id": 0, "shape_id": "S1"}
        ]),
        shapes=_shapes_csv({"S1": [(-6.30, 53.30), (-6.20, 53.30)]}),
        stop_times=_stop_times_csv(kw.get("stop_times_rows", [])),
        stops=_stops_csv([]),
    )
    routes, _ = build(d, "2026-05-05")
    return routes["features"][0] if routes["features"] else None


def test_C_pipeline_attaches_correct_category_for_lettered_spine(tmp_path):
    f = _build_with_route(tmp_path, "C1")
    assert f["properties"]["category"] == "spine"


def test_C_pipeline_attaches_orbital_for_W_prefix(tmp_path):
    f = _build_with_route(tmp_path, "W4")
    assert f["properties"]["category"] == "orbital"


def test_C_pipeline_attaches_local_for_L_prefix(tmp_path):
    f = _build_with_route(tmp_path, "L25")
    assert f["properties"]["category"] == "local"


def test_C_pipeline_attaches_peak_for_X_prefix(tmp_path):
    f = _build_with_route(tmp_path, "X1")
    assert f["properties"]["category"] == "peak"


def test_C_pipeline_attaches_radial_for_low_frequency_numeric(tmp_path):
    # No 8 am trips -> not high-frequency -> radial.
    f = _build_with_route(tmp_path, "13")
    assert f["properties"]["category"] == "radial"


def test_C_high_frequency_numeric_promotes_to_spine(tmp_path):
    """Plain-numeric route with >=5 trips at the configured peak hour
    should be spine."""
    from gtfs_map.pipeline import HIGH_FREQUENCY_HOUR
    stop_times = [
        {"trip_id": f"T{i}", "stop_id": "X", "stop_sequence": 1,
         "departure_time": f"{HIGH_FREQUENCY_HOUR:02d}:{i:02d}:00"}
        for i in range(6)
    ]
    routes_csv = _routes_csv([
        {"route_id": "R1", "agency_id": "7778019", "route_short_name": "13"}
    ])
    trips_csv = _trips_csv([
        {"route_id": "R1", "service_id": "WK", "trip_id": f"T{i}",
         "direction_id": 0, "shape_id": "S1"}
        for i in range(6)
    ])
    d = _write_gtfs(
        tmp_path / "gtfs",
        agency=_AGENCY, calendar=_CALENDAR_WEEKDAY, calendar_dates=_CAL_DATES_EMPTY,
        routes=routes_csv, trips=trips_csv,
        shapes=_shapes_csv({"S1": [(-6.30, 53.30), (-6.20, 53.30)]}),
        stop_times=_stop_times_csv(stop_times), stops=_stops_csv([]),
    )
    routes, meta = build(d, "2026-05-05")
    assert routes["features"][0]["properties"]["category"] == "spine"
    assert meta["high_frequency_route_count"] >= 1


def test_C_high_frequency_local_stays_local(tmp_path):
    """A category-prefixed route (L*) doesn't get promoted even if
    high-frequency."""
    from gtfs_map.pipeline import HIGH_FREQUENCY_HOUR
    stop_times = [
        {"trip_id": f"T{i}", "stop_id": "X", "stop_sequence": 1,
         "departure_time": f"{HIGH_FREQUENCY_HOUR:02d}:{i*3:02d}:00"}
        for i in range(6)
    ]
    d = _write_gtfs(
        tmp_path / "gtfs",
        agency=_AGENCY, calendar=_CALENDAR_WEEKDAY, calendar_dates=_CAL_DATES_EMPTY,
        routes=_routes_csv([
            {"route_id": "R1", "agency_id": "7778019", "route_short_name": "L25"}
        ]),
        trips=_trips_csv([
            {"route_id": "R1", "service_id": "WK", "trip_id": f"T{i}",
             "direction_id": 0, "shape_id": "S1"}
            for i in range(6)
        ]),
        shapes=_shapes_csv({"S1": [(-6.30, 53.30), (-6.20, 53.30)]}),
        stop_times=_stop_times_csv(stop_times), stops=_stops_csv([]),
    )
    routes, _ = build(d, "2026-05-05")
    assert routes["features"][0]["properties"]["category"] == "local"


# --------------------------------------------------------------------------
# P: rollout-phase mapping
# --------------------------------------------------------------------------


def _build_with_phase_file(tmp_path: Path, short: str, phases_json: dict):
    d_root = tmp_path
    gtfs_dir = d_root / "gtfs"
    _write_gtfs(
        gtfs_dir,
        agency=_AGENCY, calendar=_CALENDAR_WEEKDAY, calendar_dates=_CAL_DATES_EMPTY,
        routes=_routes_csv([
            {"route_id": "R1", "agency_id": "7778019", "route_short_name": short}
        ]),
        trips=_trips_csv([
            {"route_id": "R1", "service_id": "WK", "trip_id": "T1",
             "direction_id": 0, "shape_id": "S1"}
        ]),
        shapes=_shapes_csv({"S1": [(-6.30, 53.30), (-6.20, 53.30)]}),
        stop_times=_stop_times_csv([]), stops=_stops_csv([]),
    )
    (d_root / "rollout-phases.json").write_text(json.dumps(phases_json))
    routes, meta = build(gtfs_dir, "2026-05-05")
    return routes, meta


def test_P1_route_listed_in_phases_gets_phase_property(tmp_path):
    routes, meta = _build_with_phase_file(
        tmp_path, "C1",
        {"2": {"date": "2021-11-28", "routes": ["C1"]}},
    )
    assert routes["features"][0]["properties"]["phase"] == "2"
    assert meta["route_phase"]["C1"] == "2"


def test_P2_route_not_in_phases_marked_legacy(tmp_path):
    routes, meta = _build_with_phase_file(
        tmp_path, "13", {"2": {"date": "2021-11-28", "routes": ["C1"]}}
    )
    assert routes["features"][0]["properties"]["phase"] == "legacy"


def test_P3_meta_carries_rollout_phases_dict(tmp_path):
    phases = {"1": {"date": "2021-06-27", "routes": ["H1"]}}
    _, meta = _build_with_phase_file(tmp_path, "H1", phases)
    assert meta["rollout_phases"] == phases
    assert meta["phase_route_counts"]["1"] == 1


# --------------------------------------------------------------------------
# S: style/colour mapping
# --------------------------------------------------------------------------


def test_S1_each_category_has_distinct_colour():
    from gtfs_map.colour import SPINE_COLOURS  # not used here directly
    from gtfs_map.category import CATEGORY_COLOURS
    cs = list(CATEGORY_COLOURS.values())
    assert len(set(cs)) == 5  # 5 categories, 5 distinct colours


def test_S2_feature_colour_matches_category_colour(tmp_path):
    from gtfs_map.category import CATEGORY_COLOURS
    f = _build_with_route(tmp_path, "C1")
    assert f["properties"]["colour"] == CATEGORY_COLOURS["spine"]


# --------------------------------------------------------------------------
# Bundling integration (step 3 of the cross-route bundling work)
# --------------------------------------------------------------------------


def test_bundle_two_lettered_spines_share_routes_property(tmp_path):
    """Two lettered spine routes (C1, C2) on the same corridor each
    walk and emit a Feature; both carry routes=[C1, C2] and the same
    spine colour, so they stack as a single visually-thicker red
    line at the rendering layer."""
    d = _write_gtfs(
        tmp_path / "gtfs",
        agency=_AGENCY,
        calendar=_CALENDAR_WEEKDAY,
        calendar_dates=_CAL_DATES_EMPTY,
        routes=_routes_csv([
            {"route_id": "R1", "agency_id": "7778019", "route_short_name": "C1"},
            {"route_id": "R2", "agency_id": "7778019", "route_short_name": "C2"},
        ]),
        trips=_trips_csv([
            {"route_id": "R1", "service_id": "WK", "trip_id": "T1",
             "direction_id": 0, "shape_id": "S1"},
            {"route_id": "R2", "service_id": "WK", "trip_id": "T2",
             "direction_id": 0, "shape_id": "S1"},  # SAME shape
        ]),
        shapes=_shapes_csv({"S1": [(-6.30, 53.30), (-6.20, 53.30)]}),
        stop_times=_stop_times_csv([]),
        stops=_stops_csv([]),
    )
    routes, _ = build(d, "2026-05-05")
    feats = routes["features"]
    assert len(feats) == 2
    for f in feats:
        assert f["properties"]["category"] == "spine"
        assert sorted(f["properties"]["routes"]) == ["C1", "C2"]


def test_bundle_two_unrelated_numeric_routes_share_at_category_level(tmp_path):
    """Routes 13 and 16 on the same corridor are both radial — each
    walks its own line and emits a Feature carrying routes=[13, 16].
    Both stacked at the same purple colour render as a single line,
    even though the routes aren't a deliberate "bundle group"."""
    d = _write_gtfs(
        tmp_path / "gtfs",
        agency=_AGENCY,
        calendar=_CALENDAR_WEEKDAY,
        calendar_dates=_CAL_DATES_EMPTY,
        routes=_routes_csv([
            {"route_id": "R1", "agency_id": "7778019", "route_short_name": "13"},
            {"route_id": "R2", "agency_id": "7778019", "route_short_name": "16"},
        ]),
        trips=_trips_csv([
            {"route_id": "R1", "service_id": "WK", "trip_id": "T1",
             "direction_id": 0, "shape_id": "S1"},
            {"route_id": "R2", "service_id": "WK", "trip_id": "T2",
             "direction_id": 0, "shape_id": "S1"},
        ]),
        shapes=_shapes_csv({"S1": [(-6.30, 53.30), (-6.20, 53.30)]}),
        stop_times=_stop_times_csv([]),
        stops=_stops_csv([]),
    )
    routes, _ = build(d, "2026-05-05")
    feats = routes["features"]
    assert len(feats) == 2
    for f in feats:
        assert f["properties"]["category"] == "radial"
        assert sorted(f["properties"]["routes"]) == ["13", "16"]


def test_bundle_two_far_apart_routes_stay_separate(tmp_path):
    """Two routes whose shapes are geographically distinct (>100 m
    apart) emit TWO Features each with a singleton `routes` list."""
    d = _write_gtfs(
        tmp_path / "gtfs",
        agency=_AGENCY,
        calendar=_CALENDAR_WEEKDAY,
        calendar_dates=_CAL_DATES_EMPTY,
        routes=_routes_csv([
            {"route_id": "R1", "agency_id": "7778019", "route_short_name": "13"},
            {"route_id": "R2", "agency_id": "7778019", "route_short_name": "16"},
        ]),
        trips=_trips_csv([
            {"route_id": "R1", "service_id": "WK", "trip_id": "T1",
             "direction_id": 0, "shape_id": "S1"},
            {"route_id": "R2", "service_id": "WK", "trip_id": "T2",
             "direction_id": 0, "shape_id": "S2"},
        ]),
        shapes=_shapes_csv({
            "S1": [(-6.30, 53.30), (-6.20, 53.30)],
            "S2": [(-6.30, 53.40), (-6.20, 53.40)],  # 11 km north
        }),
        stop_times=_stop_times_csv([]),
        stops=_stops_csv([]),
    )
    routes, _ = build(d, "2026-05-05")
    assert len(routes["features"]) == 2
    shorts = sorted(r for f in routes["features"] for r in f["properties"]["routes"])
    assert shorts == ["13", "16"]
    for f in routes["features"]:
        assert len(f["properties"]["routes"]) == 1


def test_bundle_does_not_cross_categories(tmp_path):
    """A spine route (C1) and an L-route (L25) with IDENTICAL
    shapes don't bundle because they're in different categories.
    Bundling is per-category to prevent route_set cascading
    across category boundaries."""
    d = _write_gtfs(
        tmp_path / "gtfs",
        agency=_AGENCY,
        calendar=_CALENDAR_WEEKDAY,
        calendar_dates=_CAL_DATES_EMPTY,
        routes=_routes_csv([
            {"route_id": "R1", "agency_id": "7778019", "route_short_name": "C1"},
            {"route_id": "R2", "agency_id": "7778019", "route_short_name": "L25"},
        ]),
        trips=_trips_csv([
            {"route_id": "R1", "service_id": "WK", "trip_id": "T1",
             "direction_id": 0, "shape_id": "S1"},
            {"route_id": "R2", "service_id": "WK", "trip_id": "T2",
             "direction_id": 0, "shape_id": "S1"},
        ]),
        shapes=_shapes_csv({"S1": [(-6.30, 53.30), (-6.20, 53.30)]}),
        stop_times=_stop_times_csv([]),
        stops=_stops_csv([]),
    )
    routes, _ = build(d, "2026-05-05")
    assert len(routes["features"]) == 2
    cats = sorted(f["properties"]["category"] for f in routes["features"])
    assert cats == ["local", "spine"]
    # Each feature has just its own route in routes
    for f in routes["features"]:
        assert len(f["properties"]["routes"]) == 1


def test_bundle_three_lettered_spines_each_walk_carries_all_three(tmp_path):
    """Three lettered spine routes (C1, C2, C3) on identical shape
    each walk and emit a Feature; all three carry routes=[C1, C2,
    C3]. Width at the rendering layer scales with len(routes), so
    the stacked features render as a single visually-thicker red
    trunk."""
    d = _write_gtfs(
        tmp_path / "gtfs",
        agency=_AGENCY,
        calendar=_CALENDAR_WEEKDAY,
        calendar_dates=_CAL_DATES_EMPTY,
        routes=_routes_csv([
            {"route_id": "R1", "agency_id": "7778019", "route_short_name": "C1"},
            {"route_id": "R2", "agency_id": "7778019", "route_short_name": "C2"},
            {"route_id": "R3", "agency_id": "7778019", "route_short_name": "C3"},
        ]),
        trips=_trips_csv([
            {"route_id": "R1", "service_id": "WK", "trip_id": "T1",
             "direction_id": 0, "shape_id": "S1"},
            {"route_id": "R2", "service_id": "WK", "trip_id": "T2",
             "direction_id": 0, "shape_id": "S1"},
            {"route_id": "R3", "service_id": "WK", "trip_id": "T3",
             "direction_id": 0, "shape_id": "S1"},
        ]),
        shapes=_shapes_csv({"S1": [(-6.30, 53.30), (-6.20, 53.30)]}),
        stop_times=_stop_times_csv([]),
        stops=_stops_csv([]),
    )
    routes, _ = build(d, "2026-05-05")
    feats = routes["features"]
    assert len(feats) == 3
    for f in feats:
        assert f["properties"]["category"] == "spine"
        assert sorted(f["properties"]["routes"]) == ["C1", "C2", "C3"]


def test_bundle_hf_numeric_with_letter_variant_split_by_category(tmp_path):
    """Route 39 (HF-promoted to spine, red) and 39A (radial, purple
    — variants don't inherit HF) share a corridor. Per-category
    collapse emits TWO Features at that corridor: one spine [39]
    and one radial [39A]. Each is drawn in its category colour;
    the front-end's z-order puts spine on top."""
    from gtfs_map.pipeline import HIGH_FREQUENCY_HOUR
    # 6 trips at the HF hour for route "39" -> high-frequency.
    hf_stop_times = [
        {"trip_id": f"T39_{i}", "stop_id": "X", "stop_sequence": 1,
         "departure_time": f"{HIGH_FREQUENCY_HOUR:02d}:{i:02d}:00"}
        for i in range(6)
    ]
    trip_rows = [
        {"route_id": "R1", "service_id": "WK", "trip_id": f"T39_{i}",
         "direction_id": 0, "shape_id": "S1"}
        for i in range(6)
    ] + [
        {"route_id": "R2", "service_id": "WK", "trip_id": "T39A",
         "direction_id": 0, "shape_id": "S1"},
    ]
    d = _write_gtfs(
        tmp_path / "gtfs",
        agency=_AGENCY,
        calendar=_CALENDAR_WEEKDAY,
        calendar_dates=_CAL_DATES_EMPTY,
        routes=_routes_csv([
            {"route_id": "R1", "agency_id": "7778019", "route_short_name": "39"},
            {"route_id": "R2", "agency_id": "7778019", "route_short_name": "39A"},
        ]),
        trips=_trips_csv(trip_rows),
        shapes=_shapes_csv({"S1": [(-6.30, 53.30), (-6.20, 53.30)]}),
        stop_times=_stop_times_csv(hf_stop_times),
        stops=_stops_csv([]),
    )
    routes, _ = build(d, "2026-05-05")
    feats = routes["features"]
    assert len(feats) == 2
    by_cat = {f["properties"]["category"]: f for f in feats}
    assert by_cat["spine"]["properties"]["routes"] == ["39"]
    assert by_cat["radial"]["properties"]["routes"] == ["39A"]
