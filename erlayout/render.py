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


def stack_svgs(parts: list[tuple[str, str]], gap: int = 24) -> str:
    """One SVG holding several titled diagrams one above the other."""
    sizes = []
    for _, svg in parts:
        w, h = re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', svg).groups()
        sizes.append((float(w), float(h)))
    W = max([w for w, _ in sizes] + [400])
    H = sum(h + 34 + gap for _, h in sizes) + gap
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W:.0f} {H:.0f}" width="{W:.0f}" '
           f'height="{H:.0f}" font-family="Inter, Segoe UI, Helvetica, Arial, sans-serif">',
           '<rect width="100%" height="100%" fill="#fafaf8"/>']
    y = gap
    for (title, svg), (_, h) in zip(parts, sizes):
        out.append(f'<text x="40" y="{y + 22:.0f}" font-size="18" font-weight="600" fill="#1d2a3a">'
                   f'{escape(title)}</text>')
        out.append(f'<line x1="40" x2="{W - 40:.0f}" y1="{y + 32:.0f}" y2="{y + 32:.0f}" stroke="#d9dce1"/>')
        out.append(svg.replace("<svg ", f'<svg x="0" y="{y + 34:.0f}" ', 1))
        y += h + 34 + gap
    out.append("</svg>")
    return "\n".join(out)
