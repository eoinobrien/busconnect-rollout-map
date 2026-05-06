from __future__ import annotations

from pathlib import Path

import pandas as pd


def high_frequency_route_ids(
    stop_times: pd.DataFrame,
    trips: pd.DataFrame,
    threshold: float = 4,
    hour: int = 8,
    active_trip_ids: set[str] | None = None,
) -> set[str]:
    """Return route_ids whose busiest direction in the given hour
    window has at least `threshold` trip starts.

    Counts trip starts (stop_sequence == 1, departure in
    [HH:00:00, HH+1:00:00)) bucketed by (route_id, direction_id),
    then takes the max across directions per route. A route is
    high-frequency when any one direction clears the bar — so an
    asymmetric peak-direction service still qualifies on the busy
    side, while a low-frequency route doesn't.

    If the trips frame has no `direction_id` column, all trips are
    treated as a single direction.

    `active_trip_ids` restricts the count to today's trips; if None,
    every trip in the input is considered active.
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

    cols = ["trip_id", "route_id"]
    if "direction_id" in trips.columns:
        cols.append("direction_id")
    joined = starts.merge(trips[cols], on="trip_id")
    if "direction_id" not in joined.columns:
        joined["direction_id"] = 0

    per_dir = joined.groupby(["route_id", "direction_id"]).size()
    max_per_route = per_dir.groupby(level="route_id").max()
    return set(max_per_route.index[max_per_route >= threshold])


def high_frequency_route_ids_from_files(
    gtfs_dir: Path,
    active_trip_ids: set[str],
    threshold: float = 4,
    hour: int = 8,
    chunksize: int = 500_000,
) -> set[str]:
    """Same as `high_frequency_route_ids` but streams stop_times.txt in
    chunks so the 300 MB file doesn't blow up memory.

    Counts per (route_id, direction_id), then keeps routes whose
    busiest direction meets `threshold`.
    """
    gtfs_dir = Path(gtfs_dir)
    trips = pd.read_csv(
        gtfs_dir / "trips.txt",
        usecols=["trip_id", "route_id", "direction_id"],
        dtype={"trip_id": str, "route_id": str},
    )
    trips["direction_id"] = trips["direction_id"].fillna(0).astype(int)
    trips = trips[trips["trip_id"].isin(active_trip_ids)]

    lo = f"{hour:02d}:00:00"
    hi = f"{hour + 1:02d}:00:00"

    counts: dict[tuple[str, int], int] = {}
    reader = pd.read_csv(
        gtfs_dir / "stop_times.txt",
        usecols=["trip_id", "departure_time", "stop_sequence"],
        dtype={"trip_id": str, "departure_time": str, "stop_sequence": int},
        chunksize=chunksize,
    )
    trip_to_rd = {
        tid: (rid, did)
        for tid, rid, did in zip(
            trips["trip_id"], trips["route_id"], trips["direction_id"]
        )
    }
    for chunk in reader:
        starts = chunk[chunk["stop_sequence"] == 1]
        starts = starts[starts["trip_id"].isin(trip_to_rd)]
        starts = starts[(starts["departure_time"] >= lo) & (starts["departure_time"] < hi)]
        for tid in starts["trip_id"]:
            key = trip_to_rd[tid]
            counts[key] = counts.get(key, 0) + 1

    max_per_route: dict[str, int] = {}
    for (rid, _did), n in counts.items():
        if n > max_per_route.get(rid, 0):
            max_per_route[rid] = n
    return {rid for rid, n in max_per_route.items() if n >= threshold}
