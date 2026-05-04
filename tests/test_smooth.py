from shapely.geometry import LineString

from gtfs_map.smooth import smooth_line


def test_straight_line_unchanged():
    line = LineString([(-6.30, 53.30), (-6.20, 53.30)])
    out = smooth_line(line, tolerance_m=3.0)
    assert list(out.coords)[0] == (-6.30, 53.30) or abs(out.coords[0][0] + 6.30) < 1e-6


def test_staircase_input_collapses_to_far_fewer_points():
    # Simulate a 2 m quantization staircase: alternating tiny zig-zag
    # points every ~2 m around a roughly straight east-west line.
    coords = []
    for i in range(40):
        # base lon advances 0.0001 per step (~6.7 m), lat oscillates by 2 m
        lon = -6.30 + i * 0.0001
        lat = 53.30 + (0.000018 if i % 2 else 0.0)
        coords.append((lon, lat))
    raw = LineString(coords)
    smoothed = smooth_line(raw, tolerance_m=3.0)
    assert len(list(smoothed.coords)) < len(coords) // 2, (
        f"expected aggressive simplification, kept {len(list(smoothed.coords))} of {len(coords)}"
    )


def test_handles_short_lines_gracefully():
    line = LineString([(-6.30, 53.30), (-6.30 + 1e-7, 53.30)])  # ~1 cm
    out = smooth_line(line, tolerance_m=3.0)
    assert out is not None
