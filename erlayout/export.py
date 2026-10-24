"""Editable exports of a finished layout: draw.io, Excalidraw and Mermaid.

A layout is first turned into a *drawing*: plain data (tables with their
boxes and rows, links with their curves) in one or more sections, one per
diagram of a split schema.  The exporters only read that data, so they work
the same for one diagram, for every group of a split schema, or for a single
group exported on its own (`pick`).

* draw.io keeps the boxes, rows and curves; each link is attached to its two
  rows, so it follows a table that is dragged.  A split schema gets one page
  per group.
* Excalidraw keeps the boxes, rows and curves too; links are bound to their
  tables and each table is grouped, so it moves as one.  A split schema gets
  one frame per group.
* Mermaid has no syntax for positions: it lays the diagram out itself.  The
  export holds the tables and relationships, declared in the layout's order.
"""
from __future__ import annotations

import io
import json
import re
import zipfile
from html import escape

from .geometry import CHAR_W, HEADER_H, ROW_H, Geometry
from .model import Schema
from .render import PALETTE, cell_width, place

FORMATS = {"svg": ".svg", "drawio": ".drawio", "excalidraw": ".excalidraw", "mermaid": ".mmd"}


def _reverse(segs):
    out = []
    for s in reversed(segs):
        out.append(("L", s[2], s[1]) if s[0] == "L" else ("C", s[4], s[3], s[2], s[1]))
    return out


def section(schema: Schema, geo: Geometry, svg: str, name: str | None = None, title: str | None = None) -> dict:
    """One diagram as data.  Every link path runs from the foreign-key row to
    the row it references."""
    tables = []
    for t in schema.tables:
        b = geo.boxes[t.name]
        tables.append({"name": t.name, "x": b.x, "y": b.y, "w": b.w, "h": b.h,
                       "columns": [{"name": c.name, "type": c.type, "pk": c.pk} for c in t.columns]})
    links = []
    for e, segs in zip(schema.solver_edges(), geo.paths):
        start = segs[0][1]
        src = geo.boxes[e.src_table]
        at_src = src.x - 1 <= start[0] <= src.x + src.w + 1 and src.y <= start[1] <= src.y + src.h
        path = segs if at_src else _reverse(segs)
        links.append({"from": [e.src_table, e.src_col], "to": [e.dst_table, e.dst_col],
                      "path": [[s[0], *[list(p) for p in s[1:]]] for s in path]})
    return {"name": name, "title": title, "width": geo.width, "height": geo.height,
            "tables": tables, "links": links, "svg": svg}


def drawing(sections: list[dict], arrange: str = "grid") -> dict:
    """Sections placed the way render.stack_svgs places their SVGs; `x`, `y`
    is where each section's content starts and `cell_w` how wide its slot is.
    One untitled section is not moved."""
    if len(sections) == 1 and not sections[0]["title"]:
        s = sections[0]
        return {"width": s["width"], "height": s["height"], "arrange": arrange,
                "sections": [{**s, "x": 0.0, "y": 0.0, "cell_w": s["width"]}]}
    pos, width, height = place([(s["width"], s["height"]) for s in sections], [s["title"] for s in sections], arrange)
    return {"width": width, "height": height, "arrange": arrange,
            "sections": [{**s, "x": x, "y": y, "cell_w": cell_width(s["width"], s["title"])}
                         for s, (x, y) in zip(sections, pos)]}


def pick(d: dict, group: int) -> dict:
    """The drawing of section `group` (1-based) alone."""
    if not 1 <= group <= len(d["sections"]):
        raise ValueError(f"group must be between 1 and {len(d['sections'])}")
    s = dict(d["sections"][group - 1], title=None)
    return drawing([s])


def slug(text: str) -> str:
    return re.sub(r"[^\w-]+", "-", text).strip("-").lower()[:60] or "diagram"


# ---------------------------------------------------------------- draw.io
def _q(v) -> str:
    return escape(str(v), quote=True)


