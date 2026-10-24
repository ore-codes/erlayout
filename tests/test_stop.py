import random
import threading
import time

from fastapi.testclient import TestClient
from test_solver import random_schema
from test_split import ddl, modules

from erlayout.api import solve, solve_split
from erlayout.solver import Stopper
from erlayout.web import app


def hard():
    return random_schema(random.Random(7), 16, 28)   # takes the full 30 s and more without a stop


def test_stop_ends_the_search_with_a_verified_layout():
    s, stopper = hard(), Stopper()
    threading.Timer(2.0, stopper.stop).start()
    t0 = time.time()
    res = solve(s, time_limit=120, workers=2, stopper=stopper)
    assert time.time() - t0 < 30
    assert res["stopped"] and res["crossings"] == res["verification"]["crossings"]
    if not res["global_optimal"]:
        assert "stopped early" in res["guarantee"] or res["status"] == "OPTIMAL"
    assert "too large" not in res["guarantee"]


def test_stopped_split_keeps_heuristic_groups():
    s, stopper = modules(), Stopper()
    stopper.stop()
    res = solve_split(s, time_limit=60, workers=2, max_group=6, stopper=stopper)
    assert res["stopped"] and res["seconds"] < 10
    assert {g["crossings"] for g in res["groups"]} and all(h["status"] == "HEURISTIC" for h in res["history"])
    assert "stopped early" in res["guarantee"]
    assert res["svg"].startswith("<svg")


def test_web_stop():
    client = TestClient(app)
    text = ddl(hard())
    jid = client.post("/api/jobs", json={"text": text, "time_limit": 120, "split": "off"}).json()["id"]
    time.sleep(2)
    assert client.post(f"/api/jobs/{jid}/stop").json() == {"stopping": True}
    t0 = time.time()
    while time.time() - t0 < 30:
        job = client.get(f"/api/jobs/{jid}").json()
        if job["done"]:
            break
        time.sleep(0.2)
    assert job["done"] and job["stopped"] and job["svg"].startswith("<svg") and not job.get("error")
    assert client.post("/api/jobs/nope/stop").status_code == 404


def test_cli_ctrl_c_writes_the_best_layout(tmp_path):
    import signal
    import subprocess
    import sys

    from test_split import ddl as to_sql
    src, out = tmp_path / "s.sql", tmp_path / "s.drawio"
    src.write_text(to_sql(hard()))
    p = subprocess.Popen([sys.executable, "-m", "erlayout.cli", "layout", str(src), "-o", str(out), "--time", "120"],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                         preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_DFL))
    time.sleep(3)
    p.send_signal(signal.SIGINT)
    text, _ = p.communicate(timeout=30)
    assert p.returncode == 0, text
    assert "stopped early" in text and out.read_text().startswith("<mxfile")
