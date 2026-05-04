import re

_SPINE_RE = re.compile(r"^([A-H])(\d+)$")


def classify_route(route_short_name: str) -> tuple[str, str | None]:
    """Classify a GTFS route_short_name as a BusConnects spine or other.

    Returns ("spine", letter) for A1, A2, B1, ... H3 — i.e. a single
    letter A-H followed by digits and nothing else. Returns ("other",
    None) for everything else, including L/N/S/W/X-prefixed locals,
    plain numeric routes, and sub-variants like A1B.
    """
    m = _SPINE_RE.match(route_short_name)
    if m:
        return ("spine", m.group(1))
    return ("other", None)
