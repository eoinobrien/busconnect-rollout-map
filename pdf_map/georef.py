"""Fit an affine transform from PDF page coordinates to WGS84 lon/lat.

The Big Picture map is a schematic — corridors are drawn as smooth
curves through approximately-correct places — so a 6-parameter
affine (PDF x,y → lon,lat) is the right level of fit. We use a
hand-curated table of large park / cemetery labels whose textual
identifiers appear once on the PDF (or whose multiple occurrences
cluster within a few PDF points), look up each label's bbox centre,
and least-squares fit:

    lon = a*x + b*y + c
    lat = d*x + e*y + f

PDF y increases downward; the fit absorbs the sign automatically.

The lat/lon constants below are approximate park centroids from OSM
— they're targeting the printed label position on the BusConnects
map, which is itself only schematic. Tens of metres of slop is
expected and irrelevant for the schematic-overlay use case.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass

import numpy as np

from .extract import TextSpan


# Hand-curated control points: labels we expect on the PDF, mapped
# to their approximate real-world (lon, lat). Each key matches an
# uppercased TextSpan.text exactly. Multi-occurrence labels are kept
# only when all occurrences cluster within a few PDF points; KILLINEY
# / CABINTEELY / SHANANAGH have inset duplicates and are excluded.
LANDMARKS_LONLAT: dict[str, tuple[float, float]] = {
    "PHOENIX": (-6.330, 53.355),            # Phoenix Park
    "TYMON PARK": (-6.334, 53.296),
    "MARLAY": (-6.255, 53.275),             # Marlay Park
    "BUSHY": (-6.290, 53.306),              # Bushy Park
    "ST. ANNE": (-6.180, 53.371),           # St Anne's Park
    "BULL": (-6.142, 53.373),               # Bull Island
    "TOLKA VALLEY PARK": (-6.310, 53.380),
    "CORKAGH": (-6.404, 53.317),            # Corkagh Park
    "FATHER": (-6.167, 53.401),             # Father Collins Park
    "WATERSTOWN": (-6.371, 53.358),         # Waterstown Park, Palmerstown
    "DARNDALE": (-6.193, 53.398),           # Darndale Park
    "WAR": (-6.341, 53.341),                # War Memorial Park, Islandbridge
    "GLASNEVIN": (-6.278, 53.374),          # Glasnevin Cemetery
    "SANTRY": (-6.241, 53.402),             # Santry Park
}


# 2x3 affine: [lon; lat] = [[a, b, c]; [d, e, f]] @ [x; y; 1]
@dataclass(frozen=True)
class Affine:
    a: float
    b: float
    c: float
    d: float
    e: float
    f: float

    def apply(self, x: float, y: float) -> tuple[float, float]:
        return (self.a * x + self.b * y + self.c, self.d * x + self.e * y + self.f)


def _label_centroids(spans: list[TextSpan]) -> dict[str, tuple[float, float]]:
    """For each landmark key, average the bbox centres of every span
    whose uppercased text matches. Skips any landmark with zero
    matches — caller decides what to do about a missing control.
    """
    grouped: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for s in spans:
        key = s.text.upper()
        if key in LANDMARKS_LONLAT:
            grouped[key].append(s.center)

    centroids: dict[str, tuple[float, float]] = {}
    for key, pts in grouped.items():
        cx = sum(p[0] for p in pts) / len(pts)
        cy = sum(p[1] for p in pts) / len(pts)
        centroids[key] = (cx, cy)
    return centroids


def fit_affine(
    pdf_pts: list[tuple[float, float]],
    world_pts: list[tuple[float, float]],
) -> Affine:
    """Least-squares fit of the 6-param affine taking PDF (x, y) to
    world (lon, lat). Requires >= 3 non-colinear correspondences.
    """
    if len(pdf_pts) != len(world_pts):
        raise ValueError("pdf_pts and world_pts must be the same length")
    if len(pdf_pts) < 3:
        raise ValueError("need at least 3 control points to fit an affine")

    A = np.array([[x, y, 1.0] for x, y in pdf_pts], dtype=float)
    lon = np.array([p[0] for p in world_pts], dtype=float)
    lat = np.array([p[1] for p in world_pts], dtype=float)

    abc, *_ = np.linalg.lstsq(A, lon, rcond=None)
    de_f, *_ = np.linalg.lstsq(A, lat, rcond=None)
    return Affine(
        a=float(abc[0]), b=float(abc[1]), c=float(abc[2]),
        d=float(de_f[0]), e=float(de_f[1]), f=float(de_f[2]),
    )


def fit_from_spans(spans: list[TextSpan]) -> tuple[Affine, dict[str, tuple[float, float]]]:
    """Convenience: locate landmarks in `spans`, fit affine, return
    the transform plus per-landmark residual distances in metres.
    """
    centroids = _label_centroids(spans)
    if len(centroids) < 3:
        raise ValueError(
            f"only {len(centroids)} landmarks found; need at least 3"
        )
    keys = sorted(centroids)
    pdf_pts = [centroids[k] for k in keys]
    world_pts = [LANDMARKS_LONLAT[k] for k in keys]
    transform = fit_affine(pdf_pts, world_pts)

    residuals: dict[str, float] = {}
    for k, (px, py) in zip(keys, pdf_pts):
        pred_lon, pred_lat = transform.apply(px, py)
        true_lon, true_lat = LANDMARKS_LONLAT[k]
        residuals[k] = _haversine_m(pred_lon, pred_lat, true_lon, true_lat)
    return transform, residuals


def _haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """Great-circle distance in metres between two lon/lat pairs."""
    R = 6371008.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))
