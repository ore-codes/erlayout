"""Schema graph model: tables with ordered columns, and foreign-key edges
anchored at specific column rows."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Column:
    name: str
    type: str = ""
    pk: bool = False


@dataclass
class Table:
    name: str
    columns: list[Column] = field(default_factory=list)

    def row(self, col: str) -> int:
        """0-based row index of a column (case-insensitive)."""
        lc = col.lower()
        for i, c in enumerate(self.columns):
            if c.name.lower() == lc:
                return i
        raise KeyError(f"{self.name}.{col}")


@dataclass(frozen=True)
class Edge:
    """A foreign key: src_table.src_col references dst_table.dst_col."""
    src_table: str
    src_col: str
    dst_table: str
    dst_col: str

    @property
    def label(self) -> str:
        return f"{self.src_table}.{self.src_col} -> {self.dst_table}.{self.dst_col}"


@dataclass
class Schema:
    tables: list[Table] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)

    def table(self, name: str) -> Table:
        ln = name.lower()
        for t in self.tables:
            if t.name.lower() == ln:
                return t
        raise KeyError(name)

    def validate(self) -> "Schema":
        """Normalise names to the declared casing and drop edges whose
        endpoints do not exist. Self-loops are kept in `edges` but the
        solver ignores them (they cannot cross anything in the model)."""
        names = {t.name.lower(): t for t in self.tables}
        if len(names) != len(self.tables):
            raise ValueError("duplicate table names")
        good: list[Edge] = []
        seen = set()
        for e in self.edges:
            s = names.get(e.src_table.lower())
            d = names.get(e.dst_table.lower())
            if s is None or d is None:
                continue
            try:
                sc = s.columns[s.row(e.src_col)].name
                dc = d.columns[d.row(e.dst_col)].name
            except KeyError:
                continue
            ne = Edge(s.name, sc, d.name, dc)
            if ne not in seen:
                seen.add(ne)
                good.append(ne)
        self.edges = good
        return self

    def solver_edges(self) -> list[Edge]:
        """Edges that are drawn between two tables.  Self-references are
        excluded, and FKs joining the same two rows (e.g. A.id->B.id and
        B.id->A.id) are drawn once, since they would be the same curve."""
        out, seen = [], set()
        for e in self.edges:
            if e.src_table == e.dst_table:
                continue
            key = frozenset([(e.src_table, e.src_col.lower()), (e.dst_table, e.dst_col.lower())])
            if key in seen:
                continue
            seen.add(key)
            out.append(e)
        return out

    def with_row_order(self, rows: dict | None) -> "Schema":
        """Copy of the schema whose tables list their columns in the given order
        (table name -> column names).  Tables not in `rows` keep their order."""
        if not rows:
            return self
        tables = []
        for t in self.tables:
            order = rows.get(t.name)
            if order:
                by = {c.name: c for c in t.columns}
                tables.append(Table(t.name, [by[n] for n in order]))
            else:
                tables.append(t)
        return Schema(tables, list(self.edges))

    def to_dict(self) -> dict:
        return {
            "tables": [
                {"name": t.name,
                 "columns": [{"name": c.name, "type": c.type, "pk": c.pk} for c in t.columns]}
                for t in self.tables
            ],
            "edges": [
                {"from": f"{e.src_table}.{e.src_col}", "to": f"{e.dst_table}.{e.dst_col}"}
                for e in self.edges
            ],
        }

    @staticmethod
    def from_dict(d: dict) -> "Schema":
        tables = [
            Table(t["name"], [Column(c["name"], c.get("type", ""), c.get("pk", False))
                              for c in t["columns"]])
            for t in d["tables"]
        ]
        edges = []
        for e in d.get("edges", []):
            st, sc = e["from"].split(".", 1)
            dt, dc = e["to"].split(".", 1)
            edges.append(Edge(st, sc, dt, dc))
        return Schema(tables, edges).validate()
