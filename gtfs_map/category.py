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


def categorise(route_short_name: str) -> str:
    """Return the high-level category of a route by short-name prefix.

    spine    — A-H followed by digits only (BusConnects spine sub-routes)
    orbital  — W*, N*, S* (orbitals/cross-town)
    local    — L*
    peak     — P*, X* (peak-only / express)
    radial   — everything else (typically plain numeric radial routes)
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
    return "radial"


def category_colour(category: str) -> str:
    return CATEGORY_COLOURS[category]
