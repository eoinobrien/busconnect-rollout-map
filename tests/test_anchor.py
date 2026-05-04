"""Anchoring an offset line back to its real stops."""

from shapely.geometry import LineString

from gtfs_map.anchor import anchor_to_stops


def test_a_line_with_no_stops_is_unchanged():
    line = LineString([(-6.30, 53.30), (-6.20, 53.30)])
    assert list(anchor_to_stops(line, []).coords) == list(line.coords)


def test_each_stop_within_threshold_gets_a_vertex_on_the_line():
    # A line approximately east-west at lat 53.30001 (offset ~1 m
    # north of stops on lat 53.30000). Each stop should pull the
    # nearest vertex back onto its true position.
    line = LineString([
        (-6.30, 53.30001),
        (-6.25, 53.30001),
        (-6.20, 53.30001),
    ])
    stops = [(-6.30, 53.30), (-6.25, 53.30), (-6.20, 53.30)]

    anchored = anchor_to_stops(line, stops, max_distance_m=20.0)
    coords = list(anchored.coords)

    # Every stop should now appear in the line.
    for s in stops:
        assert s in coords, f"stop {s} missing from {coords}"


def test_far_stops_outside_threshold_are_ignored():
    line = LineString([(-6.30, 53.30), (-6.20, 53.30)])
    far_stop = (-6.30, 53.40)  # ~11 km away — well outside any threshold

    anchored = anchor_to_stops(line, [far_stop], max_distance_m=30.0)
    assert far_stop not in list(anchored.coords)


def test_handles_a_line_only_two_vertices_long():
    line = LineString([(-6.30, 53.30001), (-6.20, 53.30001)])
    stops = [(-6.30, 53.30), (-6.20, 53.30)]
    anchored = anchor_to_stops(line, stops, max_distance_m=20.0)
    coords = list(anchored.coords)
    assert len(coords) >= 2
    for s in stops:
        assert s in coords
