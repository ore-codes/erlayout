"""Combinatorial crossing count straight from the model definition.
Independent of the CP-SAT encoding; used to validate solver output, by the
brute-force enumerator and by the heuristic baseline."""
from __future__ import annotations

from .model import Schema


def expected_items(schema: Schema, columns: dict[str, int], k: int) -> list[set]:
    edges = schema.solver_edges()
    need = [set() for _ in range(k)]
    for t, c in columns.items():
        need[c].add(("T", t))
    for j, e in enumerate(edges):
        a, b = sorted((columns[e.src_table], columns[e.dst_table]))
        for c in range(a + 1, b):
            need[c].add(("D", j))
    return need


def check_layout(schema: Schema, columns: dict[str, int], orders: list[list]) -> None:
    edges = schema.solver_edges()
    for e in edges:
        if columns[e.src_table] == columns[e.dst_table]:
            raise ValueError(f"edge within one column: {e.label}")
    need = expected_items(schema, columns, len(orders))
    for c, order in enumerate(orders):
        if set(order) != need[c] or len(order) != len(set(order)):
            raise ValueError(f"column {c} items mismatch")


def count_crossings(schema: Schema, columns: dict[str, int], orders: list[list]) -> int:
    check_layout(schema, columns, orders)
    edges = schema.solver_edges()
    pos = [{it: p for p, it in enumerate(order)} for order in orders]
    R = max(len(t.columns) for t in schema.tables) + 2

    def key(j, c):
        e = edges[j]
        if columns[e.src_table] == c:
            return pos[c][("T", e.src_table)] * R + schema.table(e.src_table).row(e.src_col) + 1
        if columns[e.dst_table] == c:
            return pos[c][("T", e.dst_table)] * R + schema.table(e.dst_table).row(e.dst_col) + 1
        return pos[c][("D", j)] * R

    spans = []
    for e in edges:
        a, b = sorted((columns[e.src_table], columns[e.dst_table]))
        spans.append((a, b))
    total = 0
    for g in range(len(orders) - 1):
        live = [j for j, (a, b) in enumerate(spans) if a <= g < b]
        for x in range(len(live)):
            for y in range(x + 1, len(live)):
                j, k2 = live[x], live[y]
                l0, l1 = key(j, g) - key(k2, g), key(j, g + 1) - key(k2, g + 1)
                if l0 * l1 < 0:
                    total += 1
    return total


def total_span(schema: Schema, columns: dict[str, int]) -> int:
    return sum(abs(columns[e.src_table] - columns[e.dst_table]) for e in schema.solver_edges())
