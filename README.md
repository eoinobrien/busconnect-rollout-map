# Dublin City Bus Map

Interactive Leaflet map of Dublin Bus and Go-Ahead Dublin city routes for a given weekday, built from the [Transport for Ireland GTFS feed](https://www.transportforireland.ie/transitData/Data/GTFS_Realtime.zip).

Lettered BusConnects spine routes are bundled where they share a path and split where they diverge. As of May 2026 the rolled-out Dublin spines are **C1-C6, E1-E2, F1-F3, G1-G2, H1-H3**; the A, B, and D spines have not yet launched in Dublin (the A1/A2/B1/D1-D5 entries in the GTFS belong to Bus Eireann's Athlone/Drogheda networks under agency 7778020).

## Rebuild

```bash
# 1. Download and extract the GTFS feed
mkdir -p data
curl -L -o data/GTFS_Realtime.zip https://www.transportforireland.ie/transitData/Data/GTFS_Realtime.zip
unzip -o data/GTFS_Realtime.zip -d data/gtfs/

# 2. Install Python deps
pip install -r requirements.txt

# 3. Build the GeoJSON (defaults to next non-holiday Tuesday; override with --date)
python build.py --date 2026-05-05

# 4. Open the map
# Most browsers block fetch() on file://, so serve locally:
python -m http.server 8000 --bind 127.0.0.1
# then visit http://localhost:8000/
```

## Tests

```bash
# Python: synthetic-GTFS unit + integration tests
python -m pytest tests/ -v

# JavaScript: Node-based viewer tests
node tests/test_viewer_visibility.js
node tests/test_viewer_init.js
```

The suite covers the calendar/services resolver, agency filter, shape
selection, route categorisation, direction merging, frequency
classification, the per-route pipeline against a synthetic GTFS feed,
the LUAS/Iarnród Éireann rail builder, the future-routes merge/strip
helpers, the PDF future-routes extraction stages, and the viewer's
label visibility logic.

## Scope

- Includes agencies `7778019` (Dublin Bus) and `7778021` (Go-Ahead Ireland Dublin metro).
- Excludes agency `7778006` (Go-Ahead's former Bus Eireann commuter routes — Dublin to Edenderry / Newbridge / Kildare / Athy).
- Renders the most-frequent representative shape per route per direction on the chosen reference date.
