from gtfs_map.consolidate import consolidate_features


def _f(coords, routes):
    return {
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": coords},
        "properties": {"route_set": list(routes), "route_count": len(routes)},
    }


def test_isolated_features_pass_through():
    a = _f([(-6.30, 53.30), (-6.20, 53.30)], ["X"])
    b = _f([(-6.10, 53.40), (-6.00, 53.40)], ["Y"])
    out = consolidate_features([a, b])
    assert len(out) == 2


def test_adjacent_features_with_full_overlap_collapse_to_one():
    a = _f([(-6.30, 53.30), (-6.25, 53.30)], ["A", "B", "C"])
    b = _f([(-6.25, 53.30), (-6.20, 53.30)], ["A", "B", "C"])  # same route_set
    out = consolidate_features([a, b])
    assert len(out) == 1
    assert sorted(out[0]["properties"]["route_set"]) == ["A", "B", "C"]


def test_adjacent_features_with_high_jaccard_overlap_merge_with_union():
    # Both share A,B,C; one has extra D. Jaccard = 3/4 = 0.75 > 0.5 → merge.
    a = _f([(-6.30, 53.30), (-6.25, 53.30)], ["A", "B", "C"])
    b = _f([(-6.25, 53.30), (-6.20, 53.30)], ["A", "B", "C", "D"])
    out = consolidate_features([a, b], jaccard_threshold=0.5)
    assert len(out) == 1
    assert sorted(out[0]["properties"]["route_set"]) == ["A", "B", "C", "D"]


def test_adjacent_features_with_low_overlap_stay_separate():
    # No common routes → Jaccard = 0 → keep separate.
    a = _f([(-6.30, 53.30), (-6.25, 53.30)], ["A"])
    b = _f([(-6.25, 53.30), (-6.20, 53.30)], ["B"])
    out = consolidate_features([a, b], jaccard_threshold=0.5)
    assert len(out) == 2


def test_branch_at_junction_keeps_branches_separate_when_disjoint():
    # a→b shared {A,B}, b→c is just {A}, b→d is just {B}.
    # Jaccard(shared, A-only) = 1/2 = 0.5 — at the threshold.
    # Jaccard(A-only, B-only) = 0 — never merged.
    shared = _f([(-6.30, 53.30), (-6.25, 53.30)], ["A", "B"])
    a_only = _f([(-6.25, 53.30), (-6.20, 53.30)], ["A"])
    b_only = _f([(-6.25, 53.30), (-6.25, 53.40)], ["B"])
    out = consolidate_features([shared, a_only, b_only], jaccard_threshold=0.6)
    # With threshold 0.6 the branches stay separate from the trunk.
    sets = [tuple(sorted(f["properties"]["route_set"])) for f in out]
    assert ("A",) in sets
    assert ("B",) in sets


def test_chain_of_three_consecutive_segments_consolidates():
    # All three share routes A,B,C. They should merge into one line.
    a = _f([(-6.30, 53.30), (-6.25, 53.30)], ["A", "B", "C"])
    b = _f([(-6.25, 53.30), (-6.20, 53.30)], ["A", "B", "C"])
    c = _f([(-6.20, 53.30), (-6.15, 53.30)], ["A", "B", "C"])
    out = consolidate_features([a, b, c])
    assert len(out) == 1
    coords = out[0]["geometry"]["coordinates"]
    assert len(coords) >= 4  # all four endpoints preserved
