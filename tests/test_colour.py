from gtfs_map.colour import spine_colour, route_colour, SPINE_COLOURS


def test_each_busconnects_spine_has_a_distinct_colour():
    letters = list("ABCDEFGH")
    colours = [spine_colour(l) for l in letters]
    # Every letter has a colour
    assert all(c.startswith("#") and len(c) == 7 for c in colours)
    # All eight are distinct
    assert len(set(colours)) == 8


def test_spine_colours_are_stable():
    # Re-running should return the same colour (matters for diff-friendly
    # GeoJSON output and for the legend matching the rendered map).
    assert spine_colour("A") == spine_colour("A")
    assert spine_colour("H") == SPINE_COLOURS["H"]


def test_route_colour_is_deterministic_per_route_name():
    # Same input → same output, every time.
    assert route_colour("46A") == route_colour("46A")
    assert route_colour("L25") == route_colour("L25")


def test_route_colour_returns_a_hex_colour_string():
    c = route_colour("13")
    assert isinstance(c, str)
    assert c.startswith("#")
    assert len(c) == 7
