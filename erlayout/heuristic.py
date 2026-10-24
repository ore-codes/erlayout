"""Baseline: the classic Sugiyama pipeline in the same drawing model.
Longest-path layering + iterated barycenter sweeps (with row-offset port
positions), keeping the best order seen.  Used as the comparison baseline
and as a warm-start hint for the exact solver."""
from __future__ import annotations

import random

from .crossings import count_crossings, total_span
from .model import Schema
from .solver import Layout


def longest_path_layering(schema: Schema) -> dict[str, int]:
    """Referenced tables go left of referencing ones (FK points left).
    Cycles are broken by ignoring back edges found by DFS."""
    edges = schema.solver_edges()
    names = [t.name for t in schema.tables]
    succ = {t: [] for t in names}          # referencing -> referenced
    for e in edges:
        succ[e.src_table].append(e.dst_table)
    # DFS to drop back edges
    state, keep = {}, {t: [] for t in names}

    def dfs(u):
        state[u] = 1
        for v in succ[u]:
            if state.get(v) == 1:
                continue                    # back edge
            keep[u].append(v)
            if v not in state:
                dfs(v)
        state[u] = 2
    for t in names:
        if t not in state:
            dfs(t)
    memo = {}

    def depth(u):
        if u not in memo:
            memo[u] = 0
            memo[u] = max([depth(v) + 1 for v in keep[u]] + [0])
        return memo[u]
    cols = {t: depth(t) for t in names}
    # endpoints must be in different columns; push violators right
    changed = True
    while changed:
        changed = False
        for e in edges:
            if cols[e.src_table] == cols[e.dst_table]:
                cols[e.src_table] += 1
                changed = True
    used = sorted(set(cols.values()))
    remap = {c: i for i, c in enumerate(range(min(used), max(used) + 1))}
    return {t: remap[c] for t, c in cols.items()}


def _initial_orders(schema: Schema, columns: dict[str, int]) -> list[list]:
    k = max(columns.values()) + 1
    orders = [[] for _ in range(k)]
    for t in schema.tables:
        orders[columns[t.name]].append(("T", t.name))
    for j, e in enumerate(schema.solver_edges()):
        a, b = sorted((columns[e.src_table], columns[e.dst_table]))
        for c in range(a + 1, b):
            orders[c].append(("D", j))
    return orders


def barycenter(schema: Schema, columns: dict[str, int], sweeps: int = 40,
               seed: int = 0) -> Layout:
    edges = schema.solver_edges()
    orders = _initial_orders(schema, columns)
    k = len(orders)
    rnd = random.Random(seed)
    nrows = {t.name: len(t.columns) for t in schema.tables}
    if seed:
        for o in orders:
            rnd.shuffle(o)

    # fractional row offset of each edge end, and the edges at each table
    frac = []
    incident = {t.name: [] for t in schema.tables}
    for j, e in enumerate(edges):
        rs = schema.table(e.src_table).row(e.src_col)
        rd = schema.table(e.dst_table).row(e.dst_col)
        frac.append(((rs + 1) / (nrows[e.src_table] + 1), (rd + 1) / (nrows[e.dst_table] + 1)))
        if columns[e.src_table] != columns[e.dst_table]:
            incident[e.src_table].append(j)
            incident[e.dst_table].append(j)

    def ypos(c, order):
        """fractional vertical coordinate of every edge end in column c."""
        pos = {it: p for p, it in enumerate(order)}
        out = {}
        for j, e in enumerate(edges):
            if columns[e.src_table] == c:
                out[j] = pos[("T", e.src_table)] + frac[j][0]
            elif columns[e.dst_table] == c:
                out[j] = pos[("T", e.dst_table)] + frac[j][1]
            elif ("D", j) in pos:
                out[j] = pos[("D", j)] + 0.5
        return out

    def item_edges(it, c):
        return [it[1]] if it[0] == "D" else incident[it[1]]

    def other_side(j, c, ref):
        e = edges[j]
        a, b = sorted((columns[e.src_table], columns[e.dst_table]))
        if ref < c and a <= ref:
            return True
        if ref > c and b >= ref:
            return True
        return False

    best = [list(o) for o in orders]
    best_c = count_crossings(schema, columns, best)
    for s in range(sweeps):
        rng = range(1, k) if s % 2 == 0 else range(k - 2, -1, -1)
        for c in rng:
            ref = c - 1 if s % 2 == 0 else c + 1
            ys = ypos(ref, orders[ref])
            keys = {}
            for p, it in enumerate(orders[c]):
                vals = [ys[j] for j in item_edges(it, c) if j in ys and other_side(j, c, ref)]
                keys[it] = sum(vals) / len(vals) if vals else p
            orders[c].sort(key=lambda it: (keys[it], rnd.random() * 1e-9))
        cur = count_crossings(schema, columns, orders)
        if cur < best_c:
            best_c, best = cur, [list(o) for o in orders]
    return Layout(dict(columns), best, best_c, total_span(schema, columns), "HEURISTIC", 0.0, 0.0, k)


def heuristic(schema: Schema, restarts: int = 8) -> Layout:
    cols = longest_path_layering(schema)
    best = None
    for r in range(restarts):
        lay = barycenter(schema, cols, seed=r)
        if best is None or lay.crossings < best.crossings:
            best = lay
    return best
