from gtfs_map.classify import classify_route


def test_lettered_spine_routes_are_classified_by_letter():
    assert classify_route("A1") == ("spine", "A")
    assert classify_route("A2") == ("spine", "A")
    assert classify_route("H3") == ("spine", "H")
    assert classify_route("C6") == ("spine", "C")


def test_plain_numeric_routes_are_other():
    assert classify_route("13") == ("other", None)
    assert classify_route("46A") == ("other", None)
    assert classify_route("155") == ("other", None)


def test_local_express_night_school_routes_are_other_not_spine():
    # L, X, N, S, W are local/express/night/school/west prefixes, not spines
    assert classify_route("L25") == ("other", None)
    assert classify_route("X25") == ("other", None)
    assert classify_route("N4") == ("other", None)
    assert classify_route("S2") == ("other", None)
    assert classify_route("W2") == ("other", None)


def test_only_letters_a_through_h_count_as_spines():
    # Z9 is not a spine letter; if it ever appears it should be "other"
    assert classify_route("Z9") == ("other", None)
    # I-spine doesn't exist in BusConnects
    assert classify_route("I1") == ("other", None)


def test_spine_must_be_letter_then_digits_only():
    # "A1B" is a sub-variant — treat as other to avoid mis-bundling
    assert classify_route("A1B") == ("other", None)
    assert classify_route("AB1") == ("other", None)
