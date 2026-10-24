"""SVG rendering of a Geometry."""
from __future__ import annotations

import re
from html import escape

from .geometry import HEADER_H, ROW_H, Geometry
from .model import Schema

PALETTE = ["#2f6fb3", "#b5452d", "#2e8b57", "#8a4fbf", "#c28a12", "#1f8a99",
           "#b33f7a", "#5d6b2f", "#6b5bd6", "#a0522d"]


def _path_d(segs) -> str:
    d = []
    for i, s in enumerate(segs):
        if s[0] == "L":
            p0, p1 = s[1], s[2]
            if i == 0:
                d.append(f"M{p0[0]:.1f},{p0[1]:.1f}")
            d.append(f"L{p1[0]:.1f},{p1[1]:.1f}")
        else:
            _, p0, c1, c2, p1 = s
            if i == 0:
                d.append(f"M{p0[0]:.1f},{p0[1]:.1f}")
            d.append(f"C{c1[0]:.1f},{c1[1]:.1f} {c2[0]:.1f},{c2[1]:.1f} {p1[0]:.1f},{p1[1]:.1f}")
    return " ".join(d)


def render_svg(schema: Schema, geo: Geometry, title: str | None = None) -> str:
    edges = schema.solver_edges()
    W, H = geo.width, geo.height + (30 if title else 0)
    oy = 30 if title else 0
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W:.0f} {H:.0f}" '
           f'width="{W:.0f}" height="{H:.0f}" font-family="Inter, Segoe UI, Helvetica, Arial, sans-serif">',
           '<rect width="100%" height="100%" fill="#fafaf8"/>']
    if title:
        out.append(f'<text x="40" y="26" font-size="15" font-weight="600" fill="#222">{escape(title)}</text>')
    out.append(f'<g transform="translate(0,{oy})">')
    # edges below tables
    out.append('<g fill="none" stroke-width="1.6" stroke-opacity="0.85">')
    for j, (e, segs) in enumerate(zip(edges, geo.paths)):
        col = PALETTE[j % len(PALETTE)]
        out.append(f'<path d="{_path_d(segs)}" stroke="{col}"><title>{escape(e.label)}</title></path>')
    out.append("</g>")
    # tables
    for t in schema.tables:
        b = geo.boxes[t.name]
        out.append(f'<g><rect x="{b.x:.1f}" y="{b.y:.1f}" width="{b.w:.1f}" height="{b.h:.1f}" rx="6" '
                   f'fill="#ffffff" stroke="#c9ccd1"/>')
        out.append(f'<path d="M{b.x:.1f},{b.y + 6:.1f} a6,6 0 0 1 6,-6 h{b.w - 12:.1f} a6,6 0 0 1 6,6 '
                   f'v{HEADER_H - 6:.1f} h{-b.w:.1f} z" fill="#eef1f6" stroke="#c9ccd1"/>')
        out.append(f'<text x="{b.x + b.w / 2:.1f}" y="{b.y + 22:.1f}" text-anchor="middle" font-size="14" '
                   f'font-weight="600" fill="#1d2a3a">{escape(t.name)}</text>')
        for r, c in enumerate(t.columns):
            y = b.y + HEADER_H + r * ROW_H + ROW_H / 2 + 4
            weight = "600" if c.pk else "400"
            out.append(f'<text x="{b.x + 12:.1f}" y="{y:.1f}" font-size="12.5" font-weight="{weight}" '
                       f'fill="#2b2f36">{escape(c.name)}</text>')
            out.append(f'<text x="{b.x + b.w - 12:.1f}" y="{y:.1f}" font-size="11.5" text-anchor="end" '
                       f'fill="#8a9099">{escape(c.type)}</text>')
        out.append("</g>")
    # port dots on top
    out.append('<g>')
    for j, segs in enumerate(geo.paths):
        col = PALETTE[j % len(PALETTE)]
        first, last = segs[0], segs[-1]
        p0 = first[1]
        p1 = last[-1]
        for p in (p0, p1):
            out.append(f'<circle cx="{p[0]:.1f}" cy="{p[1]:.1f}" r="3" fill="{col}"/>')
    out.append("</g></g></svg>")
    return "\n".join(out)


