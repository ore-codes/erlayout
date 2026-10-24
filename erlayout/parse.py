"""Parsers: SQL DDL (any dialect sqlglot understands), DBML, and JSON."""
from __future__ import annotations

import json
import re
from collections import Counter

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


def _parse_statements(text: str, dialect: str | None):
    """sqlglot statements for `text`. Without a dialect, try the likely ones in
    turn: sqlglot's generic dialect does not treat backticks as identifier
    quotes, so a MySQL dump with a column named `key` fails there."""
    if dialect is not None:
        return sqlglot.parse(text, read=dialect)
    candidates = ["mysql", None, "postgres"] if "`" in text else [None, "postgres", "mysql", "tsql"]
    first_error = None
    for d in candidates:
        try:
            return sqlglot.parse(text, read=d)
        except sqlglot.errors.ParseError as exc:
            first_error = first_error or exc
    raise first_error


def parse_sql(text: str, dialect: str | None = None) -> Schema:
    """Parse CREATE TABLE / ALTER TABLE ... FOREIGN KEY statements.
    Referenced column defaults to the target's primary key (or `id`)."""
    tables: dict[str, Table] = {}
    raw_fks: list[tuple[str, str, str, str | None]] = []
    pks: dict[str, str] = {}

    for st in _parse_statements(text, dialect):
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


# ---------------------------------------------------------------- DBML
# A small tokenizer and parser for the DBML that dbdiagram.io writes: tables
# with aliases, schema-qualified and quoted names, settings, notes and index
# blocks; inline, short and long refs, composite refs and TablePartials.
# Enums, TableGroups, Project and sticky notes carry nothing a layout needs and
# are skipped.
_DBML_TOKEN = re.compile(r"""
    (?P<ws>[ \t\r\f]+) | (?P<nl>\n) | (?P<comment>//[^\n]*|/\*.*?\*/)
  | (?P<str3>'{3}.*?'{3}) | (?P<str>'(?:\\.|[^'\\])*') | (?P<qname>"(?:\\.|[^"\\])*")
  | (?P<expr>`[^`]*`) | (?P<op><>) | (?P<word>[^\W]+) | (?P<punct>\S)
""", re.S | re.X)


def _dbml_tokens(text: str) -> list[tuple[str, str]]:
    out = []
    for m in _DBML_TOKEN.finditer(text):
        kind = m.lastgroup
        if kind in ("ws", "comment"):
            continue
        v = m.group()
        if kind == "qname":
            kind, v = "word", v[1:-1]
        out.append((kind, v))
    return out


class _Dbml:
    def __init__(self, text: str):
        self.t = _dbml_tokens(text)
        self.i = 0

    def peek(self, k=0):
        j = self.i + k
        return self.t[j] if j < len(self.t) else ("eof", "")

    def next(self):
        tok = self.peek()
        self.i += 1
        return tok

    def skip_nl(self):
        while self.peek()[0] == "nl":
            self.i += 1

    def is_(self, v, k=0):
        return self.peek(k)[1] == v and self.peek(k)[0] in ("punct", "op")

    def word(self, v, k=0):
        return self.peek(k)[0] == "word" and self.peek(k)[1].lower() == v

    def expect(self, v):
        self.skip_nl()
        kind, got = self.next()
        if got != v:
            raise ValueError(f"DBML: expected {v!r}, found {got!r}")

    def balanced(self, open_, close):
        """Tokens inside a balanced open_ ... close, the brackets consumed."""
        self.expect(open_)
        depth, out = 1, []
        while True:
            tok = self.next()
            if tok[0] == "eof":
                raise ValueError(f"DBML: missing {close!r}")
            if tok[1] == open_ and tok[0] == "punct":
                depth += 1
            elif tok[1] == close and tok[0] == "punct":
                depth -= 1
                if depth == 0:
                    return out
            out.append(tok)

    def name(self) -> list[str]:
        """A possibly qualified name: a.b."c d" -> ['a', 'b', 'c d']."""
        self.skip_nl()
        kind, v = self.next()
        if kind != "word":
            raise ValueError(f"DBML: expected a name, found {v!r}")
        parts = [v]
        while self.is_(".") and self.peek(1)[0] == "word":
            self.i += 1
            parts.append(self.next()[1])
        return parts

    def endpoint(self) -> tuple[list[str], list[str]]:
        """table.column or table.(col1, col2), the table possibly schema-qualified."""
        parts = self.name()
        if self.is_(".") and self.is_("(", 1):
            self.i += 1
            cols = [v for k, v in self.balanced("(", ")") if k == "word"]
            return parts, cols
        if len(parts) < 2:
            raise ValueError(f"DBML: expected table.column, found {'.'.join(parts)!r}")
        return parts[:-1], parts[-1:]


def _split_commas(tokens):
    parts, cur, depth = [], [], 0
    for tok in tokens:
        if tok[1] in "([" and tok[0] == "punct":
            depth += 1
        elif tok[1] in ")]" and tok[0] == "punct":
            depth -= 1
        if tok[1] == "," and tok[0] == "punct" and depth == 0:
            parts.append(cur)
            cur = []
        elif tok[0] != "nl":
            cur.append(tok)
    parts.append(cur)
    return [p for p in parts if p]


