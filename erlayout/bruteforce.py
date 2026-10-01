"""Exhaustive reference optimiser for tiny instances.

Enumerates every layering into K columns (endpoints in different columns,
empty columns allowed) and every permutation of every column, and evaluates
the model's crossing count with crossings.count_crossings.  Shares no code
with the CP-SAT encoding; used to cross-check optimality claims."""
from __future__ import annotations

import itertools

from .crossings import count_crossings, expected_items
from .model import Schema


def brute_force(schema: Schema, k: int, limit: int = 5_000_000) -> tuple[int, int]:
    """Returns (optimal crossings, number of drawings evaluated)."""
    names = [t.name for t in schema.tables]
    edges = schema.solver_edges()
    best, evaluated = None, 0
    for assign in itertools.product(range(k), repeat=len(names)):
        cols = dict(zip(names, assign))
        if any(cols[e.src_table] == cols[e.dst_table] for e in edges):
            continue
        need = expected_items(schema, cols, k)
        perms = [list(itertools.permutations(sorted(s, key=str))) for s in need]
        for combo in itertools.product(*perms):
            evaluated += 1
            if evaluated > limit:
                raise RuntimeError("instance too large for brute force")
            c = count_crossings(schema, cols, [list(o) for o in combo])
            if best is None or c < best:
                best = c
                if best == 0:
                    return 0, evaluated
    if best is None:
        raise RuntimeError("no feasible layering")
    return best, evaluated
