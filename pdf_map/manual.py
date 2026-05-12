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

We also synthesise label seeds for the future routes by sampling
points along each route's MultiLineString. Labels carry phase=
"future" so the front-end can render them with the outline-only
pill style and asterisk suffix that distinguishes a planned route
from a live one.
"""

from __future__ import annotations

import json
from pathlib import Path

from shapely.geometry import LineString, MultiLineString, shape

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


def load_manual_future_features(path: Path | str) -> list[dict]:
    """Read the manual GeoJSON and return routes.geojson-shaped features.

    - MultiLineString geometry preserved as-is so each road-snapped
      corridor stays one feature (Leaflet renders MultiLineString
      natively).
    - Empty-geometry placeholder features (geometry coordinates == [])
      are dropped.
    - Features whose `route no` is missing or unparseable are also
      dropped, with the count returned via the second element of the
      tuple so the build can report it.
    """
    path = Path(path)
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))

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
                "phase": FUTURE_PHASE,
            },
        })
    return features


def distinct_routes(features: list[dict]) -> list[str]:
    """Sorted set-union of route_ids across the manual feature list."""
    seen: set[str] = set()
    for f in features:
        for r in f["properties"].get("routes", []) or []:
            seen.add(r)
    return sorted(seen)


def _sample_along_geometry(geom, fractions: tuple[float, ...]) -> list[tuple[float, float]]:
    """Sample (lon, lat) at given length-fractions of a (Multi)LineString.

    Fractions are expressed as 0..1 of the geometry's *total* length.
    For MultiLineString this walks the parts in order, so 0.5 is the
    midpoint of the concatenated route, not the midpoint of one part.
    """
    if isinstance(geom, MultiLineString):
        parts = list(geom.geoms)
    elif isinstance(geom, LineString):
        parts = [geom]
    else:
        return []
    if not parts:
        return []
    total = sum(p.length for p in parts)
    if total <= 0:
        return []

    out: list[tuple[float, float]] = []
    for f in fractions:
        target = total * f
        acc = 0.0
        for part in parts:
            if acc + part.length >= target:
                local = max(0.0, target - acc)
                pt = part.interpolate(local)
                out.append((pt.x, pt.y))
                break
            acc += part.length
    return out


# Sample 5 points evenly along each future route — start, two
# intermediates, two near the end. Same density as the GTFS label
# sampler tends to produce for a typical urban route.
_LABEL_FRACTIONS: tuple[float, ...] = (0.1, 0.3, 0.5, 0.7, 0.9)


def build_future_labels(features: list[dict]) -> list[dict]:
    """Return label-point features matching labels.geojson's schema.

    Each future route_id contributes a handful of points sampled
    along its MultiLineString. Properties:
      routes: [route_id]
      colours: [feature's category colour]
      category, colour, phase: passed through from the route feature
    The viewer reads `phase == "future"` to switch to the outline-
    only pill style and append the asterisk suffix.
    """
    labels: list[dict] = []
    for f in features:
        try:
            geom = shape(f["geometry"])
        except Exception:
            continue
        pts = _sample_along_geometry(geom, _LABEL_FRACTIONS)
        if not pts:
            continue
        p = f["properties"]
        routes = p.get("routes", [])
        colour = p.get("colour")
        category = p.get("category")
        for lon, lat in pts:
            labels.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {
                    "routes": routes,
                    "colours": [colour] * len(routes),
                    "category": category,
                    "colour": colour,
                    "phase": FUTURE_PHASE,
                },
            })
    return labels
