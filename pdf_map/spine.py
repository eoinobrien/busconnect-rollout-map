"""Classify which Path objects are bus-route lines vs. map furniture.

The Big Picture map mixes route lines with park outlines, road
carriageways, water boundaries, shield outlines, and administrative
borders. Empirically the route lines on the 2025-10 revision share
a tight signature: stroke width ~0.4pt, bounding-box diagonal
> 50pt, and a stroke colour in the BusConnects spine palette
(red, purple, green, orange, blue and a few related hues).

We don't need to map a colour back to a particular spine letter —
downstream code uses colour as a *grouping key* per route shield,
and the user supplies the shield's route_id directly. That keeps
this module thin: identify candidate paths by signature, expose
the colour as a tuple so callers can bucket on it.
"""

from __future__ import annotations

from collections import Counter
from typing import Iterable

from .extract import RGB, Path


# Route lines on the 2025-10 PDF revision. Tolerated as exact-equal
# tuples after the `_round_rgb` 4-decimal rounding done by `extract`.
# Hand-validated by visual inspection: each colour corresponds to
# at least one BusConnects route family (spine A/B/C/.../H or one
# of the orbital/local palettes). New revisions of the PDF may need
# additions; verify by running `dominant_route_colors()` over the
# extracted paths and adding any new dominant colour tuples.
ROUTE_LINE_PALETTE: set[RGB] = {
    (0.9304, 0.1109, 0.1415),   # red
    (0.398, 0.143, 0.518),      # purple
    (0.402, 0.684, 0.241),      # green
    (0.981, 0.65, 0.1),         # orange
    (0.0, 0.607, 0.873),        # blue
    (0.0, 0.585, 0.369),        # teal
    (0.477, 0.305, 0.625),      # mid purple
    (0.158, 0.624, 0.257),      # darker green
    (0.0, 0.653, 0.914),        # cyan
    (0.552, 0.778, 0.247),      # lime
    (0.506, 0.765, 0.259),      # mid lime
    (0.397, 0.247, 0.525),      # mid purple variant
    (0.936, 0.23, 0.171),       # red-orange
    (0.936, 0.228, 0.224),      # warm red
}


# Filters chosen from the empirical distribution. Width varies by
# spine on this PDF — red routes stroke at ~0.40pt, green at ~0.27pt,
# orange at ~0.20pt — so the width window stays wide. The lower
# bound rejects shield-glyph outlines (typ. 0.10pt) and park-outline
# hairlines; the diagonal cutoff rejects shield outlines and other
# tiny artefacts that share a route-palette colour.
_MIN_DIAG_PT = 50.0
_MIN_WIDTH_PT = 0.13
_MAX_WIDTH_PT = 0.60


def _close_to_palette(rgb: RGB, tol: float = 0.01) -> RGB | None:
    """Return the canonical palette colour matching `rgb` within `tol`,
    or None. Helps tolerate float-rounding drift if the PDF revision
    re-encodes the same nominal hue at slightly different precision.
    """
    if rgb in ROUTE_LINE_PALETTE:
        return rgb
    for ref in ROUTE_LINE_PALETTE:
        if all(abs(rgb[i] - ref[i]) < tol for i in range(3)):
            return ref
    return None


def is_route_line(path: Path) -> bool:
    """True if `path` looks like a BusConnects route line."""
    if path.stroke is None:
        return False
    if path.width is None:
        return False
    if not (_MIN_WIDTH_PT <= path.width <= _MAX_WIDTH_PT):
        return False
    if path.diag < _MIN_DIAG_PT:
        return False
    return _close_to_palette(path.stroke) is not None


def route_line_paths(paths: Iterable[Path]) -> list[Path]:
    """Filter `paths` down to route-line candidates."""
    return [p for p in paths if is_route_line(p)]


def dominant_route_colors(paths: Iterable[Path], top: int = 12) -> list[tuple[RGB, int]]:
    """Diagnostic: count stroke-colour frequency among large stroked
    paths. Use this to spot palette additions when a new PDF revision
    introduces a hue we don't yet recognise.
    """
    counter: Counter[RGB] = Counter()
    for p in paths:
        if p.stroke is None:
            continue
        if p.width is None or not (_MIN_WIDTH_PT <= p.width <= _MAX_WIDTH_PT):
            continue
        if p.diag < _MIN_DIAG_PT:
            continue
        counter[p.stroke] += 1
    return counter.most_common(top)
