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


def lower_bound(schema: Schema, lay: Layout, time_limit: float, workers: int = 8,
                attempt_cap: float | None = None, should_stop=None,
                reorder_rows: bool = False, pin_pk: bool = True) -> dict:
    """Grow a sub-schema from the tables touched by the layout's crossings,
    adding the table with the most foreign keys into the set each step, and
    solve each sub-schema at K = its size.  Every proven optimum is a lower
    bound for the whole schema (Lemma 3).  Stops when the bound reaches
    lay.crossings, when an attempt cannot be proven within attempt_cap
    (larger ones would be harder still), or when time runs out.
    Returns {"bound", "tables", "edges", "complete", "attempts", "seconds"}."""
    target = lay.crossings
    t0 = time.time()
    out = {"bound": 0, "tables": [], "edges": [], "complete": target == 0, "attempts": [], "seconds": 0.0}
    if target == 0:
        return out
    edges = schema.solver_edges()
    current = set()
    for _, i, j in crossing_pairs(schema.with_row_order(lay.rows), lay):
        for e in (edges[i], edges[j]):
            current.update((e.src_table, e.dst_table))
    while True:
        left = time_limit - (time.time() - t0)
        if left < 1 or (should_stop and should_stop()):
            break
        sub = induced(schema, current)
        cap = min(left, attempt_cap) if attempt_cap else left
        try:
            res = ExactSolver(sub, len(sub.tables), reorder_rows=reorder_rows, pin_pk=pin_pk).solve(
                time_limit=cap, workers=workers, phase2=False)
        except RuntimeError:
            res = None
        if res is None:
            break
        out["attempts"].append({"tables": len(current), "status": res.status, "optimum": res.crossings,
                                "seconds": round(res.seconds, 2)})
        if res.status != "OPTIMAL":
            break
        if res.crossings > out["bound"]:
            out.update(bound=res.crossings, tables=sorted(current),
                       edges=sorted(e.label for e in sub.solver_edges()))
        if res.crossings >= target:
            out["complete"] = True
            break
        outside = [t.name for t in schema.tables if t.name not in current]
        if not outside:
            break

        def score(name):
            into = sum(1 for e in edges if (e.src_table == name and e.dst_table in current)
                       or (e.dst_table == name and e.src_table in current))
            deg = sum(1 for e in edges if name in (e.src_table, e.dst_table))
            return (into, deg)
        current.add(max(outside, key=score))
    out["seconds"] = round(time.time() - t0, 2)
    return out


def certify(schema: Schema, lay: Layout, time_limit: float, workers: int = 8) -> dict | None:
    """Certificate that lay is globally optimal, or None."""
    lb = lower_bound(schema, lay, time_limit, workers)
    if lay.crossings == 0 or not lb["complete"]:
        return None
    return {"tables": lb["tables"], "forced_crossings": lb["bound"], "edges": lb["edges"],
            "attempts": lb["attempts"], "seconds": lb["seconds"]}
