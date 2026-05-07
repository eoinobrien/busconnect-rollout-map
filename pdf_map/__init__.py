"""Extract future BusConnects routes from the official Big Picture map PDF.

Phases not yet in GTFS only exist as static maps published by the NTA
(busconnects.ie). The latest revision is a single-page A3 vector PDF
where every route line is a stroked path in the spine-letter palette
and every route is labelled by one or more shield text spans. This
package walks that vector data, georeferences it from named-park
landmark labels, and emits per-route GeoJSON LineStrings shaped like
the bus pipeline's `routes.geojson` so the front-end can render it
alongside live phases.

Output is *schematic*, not road-snapped — the source map is drawn as
smoothed corridor curves. Treat the geometry as approximate; manual
post-extraction adjustment is expected for road-accurate placement.
"""
