import pandas as pd

from gtfs_map.shapes import representative_shape_ids, build_linestrings


def test_representative_shape_picks_most_frequent_per_route_and_direction():
    trips = pd.DataFrame(
        [
            # route R1, direction 0 — shape S_A is most common (3 trips)
            {"route_id": "R1", "direction_id": 0, "shape_id": "S_A"},
            {"route_id": "R1", "direction_id": 0, "shape_id": "S_A"},
            {"route_id": "R1", "direction_id": 0, "shape_id": "S_A"},
            {"route_id": "R1", "direction_id": 0, "shape_id": "S_B"},
            # route R1, direction 1 — shape S_C
            {"route_id": "R1", "direction_id": 1, "shape_id": "S_C"},
            {"route_id": "R1", "direction_id": 1, "shape_id": "S_C"},
            # route R2, direction 0 — single trip
            {"route_id": "R2", "direction_id": 0, "shape_id": "S_D"},
        ]
    )

    rep = representative_shape_ids(trips)

    # rep is a dict {(route_id, direction_id): shape_id}
    assert rep[("R1", 0)] == "S_A"
    assert rep[("R1", 1)] == "S_C"
    assert rep[("R2", 0)] == "S_D"
    assert ("R1", 0) in rep and ("R1", 1) in rep  # both directions kept


def test_routes_with_only_one_direction_get_one_shape():
    trips = pd.DataFrame(
        [
            {"route_id": "ONEDIR", "direction_id": 0, "shape_id": "X"},
            {"route_id": "ONEDIR", "direction_id": 0, "shape_id": "X"},
        ]
    )
    rep = representative_shape_ids(trips)
    assert rep == {("ONEDIR", 0): "X"}


def test_build_linestrings_groups_shape_points_by_id_in_sequence_order():
    shapes = pd.DataFrame(
        [
            # shape A: three points, sequence out of order in the file
            {"shape_id": "A", "shape_pt_lat": 53.30, "shape_pt_lon": -6.20, "shape_pt_sequence": 1},
            {"shape_id": "A", "shape_pt_lat": 53.32, "shape_pt_lon": -6.22, "shape_pt_sequence": 3},
            {"shape_id": "A", "shape_pt_lat": 53.31, "shape_pt_lon": -6.21, "shape_pt_sequence": 2},
            # shape B: two points
            {"shape_id": "B", "shape_pt_lat": 53.40, "shape_pt_lon": -6.30, "shape_pt_sequence": 1},
            {"shape_id": "B", "shape_pt_lat": 53.41, "shape_pt_lon": -6.31, "shape_pt_sequence": 2},
        ]
    )

    lines = build_linestrings(shapes)
    assert set(lines.keys()) == {"A", "B"}

    a_coords = list(lines["A"].coords)
    # Coordinates are (lon, lat) per GeoJSON convention, ordered by sequence
    assert a_coords == [(-6.20, 53.30), (-6.21, 53.31), (-6.22, 53.32)]

    b_coords = list(lines["B"].coords)
    assert b_coords == [(-6.30, 53.40), (-6.31, 53.41)]
