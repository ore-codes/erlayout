import json
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from erlayout.parse import parse_any, parse_dbml, parse_sql
from erlayout.web import app

ROOT = Path(__file__).resolve().parents[1]
SAKILA = (ROOT / "examples" / "sakila.sql").read_text()


def test_sql_inline_and_table_level_fks():
    s = parse_sql("""
      CREATE TABLE b (id INT PRIMARY KEY);
      CREATE TABLE c (cid INT, PRIMARY KEY (cid));
      CREATE TABLE a (id INT PRIMARY KEY, b_id INT REFERENCES b(id), c_ref INT,
                      CONSTRAINT fk FOREIGN KEY (c_ref) REFERENCES c);
      ALTER TABLE b ADD COLUMN extra INT;
    """)
    labels = sorted(e.label for e in s.edges)
    assert labels == ["a.b_id -> b.id", "a.c_ref -> c.cid"]
    assert s.table("a").columns[0].pk


def test_sql_alter_table_fk():
    s = parse_sql("""
      CREATE TABLE p (id INT PRIMARY KEY);
      CREATE TABLE q (id INT PRIMARY KEY, p_id INT);
      ALTER TABLE q ADD CONSTRAINT f FOREIGN KEY (p_id) REFERENCES p (id);
    """)
    assert [e.label for e in s.edges] == ["q.p_id -> p.id"]


def test_dbml():
    s = parse_dbml("""
Table users {
  id int [pk]
  name varchar
}
Table posts {
  id int [pk]
  user_id int [ref: > users.id]
  editor_id int
}
Ref: posts.editor_id > users.id
""")
    assert sorted(e.label for e in s.edges) == ["posts.editor_id -> users.id", "posts.user_id -> users.id"]
    assert parse_any("Table x {\n  id int\n}\n").tables[0].name == "x"


def test_json_roundtrip():
    s = parse_sql(SAKILA, "postgres")
    s2 = parse_any(json.dumps(s.to_dict()))
    assert [e.label for e in s2.edges] == [e.label for e in s.edges]


def test_sakila_example_parses():
    s = parse_sql(SAKILA, "postgres")
    assert len(s.tables) == 15 and len(s.edges) == 22


SMALL = """
CREATE TABLE a (id INT PRIMARY KEY, x INT, y INT);
CREATE TABLE b (id INT PRIMARY KEY, p INT, q INT);
CREATE TABLE c (id INT PRIMARY KEY, a_id INT REFERENCES a(id), b_id INT REFERENCES b(id));
ALTER TABLE a ADD CONSTRAINT f1 FOREIGN KEY (y) REFERENCES b (p);
ALTER TABLE a ADD CONSTRAINT f2 FOREIGN KEY (x) REFERENCES b (q);
"""


def test_web_api():
    client = TestClient(app)
    assert client.get("/").status_code == 200
    assert "CREATE TABLE" in client.get("/api/example").text
    r = client.post("/api/layout", json={"text": SMALL, "time_limit": 30})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "OPTIMAL"
    assert body["crossings"] == body["verification"]["crossings"] == body["verification"]["model_recount"]
    assert body["svg"].startswith("<svg")
    b = client.post("/api/baseline", json={"text": SMALL}).json()
    assert b["crossings"] >= body["crossings"]
    assert client.post("/api/layout", json={"text": "hello"}).status_code == 400


def test_cli(tmp_path):
    src = tmp_path / "s.sql"
    src.write_text(SMALL)
    out = tmp_path / "o.svg"
    js = tmp_path / "o.json"
    r = subprocess.run([sys.executable, "-m", "erlayout.cli", "layout", str(src), "-o", str(out),
                        "--json", str(js), "--time", "30", "--workers", "1"],
                       capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0, r.stderr
    assert "Proven optimal" in r.stdout
    assert out.read_text().startswith("<svg")
    assert json.loads(js.read_text())["status"] == "OPTIMAL"


def test_web_jobs_report_progress_then_result():
    import time
    client = TestClient(app)
    r = client.post("/api/jobs", json={"text": SMALL, "time_limit": 20})
    assert r.status_code == 200, r.text
    job_id = r.json()["id"]
    for _ in range(200):
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["done"]:
            break
        time.sleep(0.1)
    assert job["done"] and job.get("error") is None
    assert job["status"] == "OPTIMAL" and job["svg"].startswith("<svg")
    assert job["crossings"] == job["verification"]["crossings"]
    assert client.get("/api/jobs/nope").status_code == 404
    assert client.post("/api/jobs", json={"text": "hello"}).status_code == 400


TWIST_SQL = """
CREATE TABLE b (p INT, q INT);
CREATE TABLE a (x INT REFERENCES b(q), y INT REFERENCES b(p));
"""


def test_row_order_option():
    client = TestClient(app)
    keep = client.post("/api/layout", json={"text": TWIST_SQL, "time_limit": 20}).json()
    assert keep["crossings"] == 1 and keep["row_order"] == {} and not keep["rows_reordered"]
    free = client.post("/api/layout", json={"text": TWIST_SQL, "time_limit": 20, "rows": "reorder-free"}).json()
    assert free["crossings"] == 0 and free["row_order"] and free["rows_reordered"]
    assert free["verification"]["crossings"] == 0
    bad = client.post("/api/layout", json={"text": TWIST_SQL, "rows": "shuffle"})
    assert bad.status_code == 400


def test_cli_reorder_flag(tmp_path):
    src = tmp_path / "t.sql"
    src.write_text(TWIST_SQL)
    r = subprocess.run([sys.executable, "-m", "erlayout.cli", "layout", str(src), "-o", str(tmp_path / "o.svg"),
                        "--time", "20", "--workers", "1", "--reorder-rows", "--free-pk"],
                       capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0, r.stderr
    assert "0 crossings" in r.stdout and "reordered" in r.stdout
