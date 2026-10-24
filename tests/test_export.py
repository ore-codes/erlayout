import io
import json
import time
import xml.etree.ElementTree as ET
import zipfile

from fastapi.testclient import TestClient
from test_split import ddl, modules

from erlayout.api import solve, solve_split
from erlayout.export import export, export_each, pick, to_drawio, to_excalidraw, to_mermaid
from erlayout.parse import parse_any, parse_dbml
from erlayout.web import app

# the kind of DBML dbdiagram.io writes, with most of the syntax it allows
DBDIAGRAM = """
Project shop {
  database_type: 'PostgreSQL'
  Note: '''
    # Shop
    Tables { and } in a note must not end anything.
  '''
}

enum shop.order_status {
  created [note: 'waiting']
  paid
}

TablePartial timestamps {
  created_at timestamp [default: `now()`]
  updated_by int [ref: > users.id]
}

Table users as U [headercolor: #3498DB, note: 'people'] {
  id integer [primary key, increment]
  "full name" varchar(255) [not null]
  country_code int
  Note: 'a user'
}

Table shop.orders [note: 'orders'] {
  id int [pk] // comment
  user_id int [not null, ref: > U.id]
  status shop.order_status
  total decimal(10, 2) [default: 0]
  ~timestamps
  indexes {
    (id, user_id) [unique]
    status [name: 'by_status']
  }
}

Table "order items" {
  order_id int
  product_id int
  quantity int [default: -1]
}

Table products {
  id int [pk]
  merchant_id int
  country_code int
  tags "text[]"
}

Table merchants {
  id int
  country_code int
  /* a block
     comment */
  indexes { (id, country_code) [pk] }
}

Ref: "order items".order_id > shop.orders.id [delete: cascade]
Ref product_fk { "order items".product_id > products.id }
Ref: products.(merchant_id, country_code) > merchants.(id, country_code)
Ref: merchants.id - users.id
Ref: users.country_code <> products.country_code

TableGroup sales [color: #ff0] {
  shop.orders
  "order items"
}

Note sticky {
  'remember the {braces}'
}
"""


def test_dbdiagram_dbml():
    s = parse_any(DBDIAGRAM)
    assert [t.name for t in s.tables] == ["users", "orders", "order items", "products", "merchants"]
    users, orders = s.table("users"), s.table("orders")
    assert [c.name for c in users.columns] == ["id", "full name", "country_code"]
    assert users.columns[0].pk and users.columns[1].type == "varchar(255)"
    # the partial's columns are added where ~timestamps stands; the index block is not a column
    assert [c.name for c in orders.columns] == ["id", "user_id", "status", "total", "created_at", "updated_by"]
    assert orders.columns[3].type == "decimal(10,2)"
    assert sorted(e.label for e in s.edges) == sorted([
        "orders.user_id -> users.id",
        "orders.updated_by -> users.id",
        "order items.order_id -> orders.id",
        "order items.product_id -> products.id",
        "products.merchant_id -> merchants.id",
        "products.country_code -> merchants.country_code",
        "merchants.id -> users.id",
        "users.country_code -> products.country_code",
    ])


def test_dbml_one_to_many_and_schema_clash():
    s = parse_dbml("""
Table a.t { id int [pk] }
Table b.t { id int [pk]
  a_id int }
Table x { id int [pk] }
Ref: x.id < b.t.a_id
""")
    assert [t.name for t in s.tables] == ["a.t", "b.t", "x"]
    assert [e.label for e in s.edges] == ["b.t.a_id -> x.id"]


def _check_drawio(xml: str, schema):
    root = ET.fromstring(xml)
    pages = root.findall("diagram")
    cells = [c for p in pages for c in p.iter("mxCell")]
    ids = {c.get("id") for c in cells}
    edges = [c for c in cells if c.get("edge") == "1"]
    for e in edges:
        assert e.get("source") in ids and e.get("target") in ids
    return pages, cells, edges


