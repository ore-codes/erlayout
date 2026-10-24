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
    rows = [(schema.table(e.src_table).row(e.src_col), schema.table(e.dst_table).row(e.dst_col)) for e in edges]

    def key(j, c):
        e = edges[j]
        if columns[e.src_table] == c:
            return pos[c][("T", e.src_table)] * R + rows[j][0] + 1
        if columns[e.dst_table] == c:
            return pos[c][("T", e.dst_table)] * R + rows[j][1] + 1
        return pos[c][("D", j)] * R

    spans = []
    for e in edges:
        a, b = sorted((columns[e.src_table], columns[e.dst_table]))
        spans.append((a, b))
    total = 0
    for g in range(len(orders) - 1):
        live = [j for j, (a, b) in enumerate(spans) if a <= g < b]
        total += _strict_inversions([(key(j, g), key(j, g + 1)) for j in live])
    return total


def _strict_inversions(pairs: list[tuple[int, int]]) -> int:
    """Number of pairs ordered strictly one way on the left and strictly the
    other way on the right: two segments in a gap cross exactly then (equal
    keys share an endpoint).  Fenwick tree over the right keys, O(n log n)."""
    ranks = {v: i + 1 for i, v in enumerate(sorted({r for _, r in pairs}))}
    tree = [0] * (len(ranks) + 1)
    seen = total = 0
    pairs = sorted(pairs)
    i = 0
    while i < len(pairs):
        j = i
        while j < len(pairs) and pairs[j][0] == pairs[i][0]:
            j += 1
        for _, r in pairs[i:j]:         # earlier groups lie strictly above on the left
            q, below = ranks[r], 0
            while q:
                below += tree[q]
                q -= q & -q
            total += seen - below       # ... and strictly below on the right
        for _, r in pairs[i:j]:
            q = ranks[r]
            while q < len(tree):
                tree[q] += 1
                q += q & -q
            seen += 1
        i = j
    return total


def total_span(schema: Schema, columns: dict[str, int]) -> int:
    return sum(abs(columns[e.src_table] - columns[e.dst_table]) for e in schema.solver_edges())
