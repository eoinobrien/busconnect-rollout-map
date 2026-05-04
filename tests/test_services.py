import io

from gtfs_map.services import active_services_for_date


CALENDAR_CSV = """service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date
WEEKDAY,1,1,1,1,1,0,0,20260101,20271231
SAT,0,0,0,0,0,1,0,20260101,20271231
SUN,0,0,0,0,0,0,1,20260101,20271231
EXPIRED,1,1,1,1,1,0,0,20240101,20241231
FUTURE,1,1,1,1,1,0,0,20270101,20271231
"""


CALENDAR_DATES_CSV = """service_id,date,exception_type
WEEKDAY,20260505,2
HOLIDAY_ADD,20260505,1
WEEKDAY,20260506,2
"""


def _read(s: str):
    return io.StringIO(s)


def test_returns_services_active_on_a_normal_tuesday():
    services = active_services_for_date(
        _read(CALENDAR_CSV),
        _read("service_id,date,exception_type\n"),
        "2026-05-05",
    )
    assert services == {"WEEKDAY"}


def test_drops_services_outside_date_window():
    services = active_services_for_date(
        _read(CALENDAR_CSV),
        _read("service_id,date,exception_type\n"),
        "2026-05-05",
    )
    assert "EXPIRED" not in services
    assert "FUTURE" not in services


def test_picks_correct_weekday_column():
    saturday = active_services_for_date(
        _read(CALENDAR_CSV),
        _read("service_id,date,exception_type\n"),
        "2026-05-09",  # Saturday
    )
    assert saturday == {"SAT"}

    sunday = active_services_for_date(
        _read(CALENDAR_CSV),
        _read("service_id,date,exception_type\n"),
        "2026-05-10",  # Sunday
    )
    assert sunday == {"SUN"}


def test_calendar_dates_exception_type_2_removes_service():
    # On 2026-05-05, WEEKDAY is normally active but excepted out.
    services = active_services_for_date(
        _read(CALENDAR_CSV),
        _read(CALENDAR_DATES_CSV),
        "2026-05-05",
    )
    assert "WEEKDAY" not in services


def test_calendar_dates_exception_type_1_adds_service():
    # HOLIDAY_ADD doesn't appear in calendar.txt at all but is added by
    # an exception_type=1 row for that date.
    services = active_services_for_date(
        _read(CALENDAR_CSV),
        _read(CALENDAR_DATES_CSV),
        "2026-05-05",
    )
    assert "HOLIDAY_ADD" in services
