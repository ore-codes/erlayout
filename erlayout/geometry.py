"""Coordinates for a combinatorial layout, and an independent geometric
verifier that counts crossings on the actual drawn curves.

Vertical coordinates come from a linear program that keeps every column's
stacking order (so the crossing count is unchanged) while minimising the
vertical travel of edges across each gap."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from ortools.linear_solver import pywraplp

from .model import Schema
from .solver import Layout

CHAR_W = 7.4
ROW_H = 26
HEADER_H = 34
GAP_Y = 28
DUMMY_GAP = 12
GAP_X = 150
PAD = 40


@dataclass
class Box:
    name: str
    x: float
    y: float
    w: float
    h: float


@dataclass
class Geometry:
    boxes: dict[str, Box]
    paths: list[list[tuple]]          # per edge: list of segments; ('L',p0,p1) or ('C',p0,c1,c2,p1)
    width: float
    height: float
    col_x: list[float]
    col_w: list[float]


def table_width(t) -> float:
    longest = max([len(t.name) + 2] + [len(c.name) + len(c.type) + 4 for c in t.columns])
    return max(150.0, longest * CHAR_W + 32)


def table_height(t) -> float:
    return HEADER_H + len(t.columns) * ROW_H + 6


def port_y(schema: Schema, box: Box, table: str, col: str) -> float:
    r = schema.table(table).row(col)
    return box.y + HEADER_H + r * ROW_H + ROW_H / 2


def assign(schema: Schema, lay: Layout) -> Geometry:
    edges = schema.solver_edges()
    k = len(lay.orders)
    col_w = []
    for c in range(k):
        ws = [table_width(schema.table(r)) for kind, r in lay.orders[c] if kind == "T"]
        col_w.append(max(ws) if ws else 60.0)
    col_x = []
    x = PAD
    for c in range(k):
        col_x.append(x)
        x += col_w[c] + GAP_X
    width = x - GAP_X + PAD

    # ---- LP for vertical positions
    lp = pywraplp.Solver.CreateSolver("GLOP")
    inf = lp.infinity()
    top = {}
    for c, order in enumerate(lay.orders):
        prev = None
        for it in order:
            v = lp.NumVar(0, inf, f"y{c}_{it}")
            top[(c, it)] = v
            if prev is not None:
                pit, pv = prev
                ph = table_height(schema.table(pit[1])) if pit[0] == "T" else 0.0
                gap = GAP_Y if (pit[0] == "T" or it[0] == "T") else DUMMY_GAP
                lp.Add(v >= pv + ph + gap)
            prev = (it, v)

    def end_y(j, c):
        e = edges[j]
        for tname, col in ((e.src_table, e.src_col), (e.dst_table, e.dst_col)):
            if lay.columns[tname] == c:
                r = schema.table(tname).row(col)
                return top[(c, ("T", tname))] + (HEADER_H + r * ROW_H + ROW_H / 2)
        return top[(c, ("D", j))]

    obj = lp.Objective()
    terms = []
    for j, e in enumerate(edges):
        a, b = sorted((lay.columns[e.src_table], lay.columns[e.dst_table]))
        for g in range(a, b):
            d = lp.NumVar(0, inf, f"d{j}_{g}")
            lp.Add(d >= end_y(j, g) - end_y(j, g + 1))
            lp.Add(d >= end_y(j, g + 1) - end_y(j, g))
            terms.append(d)
    for d in terms:
        obj.SetCoefficient(d, 1.0)
    for v in top.values():
        obj.SetCoefficient(v, 1e-3)          # compactness tie-break
    obj.SetMinimization()
    if lp.Solve() != pywraplp.Solver.OPTIMAL:
        raise RuntimeError("coordinate LP failed")

    boxes = {}
    height = 0.0
    dummy_y = {}
    for (c, it), v in top.items():
        y = v.solution_value() + PAD
        if it[0] == "T":
            t = schema.table(it[1])
            boxes[it[1]] = Box(it[1], col_x[c], y, col_w[c], table_height(t))
            height = max(height, y + table_height(t))
        else:
            dummy_y[(c, it[1])] = y
            height = max(height, y)
    height += PAD

    paths = []
    for j, e in enumerate(edges):
        cs, ct = lay.columns[e.src_table], lay.columns[e.dst_table]
        # walk left -> right
        if cs < ct:
            left = (e.src_table, e.src_col); right = (e.dst_table, e.dst_col)
        else:
            left = (e.dst_table, e.dst_col); right = (e.src_table, e.src_col)
        a, b = min(cs, ct), max(cs, ct)
        lb, rb = boxes[left[0]], boxes[right[0]]
        pts_in = {a: (lb.x + lb.w, port_y(schema, lb, *left))}
        pts_out = {b: (rb.x, port_y(schema, rb, *right))}
        for c in range(a + 1, b):
            y = dummy_y[(c, j)]
            pts_out[c] = (col_x[c], y)
            pts_in[c] = (col_x[c] + col_w[c], y)
        segs = []
        for c in range(a, b):
            p0, p1 = pts_in[c], pts_out[c + 1]
            mx = (p0[0] + p1[0]) / 2
            segs.append(("C", p0, (mx, p0[1]), (mx, p1[1]), p1))
            if c + 1 < b:
                segs.append(("L", pts_out[c + 1], pts_in[c + 1]))
        paths.append(segs)
    return Geometry(boxes, paths, width, height, col_x, col_w)


# ---------------------------------------------------------------- verifier
def _flatten(segs, steps=48):
    pts = []
    for s in segs:
        if s[0] == "L":
            seg = [s[1], s[2]]
        else:
            _, p0, c1, c2, p1 = s
            t = np.linspace(0, 1, steps + 1)[:, None]
            P = ((1 - t) ** 3) * np.array(p0) + 3 * ((1 - t) ** 2) * t * np.array(c1) \
                + 3 * (1 - t) * t ** 2 * np.array(c2) + t ** 3 * np.array(p1)
            seg = [tuple(p) for p in P]
        if pts and np.allclose(pts[-1], seg[0]):
            seg = seg[1:]
        pts.extend(seg)
    return np.array(pts, dtype=float)


def _seg_intersections(A, B, eps=1e-7):
    """All proper intersection points between polylines A (n,2) and B (m,2)."""
    a0, a1 = A[:-1], A[1:]
    b0, b1 = B[:-1], B[1:]
    # bounding-box prefilter
    axmin = np.minimum(a0[:, 0], a1[:, 0])[:, None]; axmax = np.maximum(a0[:, 0], a1[:, 0])[:, None]
    aymin = np.minimum(a0[:, 1], a1[:, 1])[:, None]; aymax = np.maximum(a0[:, 1], a1[:, 1])[:, None]
    bxmin = np.minimum(b0[:, 0], b1[:, 0])[None]; bxmax = np.maximum(b0[:, 0], b1[:, 0])[None]
    bymin = np.minimum(b0[:, 1], b1[:, 1])[None]; bymax = np.maximum(b0[:, 1], b1[:, 1])[None]
    cand = (axmin <= bxmax) & (bxmin <= axmax) & (aymin <= bymax) & (bymin <= aymax)
    ii, jj = np.nonzero(cand)
    out = []
    for i, j in zip(ii, jj):
        p, r = a0[i], a1[i] - a0[i]
        q, s = b0[j], b1[j] - b0[j]
        den = r[0] * s[1] - r[1] * s[0]
        if abs(den) < 1e-12:
            continue
        t = ((q[0] - p[0]) * s[1] - (q[1] - p[1]) * s[0]) / den
        u = ((q[0] - p[0]) * r[1] - (q[1] - p[1]) * r[0]) / den
        if -eps <= t <= 1 + eps and -eps <= u <= 1 + eps:
            out.append(p + t * r)
    return out


def geometric_check(schema: Schema, geo: Geometry) -> dict:
    """Counts crossings between drawn edges (touching at a shared port is
    not a crossing), and checks that no edge passes through a table and no
    two tables overlap.  Shares no code with the solver."""
    polys = [_flatten(p) for p in geo.paths]
    ends = [{tuple(np.round(P[0], 3)), tuple(np.round(P[-1], 3))} for P in polys]
    lo = [P.min(axis=0) - 1e-6 for P in polys]
    hi = [P.max(axis=0) + 1e-6 for P in polys]
    crossings = 0
    for a in range(len(polys)):
        for b in range(a + 1, len(polys)):
            if (lo[a] > hi[b]).any() or (lo[b] > hi[a]).any():
                continue                # bounding boxes apart: cannot intersect
            pts = _seg_intersections(polys[a], polys[b])
            shared = ends[a] & ends[b]
            uniq = []
            for p in pts:
                if any(np.hypot(p[0] - s[0], p[1] - s[1]) < 1e-3 for s in shared):
                    continue
                if not any(np.hypot(p[0] - u[0], p[1] - u[1]) < 0.5 for u in uniq):
                    uniq.append(p)
            crossings += len(uniq)
    through = 0
    for P in polys:
        for bx in geo.boxes.values():
            inside = (P[:, 0] > bx.x + 1) & (P[:, 0] < bx.x + bx.w - 1) & \
                     (P[:, 1] > bx.y + 1) & (P[:, 1] < bx.y + bx.h - 1)
            if inside.any():
                through += 1
    overlaps = 0
    bl = list(geo.boxes.values())
    for i in range(len(bl)):
        for j in range(i + 1, len(bl)):
            A, B = bl[i], bl[j]
            if A.x < B.x + B.w and B.x < A.x + A.w and A.y < B.y + B.h and B.y < A.y + A.h:
                overlaps += 1
    return {"crossings": crossings, "edges_through_tables": through, "table_overlaps": overlaps}
