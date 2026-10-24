import time

from fastapi.testclient import TestClient

from erlayout.api import baseline_split, resolve_hubs, solve_split, wants_split
from erlayout.model import Column, Edge, Schema, Table
from erlayout.split import _communities, find_hubs, split_schema
from erlayout.web import app


def modules(n_mod=4, size=6, loose=3):
    """n_mod chains of `size` tables that all point at a hub `stores`, one link
    between consecutive modules, and some tables with no links at all."""
    T = [Table("stores", [Column("id", "bigint", True)])]
    E = []
    for m in range(n_mod):
        for i in range(size):
            cols = [Column("id", "bigint", True), Column("store_id", "bigint")]
            E.append(Edge(f"m{m}_t{i}", "store_id", "stores", "id"))
            if i:
                cols.append(Column("prev_id", "bigint"))
                E.append(Edge(f"m{m}_t{i}", "prev_id", f"m{m}_t{i - 1}", "id"))
            if m and i == 0:
                cols.append(Column("other_id", "bigint"))
                E.append(Edge(f"m{m}_t{i}", "other_id", f"m{m - 1}_t0", "id"))
            T.append(Table(f"m{m}_t{i}", cols))
    T += [Table(f"loose{i}", [Column("id", "bigint", True)]) for i in range(loose)]
    return Schema(T, E).validate()


def ddl(schema):
    out = []
    for t in schema.tables:
        cols = [f"{c.name} BIGINT" + (" PRIMARY KEY" if c.pk else "") for c in t.columns]
        cols += [f"FOREIGN KEY ({e.src_col}) REFERENCES {e.dst_table}({e.dst_col})"
                 for e in schema.edges if e.src_table == t.name]
        out.append(f"CREATE TABLE {t.name} ({', '.join(cols)});")
    return "\n".join(out)


def test_communities_respect_the_cap_and_cover_every_node():
    s = modules(n_mod=5, size=7, loose=0)
    nodes = [t.name for t in s.tables]
    for cap in (3, 7, 12):
        comms = _communities(nodes, s.solver_edges(), cap)
        assert all(len(g) <= cap for g in comms)
        assert sorted(t for g in comms for t in g) == sorted(nodes)


def test_hub_is_found_and_hidden_links_become_tags():
    s = modules()
    assert find_hubs(s) == ["stores"]
    sp = split_schema(s, max_group=8, hubs=["stores"])
    assert sp.hubs == ["stores"]
    drawn = [t.name for g in sp.groups for t in g.schema.tables]
    assert "stores" not in drawn
    assert {t.name for t in sp.loose} == {"stores", "loose0", "loose1", "loose2"}
    # every link is either drawn inside one group or tagged on its row
    inside = sum(len(g.schema.solver_edges()) for g in sp.groups)
    tagged = sum(note.count("→") for note in sp.notes.values())
    assert inside + tagged == len(s.solver_edges())
    t = next(t for g in sp.groups for t in g.schema.tables if t.name == "m0_t1")
    assert t.columns[1].type == "→ stores (hidden hub)"


def test_links_between_groups_name_the_other_group():
    sp = split_schema(modules(), max_group=6)
    group_of = {t.name: i + 1 for i, g in enumerate(sp.groups) for t in g.schema.tables}
    for (table, _), note in sp.notes.items():
        for part in note.split("; "):
            dst = part.split(" ")[1]
            assert part == f"→ {dst} (group {group_of[dst]})"
            assert group_of[dst] != group_of[table]


def test_resolve_hubs():
    s = modules()
    assert resolve_hubs(s, "") == []
    assert resolve_hubs(s, "auto") == ["stores"]
    assert resolve_hubs(s, " STORES , m0_t0") == ["stores", "m0_t0"]
    try:
        resolve_hubs(s, "nope")
    except ValueError as exc:
        assert "nope" in str(exc)
    else:
        raise AssertionError("unknown hub accepted")


