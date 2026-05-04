"""Combining inbound + outbound shapes of the same route."""

from shapely.geometry import LineString, MultiLineString

from gtfs_map.merge import combine_directions


def _len_m(line: LineString) -> float:
    """Approximate length in metres at Dublin's latitude (lazy)."""
    # 1 degree lon ≈ 67 km at lat 53; 1 degree lat ≈ 111 km. Good
    # enough for fixture sizing — the real implementation is in ITM.
    coords = list(line.coords)
    total = 0.0
    for (x1, y1), (x2, y2) in zip(coords, coords[1:]):
        total += (((x2 - x1) * 67_000) ** 2 + ((y2 - y1) * 111_000) ** 2) ** 0.5
    return total


def test_a_route_with_only_one_direction_returns_that_one_line():
    line = LineString([(-6.30, 53.30), (-6.20, 53.30)])
    result = combine_directions(line, None)
    assert len(result) == 1
    assert list(result[0].coords) == list(line.coords)


def test_two_identical_directions_collapse_to_one_line():
    line = LineString([(-6.30, 53.30), (-6.20, 53.30)])
    result = combine_directions(line, line, threshold_m=30)
    assert len(result) == 1


def test_two_parallel_directions_within_threshold_collapse_to_one():
    # 'b' is offset ~6 m north of 'a' (1 degree lat = 111000 m, so
    # 0.00005 deg ≈ 5.5 m) — well inside the 30 m corridor.
    a = LineString([(-6.30, 53.30), (-6.20, 53.30)])
    b = LineString([(-6.30, 53.30005), (-6.20, 53.30005)])
    result = combine_directions(a, b, threshold_m=30)
    assert len(result) == 1


def test_two_far_apart_directions_keep_both_lines():
    # 'b' is ~11 km north of 'a' — way outside the 30 m corridor.
    a = LineString([(-6.30, 53.30), (-6.20, 53.30)])
    b = LineString([(-6.30, 53.40), (-6.20, 53.40)])
    result = combine_directions(a, b, threshold_m=30)
    # At minimum: dir-a kept whole + dir-b's leftover (also whole).
    assert len(result) == 2


def test_partial_overlap_keeps_dir_a_plus_only_the_diverged_part_of_dir_b():
    # 'a' goes east 20 km on lat 53.30.
    # 'b' starts at the same place, goes 10 km east on the same line,
    # then turns north 10 km. The first half overlaps; the second
    # half diverges and should be kept.
    a = LineString([(-6.30, 53.30), (-6.10, 53.30)])
    b = LineString([(-6.30, 53.30), (-6.20, 53.30), (-6.20, 53.40)])
    result = combine_directions(a, b, threshold_m=30)
    assert len(result) >= 2

    # Total kept geometry length should be (full a) + (the 10 km north
    # leg of b only) — definitely less than (a + full b).
    full_b_length = _len_m(b)
    kept = sum(_len_m(g) for g in result)
    a_only = _len_m(a)
    assert kept < a_only + full_b_length, (
        "expected the overlapping ~10 km of b to have been dropped"
    )


def test_drops_tiny_residual_fragments():
    # 'b' is 'a' but with a tiny 1 m wobble nowhere near the threshold.
    # Any leftover after the corridor difference should be pruned as
    # noise rather than producing a 1 m feature.
    a = LineString([(-6.30, 53.30), (-6.20, 53.30)])
    b = LineString([(-6.30, 53.30000005), (-6.20, 53.30000005)])
    result = combine_directions(a, b, threshold_m=30)
    assert len(result) == 1