HEADING = 34          # room above each diagram of a split schema for its title
ARRANGEMENTS = ("grid", "row", "column")


def cell_width(width: float, title: str) -> float:
    """A diagram's width, widened so its title does not run into the next one."""
    return max(width, len(title) * 10.5 + 80)


def _shelves(cells, limit, gap):
    """Left to right, starting a new row when the next diagram would pass `limit`."""
    pos, x, y, row_h, W = [], 0.0, gap, 0.0, 0.0
    for w, h in cells:
        if x > 0 and x + w > limit:
            x, y, row_h = 0.0, y + row_h + gap, 0.0
        pos.append((x, y + HEADING))
        W = max(W, x + w)
        x += w + gap
        row_h = max(row_h, h + HEADING)
    return pos, W, y + row_h + gap


def place(sizes: list[tuple[float, float]], titles: list[str], how: str = "grid",
          gap: int = 24) -> tuple[list[tuple[float, float]], float, float]:
    """Where each titled diagram starts (its title sits in the HEADING px above),
    and the total width and height.  "column" stacks the diagrams, "row" puts
    them side by side, and "grid" fills rows to the width that brings the
    whole picture closest to a 16:10 screen."""
    if how not in ARRANGEMENTS:
        raise ValueError(f"arrange must be one of {', '.join(ARRANGEMENTS)}")
    cells = [(cell_width(w, t), h) for (w, h), t in zip(sizes, titles)]
    widest = max([w for w, _ in cells] + [400])
    if how == "column":
        pos, W, H = _shelves(cells, 0, gap)
    elif how == "row":
        pos, W, H = _shelves(cells, float("inf"), gap)
    else:
        area = sum((w + gap) * (h + HEADING + gap) for w, h in cells)
        best = None
        for f in [0.5 + 0.1 * i for i in range(16)]:
            limit = max(widest, f * (area * 1.6) ** 0.5)
            pos, W, H = _shelves(cells, limit, gap)
            r = (W / H) / 1.6
            score = W * H * max(r, 1 / r)      # wasted space, and distance from 16:10
            if best is None or score < best[0]:
                best = (score, pos, W, H)
        _, pos, W, H = best
    return pos, max(W, 400), H


def stack_svgs(parts: list[tuple[str, str]], how: str = "grid", gap: int = 24) -> str:
    """One SVG holding several titled diagrams, arranged as `place` says."""
    sizes = []
    for _, svg in parts:
        w, h = re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', svg).groups()
        sizes.append((float(w), float(h)))
    titles = [t for t, _ in parts]
    pos, W, H = place(sizes, titles, how, gap)
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W:.0f} {H:.0f}" width="{W:.0f}" '
           f'height="{H:.0f}" font-family="Inter, Segoe UI, Helvetica, Arial, sans-serif">',
           '<rect width="100%" height="100%" fill="#fafaf8"/>']
    for (title, svg), (w, _), (x, y) in zip(parts, sizes, pos):
        cw = cell_width(w, title)
        out.append(f'<text x="{x + 40:.0f}" y="{y - 12:.0f}" font-size="18" font-weight="600" fill="#1d2a3a">'
                   f'{escape(title)}</text>')
        out.append(f'<line x1="{x + 40:.0f}" x2="{x + cw - 40:.0f}" y1="{y - 2:.0f}" y2="{y - 2:.0f}" '
                   'stroke="#d9dce1"/>')
        out.append(svg.replace("<svg ", f'<svg x="{x:.0f}" y="{y:.0f}" ', 1))
    out.append("</svg>")
    return "\n".join(out)
