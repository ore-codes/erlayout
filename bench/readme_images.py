"""Regenerate the before/after images at the top of the README.

Lays out the Sakila example three ways (the standard longest-path +
barycenter layout, erlayout with rows as declared, erlayout with rows
reordered freely), circles every crossing in red, and renders each SVG to PNG
with resvg.

  uv run --group images python bench/readme_images.py
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import resvg_py

from erlayout.api import solve
from erlayout.geometry import _flatten, _seg_intersections, assign
from erlayout.heuristic import heuristic
from erlayout.parse import parse_any
from erlayout.render import render_svg
from erlayout.solver import Layout

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "img"


def crossing_points(geo) -> list[tuple[float, float]]:
    """The points where two drawn links cross, as geometry.geometric_check counts them."""
    polys = [_flatten(p) for p in geo.paths]
    ends = [{tuple(np.round(P[0], 3)), tuple(np.round(P[-1], 3))} for P in polys]
    found = []
    for a in range(len(polys)):
        for b in range(a + 1, len(polys)):
            shared = ends[a] & ends[b]
            uniq = []
            for p in _seg_intersections(polys[a], polys[b]):
                if any(np.hypot(p[0] - s[0], p[1] - s[1]) < 1e-3 for s in shared):
                    continue
                if not any(np.hypot(p[0] - u[0], p[1] - u[1]) < 0.5 for u in uniq):
                    uniq.append(p)
            found += [(float(x), float(y)) for x, y in uniq]
    return found


def circled(schema, lay: Layout) -> tuple[str, int]:
    schema = schema.with_row_order(lay.rows)
    geo = assign(schema, lay)
    pts = crossing_points(geo)
    rings = "".join(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="34" fill="#e5383b" fill-opacity="0.12" '
                    f'stroke="#e5383b" stroke-width="7"/>' for x, y in pts)
    svg = render_svg(schema, geo).replace("</g></svg>", f"</g>{rings}</svg>")
    return svg, len(pts)


def to_png(svg: str, out: Path):
    out.write_bytes(bytes(resvg_py.svg_to_bytes(svg_string=svg, background="#fafaf8")))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--time", type=float, default=120)
    a = ap.parse_args()
    schema = parse_any((ROOT / "examples" / "sakila.sql").read_text())
    layouts = {"sakila-standard": heuristic(schema)}
    for name, reorder, pin in (("sakila-erlayout-declared", False, True), ("sakila-erlayout-reordered", True, False)):
        res = solve(schema, time_limit=a.time, reorder_rows=reorder, pin_pk=pin)
        assert res["global_optimal"], f"{name}: not proven optimal within {a.time} s"
        lay = res["layout"]
        layouts[name] = Layout(lay["columns"], [[(k, r) for k, r in o] for o in lay["orders"]],
                               res["crossings"], res["span"], rows=res["row_order"] or None)
    OUT.mkdir(parents=True, exist_ok=True)
    for name, lay in layouts.items():
        svg, n = circled(schema, lay)
        to_png(svg, OUT / f"{name}.png")
        print(f"{name}: {n} crossings -> {OUT / name}.png")


if __name__ == "__main__":
    main()
