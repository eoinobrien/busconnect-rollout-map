import io

import pandas as pd

from gtfs_map.frequency import high_frequency_route_ids


# Synthetic stop_times: trip_id, stop_sequence, departure_time
def _stop_times(rows):
    df = pd.DataFrame(
        rows,
        columns=["trip_id", "arrival_time", "departure_time", "stop_sequence"],
    )
    return df


def _trips(rows):
    # trip_id -> route_id
    return pd.DataFrame(rows, columns=["trip_id", "route_id"])


def test_route_with_five_or_more_8am_trips_is_high_frequency():
    stop_times = _stop_times([
        # Route HF has 6 trips in the 08:00–08:59 window
        ("t1", "08:00:00", "08:00:00", 1),
        ("t2", "08:10:00", "08:10:00", 1),
        ("t3", "08:20:00", "08:20:00", 1),
        ("t4", "08:30:00", "08:30:00", 1),
        ("t5", "08:45:00", "08:45:00", 1),
        ("t6", "08:55:00", "08:55:00", 1),
    ])
    trips = _trips([
        ("t1", "HF"), ("t2", "HF"), ("t3", "HF"),
        ("t4", "HF"), ("t5", "HF"), ("t6", "HF"),
    ])
    hf = high_frequency_route_ids(stop_times, trips, threshold=5, hour=8)
    assert "HF" in hf


def test_route_with_four_8am_trips_is_not_high_frequency():
    stop_times = _stop_times([
        ("t1", "08:05:00", "08:05:00", 1),
        ("t2", "08:20:00", "08:20:00", 1),
        ("t3", "08:35:00", "08:35:00", 1),
        ("t4", "08:50:00", "08:50:00", 1),
    ])
    trips = _trips([("t1", "LF"), ("t2", "LF"), ("t3", "LF"), ("t4", "LF")])
    hf = high_frequency_route_ids(stop_times, trips, threshold=5, hour=8)
    assert "LF" not in hf


def test_only_first_stop_departures_count():
    # 6 stop_times all in 08:xx — but only stop_sequence==1 is a
    # trip-start. Counting all rows would give a false positive.
    stop_times = _stop_times([
        ("t1", "08:00:00", "08:00:00", 1),  # trip starts at 8:00
        ("t1", "08:10:00", "08:10:00", 2),
        ("t1", "08:20:00", "08:20:00", 3),
        ("t1", "08:30:00", "08:30:00", 4),
        ("t1", "08:40:00", "08:40:00", 5),
        ("t1", "08:50:00", "08:50:00", 6),
    ])
    trips = _trips([("t1", "ONLY_ONE")])
    hf = high_frequency_route_ids(stop_times, trips, threshold=5, hour=8)
    assert "ONLY_ONE" not in hf


def test_only_8am_window_counts_not_other_hours():
    stop_times = _stop_times([
        # 5 trips at 7am, 0 at 8am
        ("t1", "07:00:00", "07:00:00", 1),
        ("t2", "07:15:00", "07:15:00", 1),
        ("t3", "07:30:00", "07:30:00", 1),
        ("t4", "07:45:00", "07:45:00", 1),
        ("t5", "07:50:00", "07:50:00", 1),
    ])
    trips = _trips([
        ("t1", "EARLY"), ("t2", "EARLY"), ("t3", "EARLY"),
        ("t4", "EARLY"), ("t5", "EARLY"),
    ])
    hf = high_frequency_route_ids(stop_times, trips, threshold=5, hour=8)
    assert "EARLY" not in hf


def test_one_sided_service_qualifies_via_busy_direction():
    """A peak-direction service with 5 trips outbound at noon and
    0 inbound is HF — the busy direction clears the bar even when
    the other side is empty. A real frequent corridor on at least
    one of its two directions still counts."""
    stop_times = _stop_times([
        ("t1", "12:00:00", "12:00:00", 1),
        ("t2", "12:10:00", "12:10:00", 1),
        ("t3", "12:20:00", "12:20:00", 1),
        ("t4", "12:35:00", "12:35:00", 1),
        ("t5", "12:50:00", "12:50:00", 1),
    ])
    trips = pd.DataFrame(
        [(f"t{i}", "PEAK", 0) for i in range(1, 6)],
        columns=["trip_id", "route_id", "direction_id"],
    )
    hf = high_frequency_route_ids(stop_times, trips, threshold=4, hour=12)
    assert "PEAK" in hf


def test_below_threshold_in_every_direction_is_not_high_frequency():
    """3 outbound + 3 inbound — neither direction reaches the bar,
    so not HF, even though the combined total (6) would have
    qualified under the old combined-count rule."""
    stop_times = _stop_times([
        ("t1", "12:00:00", "12:00:00", 1),
        ("t2", "12:20:00", "12:20:00", 1),
        ("t3", "12:40:00", "12:40:00", 1),
        ("t4", "12:05:00", "12:05:00", 1),
        ("t5", "12:25:00", "12:25:00", 1),
        ("t6", "12:45:00", "12:45:00", 1),
    ])
    trips = pd.DataFrame(
        [(f"t{i}", "LOWBOTH", 0) for i in range(1, 4)]
        + [(f"t{i}", "LOWBOTH", 1) for i in range(4, 7)],
        columns=["trip_id", "route_id", "direction_id"],
    )
    hf = high_frequency_route_ids(stop_times, trips, threshold=4, hour=12)
    assert "LOWBOTH" not in hf


def test_uses_only_active_trips_when_provided():
    stop_times = _stop_times([
        ("t1", "08:00:00", "08:00:00", 1),
        ("t2", "08:10:00", "08:10:00", 1),
        ("t3", "08:20:00", "08:20:00", 1),
        ("t4", "08:30:00", "08:30:00", 1),
        ("t5", "08:45:00", "08:45:00", 1),
    ])
    # All 5 trips belong to route X but only t1 is in active_trips set
    trips = _trips([
        ("t1", "X"), ("t2", "X"), ("t3", "X"), ("t4", "X"), ("t5", "X"),
    ])
    hf = high_frequency_route_ids(
        stop_times, trips, threshold=5, hour=8,
        active_trip_ids={"t1"},
    )
    assert "X" not in hf