def test_drawio_and_excalidraw_keep_every_table_and_link():
    s = parse_any(DBDIAGRAM)
    res = solve(s, time_limit=20, workers=2)
    d = res["drawing"]
    pages, cells, edges = _check_drawio(to_drawio(d), s)
    assert len(pages) == 1 and len(edges) == len(s.solver_edges())
    names = {c.get("value") for c in cells if "shape=table;" in (c.get("style") or "")}
    assert names == {t.name for t in s.tables}

    ex = json.loads(to_excalidraw(d))
    els = {e["id"]: e for e in ex["elements"]}
    arrows = [e for e in ex["elements"] if e["type"] == "arrow"]
    assert len(arrows) == len(s.solver_edges())
    for a in arrows:
        for end in ("startBinding", "endBinding"):
            box = els[a[end]["elementId"]]
            assert {"id": a["id"], "type": "arrow"} in box["boundElements"]
        # the arrow starts and ends on the edges of the boxes it is bound to
        x0, y0 = a["x"], a["y"]
        box = els[a["startBinding"]["elementId"]]
        assert box["y"] <= y0 <= box["y"] + box["height"]
        assert abs(x0 - box["x"]) < 2 or abs(x0 - box["x"] - box["width"]) < 2
    texts = {e["text"] for e in ex["elements"] if e["type"] == "text"}
    assert {t.name for t in s.tables} <= texts


def test_mermaid():
    s = parse_any(DBDIAGRAM)
    mm = to_mermaid(s)
    assert mm.startswith("erDiagram\n")
    assert "    order_items {" in mm and "        varchar(255) full_name" in mm
    assert "        integer id PK" in mm and "        int user_id FK" in mm
    assert '    orders }o--|| users : "user_id"' in mm
    assert mm.count("}o--||") == len(s.edges)


def test_split_exports_one_page_per_group_and_single_groups():
    s = modules()
    res = solve_split(s, time_limit=20, workers=2, max_group=8, hubs=["stores"])
    d = res["drawing"]
    n = len(res["groups"]) + 1                   # plus the panel of tables without links
    assert len(d["sections"]) == n
    pages, _, edges = _check_drawio(export("drawio", d, s), s)
    assert len(pages) == n
    assert len(edges) == sum(g["edges"] for g in res["groups"])
    frames = [e for e in json.loads(export("excalidraw", d, s))["elements"] if e["type"] == "frame"]
    assert len(frames) == n

    one = pick(d, 1)
    assert len(one["sections"]) == 1 and one["sections"][0]["y"] == 0
    assert len(_check_drawio(export("drawio", one, s), s)[0]) == 1
    assert export("svg", one, s) == d["sections"][0]["svg"]
    group1 = {t["name"] for t in d["sections"][0]["tables"]}
    mm = export("mermaid", one, s)
    assert all(f"    {t} {{" in mm for t in group1)
    assert "%% referenced tables outside this diagram" in mm      # stores, and the next module

    z = zipfile.ZipFile(io.BytesIO(export_each("excalidraw", d, s)))
    assert len(z.namelist()) == n and z.namelist()[0].startswith("01-group-1")
    json.loads(z.read(z.namelist()[0]))


def test_web_export_endpoint():
    client = TestClient(app)
    jid = client.post("/api/jobs", json={"text": ddl(modules()), "time_limit": 15, "split": "on",
                                         "max_group": 8}).json()["id"]
    for _ in range(200):
        job = client.get(f"/api/jobs/{jid}").json()
        if job["done"]:
            break
        time.sleep(0.1)
    assert "_drawing" not in job and "_schema" not in job and len(job["diagrams"]) > 1
    r = client.get(f"/api/jobs/{jid}/export/drawio")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    assert len(ET.fromstring(r.text).findall("diagram")) == len(job["diagrams"])
    r = client.get(f"/api/jobs/{jid}/export/mermaid?group=2")
    assert r.status_code == 200 and r.text.startswith("erDiagram")
    r = client.get(f"/api/jobs/{jid}/export/svg?group=each")
    assert r.headers["content-type"] == "application/zip"
    assert len(zipfile.ZipFile(io.BytesIO(r.content)).namelist()) == len(job["diagrams"])
    assert client.get(f"/api/jobs/{jid}/export/png").status_code == 400
    assert client.get(f"/api/jobs/{jid}/export/svg?group=99").status_code == 400
    assert client.get("/api/jobs/nope/export/svg").status_code == 404
