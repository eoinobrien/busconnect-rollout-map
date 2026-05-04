from __future__ import annotations

import datetime as _dt
from typing import IO

import pandas as pd


_WEEKDAY_COLS = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)


def active_services_for_date(
    calendar_csv: IO[str] | str,
    calendar_dates_csv: IO[str] | str,
    date_iso: str,
) -> set[str]:
    """Return the set of GTFS service_ids active on the given date.

    Implements GTFS calendar.txt + calendar_dates.txt resolution:
    - calendar.txt: service is active if its weekday flag is 1 and the
      date falls within [start_date, end_date].
    - calendar_dates.txt overrides per-date: exception_type=1 adds the
      service for that date, exception_type=2 removes it.

    Both CSV inputs may be paths or file-like objects (so the function
    is unit-testable with StringIO).
    """
    target = _dt.date.fromisoformat(date_iso)
    weekday_col = _WEEKDAY_COLS[target.weekday()]
    target_yyyymmdd = int(target.strftime("%Y%m%d"))

    cal = pd.read_csv(
        calendar_csv,
        dtype={"service_id": str, "start_date": int, "end_date": int},
    )
    in_window = (cal["start_date"] <= target_yyyymmdd) & (
        cal["end_date"] >= target_yyyymmdd
    )
    runs_today = cal[weekday_col] == 1
    active = set(cal.loc[in_window & runs_today, "service_id"])

    cal_dates = pd.read_csv(
        calendar_dates_csv,
        dtype={"service_id": str, "date": int, "exception_type": int},
    )
    if not cal_dates.empty:
        on_date = cal_dates[cal_dates["date"] == target_yyyymmdd]
        for sid in on_date.loc[on_date["exception_type"] == 1, "service_id"]:
            active.add(sid)
        for sid in on_date.loc[on_date["exception_type"] == 2, "service_id"]:
            active.discard(sid)

    return active