def test_wants_split():
    small, big = modules(n_mod=1), modules(n_mod=8)
    assert not wants_split(small) and wants_split(big)
    assert wants_split(small, "on") and not wants_split(big, "off")
    assert wants_split(small, "off", ["stores"])      # hiding hubs needs the split drawing


def test_solve_split_reports_every_group_and_verifies():
    s = modules()
    events = []
    res = solve_split(s, time_limit=30, workers=2, max_group=8, hubs=["stores"], on_progress=events.append)
    assert res["split"] and res["global_optimal"]
    assert res["crossings"] == sum(g["crossings"] for g in res["groups"])
    assert res["verification"]["crossings"] == res["crossings"]
    assert res["verification"]["edges_through_tables"] == 0 and res["verification"]["table_overlaps"] == 0
    assert set(res["unlinked_tables"]) == {"stores", "loose0", "loose1", "loose2"}
    assert res["svg"].count("<svg") == len(res["groups"]) + 2   # outer, one per group, loose panel
    first = next(e for e in events if e["kind"] == "improved")
    assert first["svg"].startswith("<svg") and first["crossings"] >= res["crossings"]
    assert any(e["kind"] == "phase" and e["phase"] == "group" for e in events)
    stats, svg = baseline_split(s, max_group=8, hubs=["stores"])
    assert stats["crossings"] == res["baseline"]["crossings"] and svg.startswith("<svg")


def test_web_split_options():
    client = TestClient(app)
    text = ddl(modules())
    r = client.post("/api/layout", json={"text": text, "time_limit": 20, "split": "on",
                                         "max_group": 8, "hide_hubs": "auto"})
    assert r.status_code == 200, r.text
    assert r.json()["split"] and r.json()["hidden_hubs"] == ["stores"]
    assert client.post("/api/layout", json={"text": text, "hide_hubs": "nope"}).status_code == 400
    assert client.post("/api/layout", json={"text": text, "split": "maybe"}).status_code == 400
    assert client.post("/api/layout", json={"text": text, "max_group": 2}).status_code == 400
    b = client.post("/api/baseline", json={"text": text, "split": "on", "max_group": 8})
    assert b.status_code == 200 and b.json()["svg"].startswith("<svg")

    jid = client.post("/api/jobs", json={"text": text, "time_limit": 20, "split": "on", "max_group": 8}).json()["id"]
    for _ in range(200):
        job = client.get(f"/api/jobs/{jid}").json()
        if job["done"]:
            break
        time.sleep(0.1)
    assert job["done"] and not job.get("error"), job.get("error")
    assert job["split"] and job["crossings"] == sum(g["crossings"] for g in job["groups"])


def test_arrangements_do_not_overlap_and_grid_is_screen_shaped():
    from erlayout.render import cell_width, place
    sizes = [(300 + 97 * (i % 5), 200 + 131 * (i % 7)) for i in range(25)]
    titles = [f"Group {i + 1}: some_table and {i} more" for i in range(25)]
    shapes = {}
    for how in ("grid", "row", "column"):
        pos, W, H = place(sizes, titles, how)
        boxes = [(x, y - 34, x + cell_width(w, t), y + h) for (x, y), (w, h), t in zip(pos, sizes, titles)]
        for i, a in enumerate(boxes):
            assert a[2] <= W + 1e-6 and a[3] <= H + 1e-6
            for b in boxes[i + 1:]:
                assert a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1]
        shapes[how] = W / H
    assert shapes["column"] < 1 < shapes["row"]
    assert 1 <= shapes["grid"] <= 2.6


def test_split_arrange_option():
    s = modules()
    for how in ("grid", "row", "column"):
        res = solve_split(s, time_limit=10, workers=2, max_group=6, arrange=how)
        assert res["drawing"]["arrange"] == how
        xs = {round(sec["x"]) for sec in res["drawing"]["sections"]}
        ys = {round(sec["y"]) for sec in res["drawing"]["sections"]}
        if how == "column":
            assert xs == {0}
        if how == "row":
            assert len(ys) == 1
    client = TestClient(app)
    assert client.post("/api/baseline", json={"text": ddl(s), "split": "on", "arrange": "spiral"}).status_code == 400
