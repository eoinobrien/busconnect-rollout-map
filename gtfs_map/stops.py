from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import pandas as pd


def sample_stop_indices(n_stops: int, target_k: int) -> list[int]:
    """Pick `target_k` indices spread evenly across [0, n_stops-1],
    always including the first (0) and last (n_stops-1) so the route's
    actual termini are guaranteed to land on a label.
    """
    if n_stops <= 0:
        return []
    if n_stops == 1:
        return [0]
    k = min(n_stops, max(2, target_k))
    return [round(i * (n_stops - 1) / (k - 1)) for i in range(k)]


def stops_for_active_routes(
    gtfs_dir: Path,
    trips: pd.DataFrame,
    chunksize: int = 500_000,
) -> dict[str, list[tuple[float, float]]]:
    """Return route_id -> list of (lon, lat) for the route's
    representative trip (preferring direction_id == 0).

    Uses streaming over stop_times.txt so the 300 MB real file doesn't
    blow up memory. Trips already-filtered to today's active services
    keeps the trip set manageable.
    """
    gtfs_dir = Path(gtfs_dir)
    if trips.empty:
        return {}

    rep_trips = (
        trips.sort_values("direction_id")
        .drop_duplicates("route_id", keep="first")
        .set_index("route_id")["trip_id"]
    )
    needed = set(rep_trips.values)
    if not needed:
        return {}

    stops_df = pd.read_csv(
        gtfs_dir / "stops.txt",
        usecols=["stop_id", "stop_lat", "stop_lon"],
        dtype={"stop_id": str},
    )
    stop_pos: dict[str, tuple[float, float]] = {
        row.stop_id: (float(row.stop_lon), float(row.stop_lat))
        for row in stops_df.itertuples(index=False)
    }

    # Stream stop_times.txt
    trip_chunks: dict[str, list[pd.DataFrame]] = defaultdict(list)
    reader = pd.read_csv(
        gtfs_dir / "stop_times.txt",
        usecols=["trip_id", "stop_id", "stop_sequence"],
        dtype={"trip_id": str, "stop_id": str},
        chunksize=chunksize,
    )
    for chunk in reader:
        chunk = chunk[chunk["trip_id"].isin(needed)]
        if chunk.empty:
            continue
        for tid, group in chunk.groupby("trip_id"):
            trip_chunks[tid].append(group)

    trip_to_stops: dict[str, list[tuple[float, float]]] = {}
    for tid, parts in trip_chunks.items():
        df = pd.concat(parts).sort_values("stop_sequence")
        coords = [stop_pos[sid] for sid in df["stop_id"] if sid in stop_pos]
        trip_to_stops[tid] = coords

    return {rid: trip_to_stops.get(tid, []) for rid, tid in rep_trips.items()}
