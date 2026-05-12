"""Helpers for building synthetic GTFS feeds in-memory.

Several test files (test_pipeline.py, test_rail.py, ...) need to
write a tiny calendar/routes/trips/shapes/stop_times set under a
tmp_path. Centralised here so the CSV header strings stay in one
place; if GTFS adds a new required column, fixtures are updated
once.
"""

from __future__ import annotations

from pathlib import Path


AGENCY = (
    "agency_id,agency_name,agency_url,agency_timezone\n"
    "7778019,Dublin Bus,https://dublinbus.ie/,Europe/London\n"
    "7778021,Go-Ahead,https://goaheadireland.ie/,Europe/London\n"
    "7778006,Go-Ahead Commuter,https://goaheadireland.ie/,Europe/London\n"
    "7778014,LUAS,https://luas.ie/,Europe/London\n"
    "7778017,Iarnród Éireann,https://irishrail.ie/,Europe/London\n"
)

CALENDAR_WEEKDAY = (
    "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\n"
    "WK,1,1,1,1,1,0,0,20260101,20271231\n"
    "SUN,0,0,0,0,0,0,1,20260101,20271231\n"
)

CAL_DATES_EMPTY = "service_id,date,exception_type\n"


def write_gtfs(d: Path, **files) -> Path:
    """files keyed by filename (no .txt) -> string contents."""
    d.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (d / f"{name}.txt").write_text(content)
    return d


def routes_csv(rows) -> str:
    """rows: list of dicts with route_id, agency_id, route_short_name."""
    header = (
        "route_id,agency_id,route_short_name,route_long_name,route_desc,"
        "route_type,route_url,route_color,route_text_color\n"
    )
    body = ""
    for r in rows:
        body += (
            f"{r['route_id']},{r['agency_id']},{r['route_short_name']},"
            f"{r.get('route_long_name', '')},,{r.get('route_type', 3)},,,\n"
        )
    return header + body


def trips_csv(rows) -> str:
    """rows: list of dicts with route_id, service_id, trip_id, direction_id, shape_id."""
    header = (
        "route_id,service_id,trip_id,trip_headsign,trip_short_name,"
        "direction_id,block_id,shape_id\n"
    )
    body = ""
    for t in rows:
        body += (
            f"{t['route_id']},{t['service_id']},{t['trip_id']},,," +
            f"{t.get('direction_id', 0)},,{t['shape_id']}\n"
        )
    return header + body


def shapes_csv(shapes: dict[str, list[tuple[float, float]]]) -> str:
    """shapes: shape_id -> list of (lon, lat) in order."""
    header = "shape_id,shape_pt_lat,shape_pt_lon,shape_pt_sequence,shape_dist_traveled\n"
    body = ""
    for sid, coords in shapes.items():
        for i, (lon, lat) in enumerate(coords, start=1):
            body += f"{sid},{lat},{lon},{i},{i*100}\n"
    return header + body


def stop_times_csv(rows) -> str:
    """rows: list of dicts with trip_id, stop_id, stop_sequence, departure_time."""
    header = (
        "trip_id,arrival_time,departure_time,stop_id,stop_sequence,stop_headsign,"
        "pickup_type,drop_off_type,timepoint\n"
    )
    body = ""
    for r in rows:
        t = r.get("departure_time", "07:00:00")
        body += (
            f"{r['trip_id']},{t},{t},{r['stop_id']},{r['stop_sequence']},,0,0,1\n"
        )
    return header + body


def stops_csv(stops) -> str:
    """stops: list of (stop_id, lon, lat)."""
    header = "stop_id,stop_name,stop_lat,stop_lon\n"
    body = ""
    for sid, lon, lat in stops:
        body += f"{sid},{sid},{lat},{lon}\n"
    return header + body
