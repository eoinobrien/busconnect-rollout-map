"""Load the user-edited future-routes GeoJSON and shape it for the
existing routes.geojson layer.

The manual file (output/manual-future-routes.geojson) is what the
user produced after road-snapping the schematic in QGIS. Each
feature is a MultiLineString with a `route no` string that is
either one route_id ("A1", "L11", "P98") or a space-joined list
("23 24 81 85 86 87 88") for shared corridors. We map each entry
into the same routes/route/category/colour shape that the live
pipeline emits and tag every feature with phase="future" so the
viewer's existing BusConnects-phase toggle picks it up.
"""

from __future__ import annotations

import json
from pathlib import Path

from gtfs_map.category import categorise, category_colour


FUTURE_PHASE = "future"


def _split_route_no(text: str | None) -> list[str]:
    if not text:
        return []
    return [tok for tok in text.split() if tok.strip()]


def _category_for_routes(route_ids: list[str]) -> str:
    """Pick a representative category for a shared-corridor feature.

    Front-end render order is radial, peak, local, orbital, spine -
    spine on top. So when a corridor mixes a spine and a radial we
    prefer the spine label so the bundle paints in spine red and
    sits at the top of the stack.
    """
    if not route_ids:
        return "radial"
    cats = [categorise(rid) for rid in route_ids]
    priority = {"spine": 0, "orbital": 1, "local": 2, "peak": 3, "radial": 4}
    return min(cats, key=lambda c: priority.get(c, 99))


def load_manual_future_features(
    path: Path | str,
    short_to_phase: dict[str, str] | None = None,
) -> list[dict]:
    """Read the manual GeoJSON and return routes.geojson-shaped features.

    - MultiLineString geometry preserved as-is so each road-snapped
      corridor stays one feature (Leaflet renders MultiLineString
      natively).
    - Empty-geometry placeholder features (geometry coordinates == [])
      are dropped.
    - Features whose `route no` is missing or unparseable are also
      dropped.
    - When `short_to_phase` is provided, each feature's phase is
      looked up from the user's rollout-phases.json (so a future route
      lands in "8 (Potential)", "11 (Potential)", etc.). Routes
      without an explicit phase fall back to the generic FUTURE_PHASE.
    """
    path = Path(path)
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    short_to_phase = short_to_phase or {}

    features: list[dict] = []
    for f in raw.get("features", []):
        geom = f.get("geometry") or {}
        coords = geom.get("coordinates") or []
        if not coords:
            continue
        gtype = geom.get("type")
        if gtype not in ("LineString", "MultiLineString"):
            continue

        route_ids = _split_route_no(f.get("properties", {}).get("route no"))
        if not route_ids:
            continue
        primary = route_ids[0]
        category = _category_for_routes(route_ids)
        colour = category_colour(category)
        phase = short_to_phase.get(primary, FUTURE_PHASE)

        features.append({
            "type": "Feature",
            "geometry": geom,
            "properties": {
                "route": primary,
                "routes": route_ids,
                "route_long_name": "",
                "agency": "BusConnects (planned)",
                "category": category,
                "colour": colour,
                "phase": phase,
            },
        })
    return features