def _drawio_page(s: dict, page: int) -> str:
    cells = ['<mxCell id="0"/>', '<mxCell id="1" parent="0"/>']
    rid = {}
    for ti, t in enumerate(s["tables"]):
        tid = f"p{page}t{ti}"
        h = HEADER_H + ROW_H * len(t["columns"])
        # draw.io's table layout wants the same two column widths in every row
        type_w = min(t["w"] - 40, max([len(c["type"]) * CHAR_W + 24 for c in t["columns"]] + [0]))
        cells.append(
            f'<mxCell id="{tid}" value="{_q(t["name"])}" style="shape=table;startSize={HEADER_H};container=1;'
            'collapsible=0;childLayout=tableLayout;fixedRows=1;rowLines=0;columnLines=0;fontStyle=1;align=center;resizeLast=1;'
            'fillColor=#eef1f6;strokeColor=#c9ccd1;swimlaneFillColor=#ffffff;fontColor=#1d2a3a;fontSize=14;" '
            f'vertex="1" parent="1"><mxGeometry x="{t["x"]:.0f}" y="{t["y"]:.0f}" width="{t["w"]:.0f}" '
            f'height="{h}" as="geometry"/></mxCell>')
        for ci, c in enumerate(t["columns"]):
            row = f"{tid}r{ci}"
            rid[(t["name"], c["name"])] = row
            cells.append(
                f'<mxCell id="{row}" value="" style="shape=tableRow;horizontal=0;startSize=0;swimlaneHead=0;'
                'swimlaneBody=0;fillColor=none;collapsible=0;dropTarget=0;points=[[0,0.5],[1,0.5]];'
                'portConstraint=eastwest;top=0;left=0;right=0;bottom=0;" vertex="1" '
                f'parent="{tid}"><mxGeometry y="{HEADER_H + ci * ROW_H}" width="{t["w"]:.0f}" height="{ROW_H}" '
                'as="geometry"/></mxCell>')
            cell = ('shape=partialRectangle;connectable=0;fillColor=none;strokeColor=none;top=0;left=0;bottom=0;'
                    'right=0;overflow=hidden;fontSize=12;')
            cells.append(
                f'<mxCell id="{row}n" value="{_q(c["name"])}" style="{cell}align=left;spacingLeft=10;'
                f'fontColor=#2b2f36;fontStyle={1 if c["pk"] else 0};" vertex="1" parent="{row}">'
                f'<mxGeometry width="{t["w"] - type_w:.0f}" height="{ROW_H}" as="geometry"/></mxCell>')
            cells.append(
                f'<mxCell id="{row}t" value="{_q(c["type"])}" style="{cell}align=right;spacingRight=10;'
                f'fontColor=#8a9099;" vertex="1" parent="{row}"><mxGeometry x="{t["w"] - type_w:.0f}" '
                f'width="{type_w:.0f}" height="{ROW_H}" as="geometry"/></mxCell>')
    for li, ln in enumerate(s["links"]):
        segs = ln["path"]
        p0, p1 = segs[0][1], segs[-1][-1]
        # waypoints: a horizontal stub at both ends of every curve, and the points where a
        # link passes a column; draw.io's curved style rounds them into the same S-curves
        pts = []
        for seg in segs:
            if seg[0] == "C":
                a, b = seg[1], seg[4]
                dx = (b[0] - a[0]) / 3
                pts += [(a[0] + dx, a[1]), (b[0] - dx, b[1])]
        out_x = 1 if p0[0] < p1[0] else 0
        col = PALETTE[li % len(PALETTE)]
        arr = "".join(f'<mxPoint x="{x:.1f}" y="{y:.1f}"/>' for x, y in pts)
        cells.append(
            f'<mxCell id="p{page}l{li}" style="curved=1;endArrow=ERmandOne;startArrow=ERmany;endFill=0;'
            f'startFill=0;strokeColor={col};strokeWidth=1.6;exitX={out_x};exitY=0.5;exitDx=0;exitDy=0;'
            f'entryX={1 - out_x};entryY=0.5;entryDx=0;entryDy=0;" edge="1" parent="1" '
            f'source="{rid[tuple(ln["from"])]}" target="{rid[tuple(ln["to"])]}">'
            f'<mxGeometry relative="1" as="geometry"><Array as="points">{arr}</Array></mxGeometry></mxCell>')
    return "".join(cells)


