from __future__ import annotations

import re


_SPINE_RE = re.compile(r"^[A-H]\d+$")


# Category palette per the brief:
#   red    — spines / high-frequency
#   blue   — orbitals (W, N, S prefixes)
#   green  — locals (L prefix)
#   orange — peak-only / express (P prefix and Dublin's X-routes)
#   purple — lower-frequency radial routes (everything else, mostly numeric)
CATEGORY_COLOURS: dict[str, str] = {
    "spine": "#d62728",     # red
    "orbital": "#1f77b4",   # blue
    "local": "#2ca02c",     # green
    "peak": "#ff7f0e",      # orange
    "radial": "#9467bd",    # purple
}


def categorise(route_short_name: str, *, high_frequency: bool = False) -> str:
    """Return the high-level category of a route.

    Category by nature (route-name prefix) — these never change with
    frequency, because they encode the route's *type* (orbital,
    local, peak/express) rather than how often it runs:
      spine    A-H followed by digits only (BusConnects spine letters)
      orbital  W*, N*, S*  (cross-town, always blue)
      local    L*          (neighbourhood feeders, always green)
      peak     P*, X*      (peak-only / express, always orange)

    Plain-numeric radials are the only category that can be promoted
    to spine (red) via the high_frequency flag — that's the rule that
    makes a 13 or a 16 paint red because it runs >=5 times an hour.
    """
    if _SPINE_RE.match(route_short_name):
        return "spine"
    first = route_short_name[:1]
    if first == "L":
        return "local"
    if first in ("W", "N", "S"):
        return "orbital"
    if first in ("P", "X"):
        return "peak"
    if high_frequency:
        return "spine"
    return "radial"


def category_colour(category: str) -> str:
    return CATEGORY_COLOURS[category]
