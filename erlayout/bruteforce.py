"""Exhaustive reference optimiser for tiny instances.

Enumerates every layering into K columns (endpoints in different columns,
empty columns allowed) and every permutation of every column, and evaluates
the model's crossing count with crossings.count_crossings.  Shares no code
with the CP-SAT encoding; used to cross-check optimality claims."""
from __future__ import annotations

import itertools

from .crossings import count_crossings, expected_items
from .model import Schema


def row_orders(schema: Schema, pin_pk: bool = True):
    """Every admissible row order: the rows that carry lines are permuted
    among their own slots (primary key first when pinned)."""
    used = {}
    for e in schema.solver_edges():
        used.setdefault(e.src_table, set()).add(schema.table(e.src_table).row(e.src_col))
        used.setdefault(e.dst_table, set()).add(schema.table(e.dst_table).row(e.dst_col))
    per_table = []
    for t in schema.tables:
        slots = sorted(used.get(t.name, ()))
        names = [c.name for c in t.columns]
        pk = next((r for r, c in enumerate(t.columns) if c.pk), None)
        options = []
        for perm in itertools.permutations(slots):
            if pin_pk and pk in slots and perm[0] != pk:
                continue
            new = list(names)
            for slot, r in zip(slots, perm):
                new[slot] = names[r]
            options.append(new)
        per_table.append((t.name, options))
    for combo in itertools.product(*[opts for _, opts in per_table]):
        yield {name: order for (name, _), order in zip(per_table, combo)}


def brute_force(schema: Schema, k: int, limit: int = 5_000_000, reorder_rows: bool = False,
                pin_pk: bool = True) -> tuple[int, int]:
    """Returns (optimal crossings, number of drawings evaluated)."""
    if reorder_rows:
        best, total = None, 0
        for rows in row_orders(schema, pin_pk):
            c, ev = brute_force(schema.with_row_order(rows), k, limit - total)
            total += ev
            if best is None or c < best:
                best = c
                if best == 0:
                    break
        return best, total
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
