"""CLI: build the future-routes GeoJSON from a BusConnects map PDF."""

from __future__ import annotations

import argparse
from pathlib import Path

from pdf_map.build import build_future_routes, write_outputs


DEFAULT_PDF = "data/pdf/big-picture-2025-10-02.pdf"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", default=DEFAULT_PDF)
    parser.add_argument("--out", default="output")
    args = parser.parse_args()

    geojson, meta = build_future_routes(args.pdf)
    write_outputs(args.out, geojson, meta)

    print(f"Source PDF:               {meta['source_pdf']}")
    print(f"Total polylines:          {meta['drawings_total']}")
    print(f"Candidate route paths:    {meta['candidate_paths']}")
    print(f"  after inset filter:     {meta['candidate_paths_after_inset_filter']}")
    print(f"Route shield labels:      {meta['shield_spans']}")
    print(f"Distinct routes matched:  {meta['distinct_routes']}")
    print(f"Paths with route_id:      {meta['paths_with_routes']}")
    res = meta["georef_residuals_m"]
    print(f"Georef residuals (m):     median {sorted(res.values())[len(res) // 2]:.0f} max {max(res.values()):.0f}")
    print(f"Wrote: {args.out}/future-routes.geojson, future-routes-meta.json")


if __name__ == "__main__":
    main()