def to_drawio(d: dict) -> str:
    """An uncompressed .drawio file; a split schema gets one page per group."""
    pages = []
    for i, s in enumerate(d["sections"]):
        name = s["name"] or "ER diagram"
        pages.append(f'<diagram id="erlayout-{i}" name="{_q(name)}"><mxGraphModel grid="1" gridSize="10" '
                     'guides="1" tooltips="1" connect="1" arrows="1" fold="1" page="0" pageScale="1" math="0" '
                     f'shadow="0"><root>{_drawio_page(s, i)}</root></mxGraphModel></diagram>')
    return f'<mxfile host="erlayout" type="device">{"".join(pages)}</mxfile>\n'


# ---------------------------------------------------------------- Excalidraw
def _bezier(p0, c1, c2, p1, steps=8):
    out = []
    for i in range(1, steps + 1):
        t = i / steps
        a, b, c, e = (1 - t) ** 3, 3 * (1 - t) ** 2 * t, 3 * (1 - t) * t ** 2, t ** 3
        out.append((a * p0[0] + b * c1[0] + c * c2[0] + e * p1[0], a * p0[1] + b * c1[1] + c * c2[1] + e * p1[1]))
    return out


def to_excalidraw(d: dict) -> str:
    """An .excalidraw file; a split schema gets one frame per group."""
    els: list[dict] = []
    n = [0]

    def el(kind, x, y, w, h, **kw):
        n[0] += 1
        e = {"id": f"er{n[0]}", "type": kind, "x": round(x, 1), "y": round(y, 1), "width": round(w, 1),
             "height": round(h, 1), "angle": 0, "strokeColor": "#1e1e1e", "backgroundColor": "transparent",
             "fillStyle": "solid", "strokeWidth": 1, "strokeStyle": "solid", "roughness": 0, "opacity": 100,
             "groupIds": [], "frameId": None, "roundness": None, "seed": n[0], "version": 1,
             "versionNonce": n[0], "isDeleted": False, "boundElements": [], "updated": 1, "link": None,
             "locked": False}
        e.update(kw)
        els.append(e)
        return e

    def text(x, y, s, size, w=None, align="left", **kw):
        w = w if w is not None else max(1.0, len(s) * size * 0.56)
        return el("text", x, y, w, size * 1.25, text=s, originalText=s, fontSize=size, fontFamily=2,
                  textAlign=align, verticalAlign="top", containerId=None, lineHeight=1.25, autoResize=True,
                  strokeColor=kw.pop("strokeColor", "#2b2f36"), **kw)

    for si, s in enumerate(d["sections"]):
        ox, oy = s["x"], s["y"]
        frame_id = f"frame{si}" if s["title"] else None
        start = len(els)
        boxes = {}
        for ti, t in enumerate(s["tables"]):
            gid = f"s{si}t{ti}"
            x, y, w, h = t["x"] + ox, t["y"] + oy, t["w"], t["h"]
            box = el("rectangle", x, y, w, h, backgroundColor="#ffffff", strokeColor="#c9ccd1",
                     groupIds=[gid], roundness={"type": 3})
            boxes[t["name"]] = box
            el("rectangle", x, y, w, HEADER_H, backgroundColor="#eef1f6", strokeColor="#c9ccd1", groupIds=[gid])
            text(x, y + 8, t["name"], 14, w=w, align="center", groupIds=[gid], strokeColor="#1d2a3a")
            for r, c in enumerate(t["columns"]):
                ry = y + HEADER_H + r * ROW_H + 6
                text(x + 12, ry, c["name"], 12, groupIds=[gid])
                if c["type"]:
                    tw = len(c["type"]) * 12 * 0.56
                    text(x + w - 12 - tw, ry, c["type"], 12, w=tw, align="right", groupIds=[gid],
                         strokeColor="#8a9099")
        for li, ln in enumerate(s["links"]):
            pts = [tuple(ln["path"][0][1])]
            for seg in ln["path"]:
                pts += [tuple(seg[2])] if seg[0] == "L" else _bezier(*seg[1:])
            pts = [(px + ox, py + oy) for px, py in pts]
            x0, y0 = pts[0]
            rel = [[round(px - x0, 1), round(py - y0, 1)] for px, py in pts]
            xs, ys = [p[0] for p in rel], [p[1] for p in rel]
            a, b = boxes[ln["from"][0]], boxes[ln["to"][0]]
            arrow = el("arrow", x0, y0, max(xs) - min(xs), max(ys) - min(ys), points=rel,
                       strokeColor=PALETTE[li % len(PALETTE)], strokeWidth=1.5, roundness={"type": 2},
                       lastCommittedPoint=None, startArrowhead=None, endArrowhead="arrow",
                       startBinding={"elementId": a["id"], "focus": 0, "gap": 1},
                       endBinding={"elementId": b["id"], "focus": 0, "gap": 1}, elbowed=False)
            a["boundElements"].append({"id": arrow["id"], "type": "arrow"})
            b["boundElements"].append({"id": arrow["id"], "type": "arrow"})
        if frame_id:
            for e in els[start:]:
                e["frameId"] = frame_id
            # frames come after their children
            el("frame", ox + 20, oy - 34, s["cell_w"] - 40, s["height"] + 34, id=frame_id, name=s["title"],
               strokeColor="#bbb")
    return json.dumps({"type": "excalidraw", "version": 2, "source": "https://github.com/ore-codes/erlayout",
                       "elements": els, "appState": {"viewBackgroundColor": "#fafaf8", "gridSize": None},
                       "files": {}}, ensure_ascii=False) + "\n"


