from gtfs_map.category import categorise, category_colour, CATEGORY_COLOURS


def test_lettered_spine_routes_are_spine():
    for name in ("C1", "C6", "E1", "F3", "G2", "H3"):
        assert categorise(name) == "spine"


def test_local_routes_are_local():
    for name in ("L1", "L25", "L52", "L89"):
        assert categorise(name) == "local"


def test_orbital_prefixes_w_n_s_are_orbital():
    for name in ("W2", "W4", "W6", "N2", "N4", "N6", "S2", "S4", "S6", "S8"):
        assert categorise(name) == "orbital"


def test_p_prefix_is_peak():
    assert categorise("P29") == "peak"


def test_x_prefix_express_routes_are_peak():
    # Dublin Bus X-routes (X1, X2, X25..X32) are peak express services
    for name in ("X1", "X2", "X25", "X32"):
        assert categorise(name) == "peak"


def test_x_suffix_express_routes_are_peak():
    # Pre-BusConnects express variants suffix the parent number with X:
    # 39X, 70X, 270X, 33X. They're peak/express, not radial.
    for name in ("39X", "70X", "270X", "33X", "16x"):
        assert categorise(name) == "peak"


def test_x_suffix_express_stays_peak_when_high_frequency():
    # An express running every 10 minutes is still an express - not
    # promoted to spine like a plain-numeric HF route would be.
    assert categorise("39X", high_frequency=True) == "peak"


def test_plain_numeric_and_other_routes_are_radial():
    for name in ("13", "46A", "155", "102", "102C", "73", "104"):
        assert categorise(name) == "radial"


def test_spine_letter_only_inside_a_h_range():
    # I1 isn't a BusConnects spine — must fall back to radial
    assert categorise("I1") == "radial"
    # Z9 isn't a spine
    assert categorise("Z9") == "radial"


def test_each_category_has_a_distinct_hex_colour():
    cats = ("spine", "orbital", "local", "peak", "radial")
    colours = [category_colour(c) for c in cats]
    assert all(c.startswith("#") and len(c) == 7 for c in colours)
    assert len(set(colours)) == 5


def test_category_colours_match_the_brief():
    # Red for spines, blue for orbitals, green for locals, orange for peak, purple for radial.
    assert category_colour("spine").lower() in {"#d62728", "#e6194b", "#dc143c", "#cc0000", "#e74c3c"}
    # We allow any reasonable red — pinning the exact hex would be overspecified.
    # But we DO want the broad hue to be right:
    def hue(hex_s):
        r = int(hex_s[1:3], 16); g = int(hex_s[3:5], 16); b = int(hex_s[5:7], 16)
        return (r, g, b)
    r, g, b = hue(category_colour("spine"))
    assert r > g and r > b, "spine should be red-dominant"
    r, g, b = hue(category_colour("orbital"))
    assert b > r and b > g, "orbital should be blue-dominant"
    r, g, b = hue(category_colour("local"))
    assert g > r and g > b, "local should be green-dominant"
    r, g, b = hue(category_colour("peak"))
    assert r > b and g > b, "peak should be orange (red+green, low blue)"
    r, g, b = hue(category_colour("radial"))
    # Purple: R and B both high, G lower
    assert r > g and b > g, "radial should be purple-dominant"


def test_category_colours_dict_matches_function():
    for cat in CATEGORY_COLOURS:
        assert category_colour(cat) == CATEGORY_COLOURS[cat]


def test_high_frequency_flag_promotes_only_plain_radials_to_spine():
    # A plain numeric route normally lands in radial; with the
    # high_frequency flag set it should be drawn red as a spine.
    assert categorise("13") == "radial"
    assert categorise("13", high_frequency=True) == "spine"
    assert categorise("46A", high_frequency=True) == "spine"


def test_orbital_routes_stay_blue_irrespective_of_frequency():
    # User's rule: W/N/S routes always render blue regardless of how
    # often they run.
    for name in ("W2", "W4", "W6", "N2", "N4", "N6", "S2", "S4", "S6", "S8"):
        assert categorise(name, high_frequency=False) == "orbital"
        assert categorise(name, high_frequency=True) == "orbital"


def test_local_peak_keep_their_category_when_high_frequency():
    # Locals and peak routes also retain their nature regardless of
    # frequency — only plain-numeric radials get promoted.
    assert categorise("L25", high_frequency=True) == "local"
    assert categorise("X1", high_frequency=True) == "peak"
    assert categorise("P29", high_frequency=True) == "peak"


def test_lettered_spines_stay_spine_regardless_of_frequency_flag():
    assert categorise("C1", high_frequency=False) == "spine"
    assert categorise("C1", high_frequency=True) == "spine"
