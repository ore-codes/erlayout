"""Parsers: SQL DDL (any dialect sqlglot understands), DBML, and JSON."""
from __future__ import annotations

import json
import re

import sqlglot
from sqlglot import exp

from .model import Column, Edge, Schema, Table


def _ident(node) -> str:
    if node is None:
        return ""
    if isinstance(node, exp.Identifier):
        return node.this
    if isinstance(node, exp.Table):
        return node.name
    return node.name if hasattr(node, "name") else str(node)


def _ref_target(ref: exp.Reference) -> tuple[str, list[str]]:
    sch = ref.this
    if isinstance(sch, exp.Schema):
        tbl = _ident(sch.this)
        cols = [_ident(c) for c in sch.expressions]
    else:
        tbl = _ident(sch)
        cols = []
    return tbl, cols


def parse_sql(text: str, dialect: str | None = None) -> Schema:
    """Parse CREATE TABLE / ALTER TABLE ... FOREIGN KEY statements.
    Referenced column defaults to the target's primary key (or `id`)."""
    tables: dict[str, Table] = {}
    raw_fks: list[tuple[str, str, str, str | None]] = []
    pks: dict[str, str] = {}

    for st in sqlglot.parse(text, read=dialect):
        if st is None:
            continue
        if isinstance(st, exp.Create) and st.args.get("kind", "").upper() == "TABLE":
            schema = st.this
            if not isinstance(schema, exp.Schema):
                continue
            tname = _ident(schema.this)
            t = Table(tname)
            for item in schema.expressions:
                if isinstance(item, exp.ColumnDef):
                    cname = _ident(item.this)
                    ctype = item.args.get("kind").sql() if item.args.get("kind") else ""
                    col = Column(cname, ctype.lower())
                    for cons in item.args.get("constraints") or []:
                        kind = cons.args.get("kind")
                        if isinstance(kind, exp.PrimaryKeyColumnConstraint):
                            col.pk = True
                            pks.setdefault(tname.lower(), cname)
                        elif isinstance(kind, exp.Reference):
                            dt, dcs = _ref_target(kind)
                            raw_fks.append((tname, cname, dt, dcs[0] if dcs else None))
                    t.columns.append(col)
                else:
                    for fk in item.find_all(exp.ForeignKey):
                        src_cols = [_ident(c) for c in fk.expressions]
                        ref = fk.args.get("reference")
                        if ref is None:
                            continue
                        dt, dcs = _ref_target(ref)
                        for i, sc in enumerate(src_cols):
                            raw_fks.append((tname, sc, dt, dcs[i] if i < len(dcs) else None))
                    for pk in item.find_all(exp.PrimaryKey):
                        for c in pk.expressions:
                            pks.setdefault(tname.lower(), _ident(c))
            tables[tname.lower()] = t
        elif isinstance(st, exp.Alter):
            tname = _ident(st.this)
            for pk in st.find_all(exp.PrimaryKey):
                for c in pk.expressions:
                    pks.setdefault(tname.lower(), _ident(c))
            for fk in st.find_all(exp.ForeignKey):
                ref = fk.args.get("reference")
                if ref is None:
                    continue
                dt, dcs = _ref_target(ref)
                for i, c in enumerate(fk.expressions):
                    raw_fks.append((tname, _ident(c), dt, dcs[i] if i < len(dcs) else None))

    for t in tables.values():
        pk = pks.get(t.name.lower())
        for c in t.columns:
            if pk and c.name.lower() == pk.lower():
                c.pk = True

    edges = []
    for st, sc, dt, dc in raw_fks:
        if dc is None:
            dc = pks.get(dt.lower())
        if dc is None:
            # no declared key: same-named column, then `id`, then the first column
            target = tables.get(dt.lower())
            names = [c.name.lower() for c in target.columns] if target else []
            dc = sc if sc.lower() in names else ("id" if "id" in names else (names[0] if names else "id"))
        edges.append(Edge(st, sc, dt, dc))
    for t in tables.values():
        pk = pks.get(t.name.lower())
        for c in t.columns:
            if pk and c.name.lower() == pk.lower():
                c.pk = True
    return Schema(list(tables.values()), edges).validate()


_DBML_TABLE = re.compile(r"Table\s+(\"?[\w.]+\"?)(?:\s+as\s+\w+)?\s*(?:\[[^\]]*\])?\s*\{(.*?)\n\s*\}", re.S | re.I)
_DBML_REF = re.compile(r"Ref(?:\s+\w+)?\s*:\s*\"?([\w]+)\"?\.\"?([\w]+)\"?\s*([<>-]|<>)\s*\"?([\w]+)\"?\.\"?([\w]+)\"?", re.I)
_INLINE_REF = re.compile(r"ref\s*:\s*([<>-]|<>)\s*\"?([\w]+)\"?\.\"?([\w]+)\"?", re.I)


def parse_dbml(text: str) -> Schema:
    tables: list[Table] = []
    edges: list[Edge] = []
    for m in _DBML_TABLE.finditer(text):
        tname = m.group(1).strip('"').split(".")[-1]
        t = Table(tname)
        for line in m.group(2).splitlines():
            line = line.strip()
            if not line or line.startswith("//") or line.lower().startswith(("indexes", "note")):
                continue
            if line in ("{", "}"):
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            cname = parts[0].strip('"')
            ctype = parts[1].split("[")[0]
            opts = line[line.find("["):] if "[" in line else ""
            col = Column(cname, ctype, pk=bool(re.search(r"\b(pk|primary key)\b", opts, re.I)))
            t.columns.append(col)
            im = _INLINE_REF.search(opts)
            if im:
                op, dt, dc = im.groups()
                if op == "<":
                    edges.append(Edge(dt, dc, tname, cname))
                else:
                    edges.append(Edge(tname, cname, dt, dc))
        tables.append(t)
    for m in _DBML_REF.finditer(text):
        a_t, a_c, op, b_t, b_c = m.groups()
        if op == "<":
            edges.append(Edge(b_t, b_c, a_t, a_c))
        else:
            edges.append(Edge(a_t, a_c, b_t, b_c))
    return Schema(tables, edges).validate()


def parse_any(text: str, fmt: str | None = None) -> Schema:
    """fmt in {sql, dbml, json}; guessed when None."""
    s = text.lstrip()
    if fmt is None:
        if s.startswith("{"):
            fmt = "json"
        elif re.search(r"^\s*Table\s+[\"\w.]+\s*(\[[^\]]*\])?\s*\{", text, re.M | re.I) and "create table" not in text.lower():
            fmt = "dbml"
        else:
            fmt = "sql"
    if fmt == "json":
        return Schema.from_dict(json.loads(text))
    if fmt == "dbml":
        return parse_dbml(text)
    return parse_sql(text)
