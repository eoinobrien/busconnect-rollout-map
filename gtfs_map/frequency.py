from __future__ import annotations

from pathlib import Path

import pandas as pd


def high_frequency_route_ids(
    stop_times: pd.DataFrame,
    trips: pd.DataFrame,
    threshold: int = 5,
    hour: int = 8,
    active_trip_ids: set[str] | None = None,
) -> set[str]:
    """Return route_ids whose first-stop departure falls within the given
    hour window at least `threshold` times across `active_trip_ids`.

    Counts the start of each trip (stop_sequence == 1), filtered to the
    hour window [HH:00:00, HH+1:00:00). Joins those starts to trips →
    routes, then groups by route_id.

    `active_trip_ids` lets the caller restrict the count to today's
    trips; if None, every trip in the input is considered active.
    """
    starts = stop_times[stop_times["stop_sequence"] == 1].copy()
    if active_trip_ids is not None:
        starts = starts[starts["trip_id"].isin(active_trip_ids)]
    if starts.empty:
        return set()

    lo = f"{hour:02d}:00:00"
    hi = f"{hour + 1:02d}:00:00"
    in_hour = (starts["departure_time"] >= lo) & (starts["departure_time"] < hi)
    starts = starts[in_hour]
    if starts.empty:
        return set()

    joined = starts.merge(trips[["trip_id", "route_id"]], on="trip_id")
    counts = joined.groupby("route_id").size()
    return set(counts.index[counts >= threshold])


def high_frequency_route_ids_from_files(
    gtfs_dir: Path,
    active_trip_ids: set[str],
    threshold: int = 5,
    hour: int = 8,
    chunksize: int = 500_000,
) -> set[str]:
    """Same as `high_frequency_route_ids` but streams stop_times.txt in
    chunks so the 300 MB file doesn't blow up memory."""
    gtfs_dir = Path(gtfs_dir)
    trips = pd.read_csv(
        gtfs_dir / "trips.txt",
        usecols=["trip_id", "route_id"],
        dtype={"trip_id": str, "route_id": str},
    )
    trips = trips[trips["trip_id"].isin(active_trip_ids)]

    lo = f"{hour:02d}:00:00"
    hi = f"{hour + 1:02d}:00:00"

    counts: dict[str, int] = {}
    reader = pd.read_csv(
        gtfs_dir / "stop_times.txt",
        usecols=["trip_id", "departure_time", "stop_sequence"],
        dtype={"trip_id": str, "departure_time": str, "stop_sequence": int},
        chunksize=chunksize,
    )
    trip_to_route = dict(zip(trips["trip_id"], trips["route_id"]))
    for chunk in reader:
        starts = chunk[chunk["stop_sequence"] == 1]
        starts = starts[starts["trip_id"].isin(trip_to_route)]
        starts = starts[(starts["departure_time"] >= lo) & (starts["departure_time"] < hi)]
        for tid in starts["trip_id"]:
            rid = trip_to_route[tid]
            counts[rid] = counts.get(rid, 0) + 1

    return {rid for rid, n in counts.items() if n >= threshold}
