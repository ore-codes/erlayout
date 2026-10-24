"""Splitting a large schema into diagrams that can each be read (and solved).

* Groups: tables are clustered by greedy modularity merging with a size cap,
  so tables that reference each other a lot end up in the same diagram.
* Links between groups are not drawn; the foreign-key row is tagged with its
  target instead ("-> orders (group 2)").
* Hub tables can be hidden: they leave the drawing and every link to them
  becomes a tag, which removes the fans of lines a table like `stores` makes.
* Tables with no drawn link go into one compact panel instead of the columns.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field

from .model import Column, Edge, Schema, Table


@dataclass
class Group:
    title: str
    schema: Schema


@dataclass
class Split:
    groups: list[Group]
    loose: list[Table]                        # tables with no drawn link
    hubs: list[str]                           # hidden hub tables
    notes: dict[tuple[str, str], str] = field(default_factory=dict)  # (table, column) -> tag


def find_hubs(schema: Schema) -> list[str]:
    """Tables referenced by many foreign keys: at least 10, and at least 5% of
    all links."""
    indeg = Counter(e.dst_table for e in schema.solver_edges())
    need = max(10, 0.05 * len(schema.solver_edges()))
    return sorted((t for t, d in indeg.items() if d >= need), key=lambda t: -indeg[t])


def _communities(nodes: list[str], edges: list[Edge], cap: int) -> list[list[str]]:
    """Greedy modularity agglomeration (Clauset-Newman-Moore) that never merges
    past `cap` tables.  Deterministic: ties go to the lexicographically first pair."""
    m = len(edges)
    if m == 0:
        return [[v] for v in nodes]
    w = defaultdict(lambda: defaultdict(float))     # community -> neighbour -> edge fraction
    deg = defaultdict(float)
    for e in edges:
        a, b = e.src_table, e.dst_table
        w[a][b] += 1 / (2 * m)
        w[b][a] += 1 / (2 * m)
        deg[a] += 1 / (2 * m)
        deg[b] += 1 / (2 * m)
    members = {v: [v] for v in nodes}
    while True:
        best = None
        for a in sorted(w):
            for b, eab in w[a].items():
                if a < b and len(members[a]) + len(members[b]) <= cap:
                    dq = 2 * (eab - deg[a] * deg[b])
                    if dq > 1e-12 and (best is None or dq > best[0]):
                        best = (dq, a, b)
        if best is None:
            break
        _, a, b = best                               # merge b into a
        members[a] += members.pop(b)
        for c, ebc in w.pop(b).items():
            if c == a:
                continue
            w[a][c] += ebc
            w[c][a] += ebc
            del w[c][b]
        w[a].pop(b, None)
        deg[a] += deg.pop(b)
    return sorted((sorted(g) for g in members.values()), key=lambda g: (-len(g), g[0]))


def split_schema(schema: Schema, max_group: int = 15, hubs: list[str] | None = None) -> Split:
    hub_set = {h.lower() for h in hubs or []}
    hidden = [t.name for t in schema.tables if t.name.lower() in hub_set]
    kept = [t for t in schema.tables if t.name.lower() not in hub_set]
    kept_names = {t.name for t in kept}
    drawable = [e for e in schema.solver_edges() if e.src_table in kept_names and e.dst_table in kept_names]

    linked = {t for e in drawable for t in (e.src_table, e.dst_table)}
    comms = _communities(sorted(linked), drawable, max_group)
    group_of = {t: i for i, g in enumerate(comms) for t in g}

    notes: dict[tuple[str, str], str] = {}
    by_group: list[list[Edge]] = [[] for _ in comms]
    for e in schema.solver_edges():
        gs, gd = group_of.get(e.src_table), group_of.get(e.dst_table)
        if gs is not None and gs == gd:
            by_group[gs].append(e)
            continue
        if e.dst_table in hidden:
            where = "hidden hub"
        elif gd is None:
            where = "unlinked tables"
        else:
            where = f"group {gd + 1}"
        key = (e.src_table, e.src_col)
        tag = f"→ {e.dst_table} ({where})"
        notes[key] = tag if key not in notes else notes[key] + "; " + tag

    def tagged(t: Table) -> Table:
        """Copy of t with each tagged row showing its tag in place of the type."""
        if not any((t.name, c.name) in notes for c in t.columns):
            return t
        return Table(t.name, [Column(c.name, notes.get((t.name, c.name), c.type), c.pk) for c in t.columns])

    tables = {t.name: tagged(t) for t in schema.tables}
    degree = Counter(t for e in drawable for t in (e.src_table, e.dst_table))
    groups = []
    for i, (g, es) in enumerate(zip(comms, by_group)):
        lead = max(g, key=lambda t: (degree[t], -len(t)))
        title = f"Group {i + 1}: {lead}" + (f" and {len(g) - 1} more" if len(g) > 1 else "")
        groups.append(Group(title, Schema([tables[t] for t in g], es)))
    loose = [tables[t] for t in hidden] + [t for t in kept if t.name not in linked]
    return Split(groups, loose, hidden, notes)