def parse_dbml(text: str) -> Schema:
    """Parse DBML (https://dbml.dbdiagram.io).  Relationship operators:
    a.x > b.y and b.y < a.x mean a.x references b.y; one-to-one (-) and
    many-to-many (<>) links are drawn from the left side to the right."""
    P = _Dbml(text)
    tables: list[tuple[list[str], str | None, list]] = []    # (qualified name, alias, items)
    partials: dict[str, list] = {}
    refs: list[tuple] = []          # (src table parts, src columns, dst table parts, dst columns)

    def rel(a, op, b):
        refs.append((*b, *a) if op == "<" else (*a, *b))

    def body() -> list:
        """Columns of a Table or TablePartial: ('col', Column, inline refs) or ('partial', name)."""
        items = []
        P.expect("{")
        while True:
            P.skip_nl()
            kind, v = P.peek()
            if kind == "eof":
                raise ValueError("DBML: missing '}'")
            if P.is_("}"):
                P.i += 1
                return items
            if P.is_("~"):
                P.i += 1
                items.append(("partial", P.next()[1].lower()))
                continue
            if kind == "word" and P.is_("{", 1):         # indexes { }, checks { }, Note { }
                P.i += 1
                P.balanced("{", "}")
                continue
            if kind == "word" and v.lower() == "note" and P.is_(":", 1):
                P.i += 2
                P.next()
                continue
            # a column: name, type up to the settings or the end of the line
            cname = P.next()[1]
            type_parts = []
            while P.peek()[0] not in ("nl", "eof") and not P.is_("}"):
                if P.is_("[") and not P.is_("]", 1):
                    break
                type_parts.append(P.next()[1])
            ctype = "".join(type_parts) if all(re.fullmatch(r"[\w.]+|[()\[\],]", x) for x in type_parts) \
                else " ".join(type_parts)
            settings = _split_commas(P.balanced("[", "]")) if P.is_("[") else []
            col = Column(cname, ctype.lower())
            inline = []
            for st in settings:
                words = [v.lower() for k, v in st if k == "word"]
                if words[:1] == ["pk"] or words[:2] == ["primary", "key"]:
                    col.pk = True
                elif words[:1] == ["ref"] and len(st) > 2 and st[1][1] == ":":
                    op = st[2][1]
                    sub = _Dbml("")
                    sub.t = st[3:]
                    inline.append((op, sub.endpoint()))
            items.append(("col", col, inline))

    while True:
        P.skip_nl()
        kind, v = P.peek()
        if kind == "eof":
            break
        low = v.lower() if kind == "word" else ""
        if low in ("table", "tablepartial"):
            P.i += 1
            qname = P.name()
            alias = None
            if P.word("as"):
                P.i += 1
                alias = P.next()[1]
            if P.is_("["):
                P.balanced("[", "]")
            items = body()
            if low == "table":
                tables.append((qname, alias, items))
            else:
                partials[qname[-1].lower()] = items
        elif low == "ref":
            P.i += 1
            if P.peek()[0] == "word":                   # optional ref name
                P.i += 1
            if P.is_(":"):
                P.i += 1
                a = P.endpoint()
                op = P.next()[1]
                rel(a, op, P.endpoint())
                if P.is_("["):
                    P.balanced("[", "]")
            else:
                inner = _Dbml("")
                inner.t = P.balanced("{", "}")
                while True:
                    inner.skip_nl()
                    if inner.peek()[0] == "eof":
                        break
                    a = inner.endpoint()
                    op = inner.next()[1]
                    rel(a, op, inner.endpoint())
                    if inner.is_("["):
                        inner.balanced("[", "]")
        else:
            # Project, Enum, TableGroup, Note, Records ... : skip to the end of the block
            while not P.is_("{") and P.peek()[0] != "eof":
                P.i += 1
            if P.peek()[0] != "eof":
                P.balanced("{", "}")

    # short names unless two schemas share one; aliases and schema-qualified refs resolve to them
    short = Counter(q[-1].lower() for q, _, _ in tables)
    lookup: dict[str, str] = {}
    out_tables: list[Table] = []
    for qname, alias, items in tables:
        tname = qname[-1] if short[qname[-1].lower()] == 1 else ".".join(qname)
        for key in {".".join(qname).lower(), tname.lower(), "public." + qname[-1].lower()} | (
                {alias.lower()} if alias else set()):
            lookup.setdefault(key, tname)
        if len(qname) == 1:
            lookup.setdefault(qname[0].lower(), tname)
        t = Table(tname)
        own = {it[1].name.lower() for it in items if it[0] == "col"}
        expanded = []
        for it in items:
            if it[0] == "partial":
                expanded += [x for x in partials.get(it[1], []) if x[0] == "col" and x[1].name.lower() not in own]
            else:
                expanded.append(it)
        for _, col, inline in expanded:
            t.columns.append(Column(col.name, col.type, col.pk))
            for op, b in inline:
                rel(([tname], [col.name]), op, b)
        out_tables.append(t)

    edges = []
    for a_t, a_c, b_t, b_c in refs:
        st = lookup.get(".".join(a_t).lower(), a_t[-1])
        dt = lookup.get(".".join(b_t).lower(), b_t[-1])
        for sc, dc in zip(a_c, b_c):
            edges.append(Edge(st, sc, dt, dc))
    return Schema(out_tables, edges).validate()


_DBML_START = re.compile(r"^\s*(Table|TablePartial)\s+[\"\w.]+[\"\w. ]*?(\s+as\s+\w+)?\s*(\[[^\]]*\])?\s*\{",
                         re.M | re.I)


def parse_any(text: str, fmt: str | None = None) -> Schema:
    """fmt in {sql, dbml, json}; guessed when None."""
    s = text.lstrip()
    if fmt is None:
        if s.startswith("{"):
            fmt = "json"
        elif _DBML_START.search(text) and "create table" not in text.lower():
            fmt = "dbml"
        else:
            fmt = "sql"
    if fmt == "json":
        return Schema.from_dict(json.loads(text))
    if fmt == "dbml":
        return parse_dbml(text)
    return parse_sql(text)
