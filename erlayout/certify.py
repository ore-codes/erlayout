"""Global optimality certificates via sub-schemas (Lemma 3).

Restricting any drawing of a schema to a subset of its tables and the
edges among them gives a drawing of that sub-schema with no more crossings.
So if some sub-schema S' needs c crossings in every layered drawing (its
optimum solved at K = |S'|, which covers all column counts by Lemma 2),
the full schema needs at least c at every K.  When c equals the crossings
of a drawing we already have, that drawing is globally optimal.
"""
from __future__ import annotations

import time

from .model import Schema
from .solver import ExactSolver, Layout


def induced(schema: Schema, names) -> Schema:
    names = set(names)
    tabs = [t for t in schema.tables if t.name in names]
    edges = [e for e in schema.edges if e.src_table in names and e.dst_table in names]
    return Schema(tabs, edges).validate()


def crossing_pairs(schema: Schema, lay: Layout) -> list[tuple[int, int, int]]:
    edges = schema.solver_edges()
    cols = lay.columns
    pos = [{it: p for p, it in enumerate(o)} for o in lay.orders]
    R = max(len(t.columns) for t in schema.tables) + 2

    def key(j, c):
        e = edges[j]
        for t, col in ((e.src_table, e.src_col), (e.dst_table, e.dst_col)):
            if cols[t] == c:
                return pos[c][("T", t)] * R + schema.table(t).row(col) + 1
        return pos[c][("D", j)] * R

    out = []
    for g in range(len(lay.orders) - 1):
        live = [j for j, e in enumerate(edges)
                if min(cols[e.src_table], cols[e.dst_table]) <= g < max(cols[e.src_table], cols[e.dst_table])]
        for a in range(len(live)):
            for b in range(a + 1, len(live)):
                i, j = live[a], live[b]
                if (key(i, g) - key(j, g)) * (key(i, g + 1) - key(j, g + 1)) < 0:
                    out.append((g, i, j))
    return out


def certify(schema: Schema, lay: Layout, time_limit: float, workers: int = 8) -> dict | None:
    """Greedily grow a sub-schema from the tables touched by the layout's
    crossings until its global optimum reaches lay.crossings.  Returns the
    certificate or None if the time budget runs out first."""
    target = lay.crossings
    if target == 0:
        return None
    t0 = time.time()
    edges = schema.solver_edges()
    seed = set()
    for _, i, j in crossing_pairs(schema, lay):
        for e in (edges[i], edges[j]):
            seed.update((e.src_table, e.dst_table))
    attempts = []
    current = set(seed)
    while True:
        left = time_limit - (time.time() - t0)
        if left < 1:
            return None
        sub = induced(schema, current)
        k = len(sub.tables)
        try:
            res = ExactSolver(sub, k).solve(time_limit=left, workers=workers, phase2=False)
        except RuntimeError:
            res = None
        if res is not None:
            attempts.append({"tables": len(current), "status": res.status, "optimum": res.crossings,
                             "seconds": round(res.seconds, 2)})
            if res.status == "OPTIMAL" and res.crossings >= target:
                return {"tables": sorted(current), "forced_crossings": res.crossings,
                        "edges": sorted(e.label for e in sub.solver_edges()),
                        "attempts": attempts, "seconds": round(time.time() - t0, 2)}
            if res.status != "OPTIMAL":
                return None
        outside = [t.name for t in schema.tables if t.name not in current]
        if not outside:
            return None

        def score(name):
            into = sum(1 for e in edges if (e.src_table == name and e.dst_table in current)
                       or (e.dst_table == name and e.src_table in current))
            deg = sum(1 for e in edges if name in (e.src_table, e.dst_table))
            return (into, deg)
        current.add(max(outside, key=score))