# ---------------------------------------------------------------- Mermaid
def _mm(word: str, fallback: str) -> str:
    """Mermaid erDiagram names: letters, digits, _ - ( ) [ ], not starting with a digit."""
    w = re.sub(r"[^\w\-()\[\]]+", "_", word.strip()).strip("_")
    if not w:
        return fallback
    return "_" + w if w[0].isdigit() else w


def to_mermaid(schema: Schema, d: dict | None = None) -> str:
    """A Mermaid erDiagram of `schema`.  With a drawing, tables are declared
    section by section in its left-to-right, top-to-bottom order, and only
    the tables it holds are included (a single exported group keeps the
    links that leave it, so the tables they reach are declared too)."""
    by = {t.name: t for t in schema.tables}
    names = []
    if d is None:
        names = [[None, [t.name for t in schema.tables]]]
    else:
        for s in d["sections"]:
            order = sorted(s["tables"], key=lambda t: (round(t["x"]), t["y"]))
            names.append([s["name"], [t["name"] for t in order if t["name"] in by]])
    shown = {n for _, ns in names for n in ns}
    edges = [e for e in schema.edges if e.src_table in shown or e.dst_table in shown]
    reached = [n for e in edges for n in (e.src_table, e.dst_table) if n not in shown]
    if reached:
        names.append(["referenced tables outside this diagram", list(dict.fromkeys(reached))])
    fk = {(e.src_table, e.src_col.lower()) for e in schema.edges}
    ident = {t.name: _mm(t.name, f"table{i}") for i, t in enumerate(schema.tables)}
    out = ["erDiagram"]
    for title, ns in names:
        if title:
            out.append(f"    %% {title}")
        for name in ns:
            t = by[name]
            out.append(f"    {ident[name]} {{")
            for c in t.columns:
                keys = ", ".join(k for k, on in (("PK", c.pk), ("FK", (name, c.name.lower()) in fk)) if on)
                out.append(f"        {_mm(c.type, 'any')} {_mm(c.name, 'column')}" + (f" {keys}" if keys else ""))
            out.append("    }")
    for e in edges:
        out.append(f'    {ident[e.src_table]} }}o--|| {ident[e.dst_table]} : "{e.src_col.replace(chr(34), "")}"')
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- one entry point
def export(fmt: str, d: dict, schema: Schema) -> str:
    if fmt == "svg":
        from .render import stack_svgs
        ss = d["sections"]
        if len(ss) == 1 and not ss[0]["title"]:
            return ss[0]["svg"]
        return stack_svgs([(s["title"], s["svg"]) for s in ss], d.get("arrange", "grid"))
    if fmt == "drawio":
        return to_drawio(d)
    if fmt == "excalidraw":
        return to_excalidraw(d)
    if fmt == "mermaid":
        return to_mermaid(schema, d)
    raise ValueError(f"format must be one of {', '.join(FORMATS)}")


def export_each(fmt: str, d: dict, schema: Schema) -> bytes:
    """A zip holding one file per section (group) in format `fmt`."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for i, s in enumerate(d["sections"], 1):
            name = f"{i:02d}-{slug(s['name'] or 'diagram')}{FORMATS[fmt]}"
            z.writestr(name, export(fmt, pick(d, i), schema))
    return buf.getvalue()
