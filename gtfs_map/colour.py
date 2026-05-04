from __future__ import annotations

import hashlib


# Eight visually distinct colours for the BusConnects spines. Hand-picked
# from a colourblind-friendlier palette so adjacent spines stay
# distinguishable on the map.
SPINE_COLOURS: dict[str, str] = {
    "A": "#e6194b",  # red
    "B": "#3cb44b",  # green
    "C": "#4363d8",  # blue
    "D": "#f58231",  # orange
    "E": "#911eb4",  # purple
    "F": "#42d4f4",  # cyan
    "G": "#bfef45",  # lime
    "H": "#f032e6",  # magenta
}


# 20-colour categorical palette for non-spine routes. Tableau 20 (less
# the lightest tints which disappear on the basemap).
_OTHER_PALETTE: list[str] = [
    "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
    "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf",
    "#aec7e8", "#ffbb78", "#98df8a", "#ff9896", "#c5b0d5",
    "#c49c94", "#f7b6d2", "#c7c7c7", "#dbdb8d", "#9edae5",
]


def spine_colour(letter: str) -> str:
    return SPINE_COLOURS[letter]


def route_colour(route_short_name: str) -> str:
    """Deterministic palette-index by hash of route name."""
    h = hashlib.md5(route_short_name.encode("utf-8")).digest()
    idx = h[0] % len(_OTHER_PALETTE)
    return _OTHER_PALETTE[idx]
