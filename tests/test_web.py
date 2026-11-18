import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from erlayout import web
from erlayout.solver import Stopper

SCHEMA = """
CREATE TABLE a (id INT PRIMARY KEY);
CREATE TABLE b (id INT PRIMARY KEY, a_id INT, FOREIGN KEY (a_id) REFERENCES a(id));
CREATE TABLE c (id INT PRIMARY KEY, a_id INT, b_id INT,
  FOREIGN KEY (a_id) REFERENCES a(id), FOREIGN KEY (b_id) REFERENCES b(id));
"""
SAKILA = (Path(__file__).parent.parent / "examples" / "sakila.sql").read_text()


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    web.JOBS.clear()
    web._CACHE.clear()
    monkeypatch.setattr(web, "_running", 0)
    yield
    web.JOBS.clear()
    web._CACHE.clear()


def wait(client, job_id, seconds=60):
    end = time.time() + seconds
    while time.time() < end:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["done"]:
            return job
        time.sleep(0.2)
    raise AssertionError("job did not finish")


def run(client, text, **body):
    r = client.post("/api/jobs", json={"text": text, "time_limit": 30, **body})
    assert r.status_code == 200, r.text
    return wait(client, r.json()["id"])


def test_identical_request_is_answered_from_the_cache(monkeypatch):
    c = TestClient(web.app)
    first = run(c, SCHEMA)
    assert first["status"] == "OPTIMAL" and not first.get("cached")
    monkeypatch.setattr(web, "MAX_RUNNING", 0)          # a real solve would now be refused
    second = run(c, SCHEMA)
    assert second["cached"] and second["svg"] == first["svg"] and second["guarantee"] == first["guarantee"]
    assert c.get(f"/api/jobs/{second['id']}/export/drawio").status_code == 200
    assert c.post("/api/jobs", json={"text": SCHEMA, "time_limit": 31}).status_code == 429   # other budget: no hit
    assert c.post("/api/jobs", json={"text": SCHEMA, "time_limit": 30, "rows": "reorder"}).status_code == 429


def test_cache_keeps_only_the_most_recent_requests(monkeypatch):
    monkeypatch.setattr(web, "MAX_CACHE", 2)
    c = TestClient(web.app)
    for t in (10, 11, 12):
        run(c, SCHEMA, time_limit=t)
    assert len(web._CACHE) == 2


def test_stopped_search_is_not_cached():
    c = TestClient(web.app)
    job_id = c.post("/api/jobs", json={"text": SAKILA, "time_limit": 30}).json()["id"]
    c.post(f"/api/jobs/{job_id}/stop")
    job = wait(c, job_id)
    assert job["stopped"] and job["status"] != "OPTIMAL"
    assert not web._CACHE


def test_full_server_still_answers_429(monkeypatch):
    monkeypatch.setattr(web, "MAX_RUNNING", 0)
    r = TestClient(web.app).post("/api/jobs", json={"text": SCHEMA})
    assert r.status_code == 429 and "busy" in r.json()["detail"]


def job_polled_at(t):
    return {"id": f"j{t}", "done": False, "phase": "main", "started": 0, "_stopper": Stopper(), "_last_poll": t}


def test_search_nobody_polls_is_stopped(monkeypatch):
    monkeypatch.setattr(web, "IDLE_STOP", 60)
    old, recent = job_polled_at(100.0), job_polled_at(150.0)
    web.JOBS.update({old["id"]: old, recent["id"]: recent})
    web._reap_idle(now=170.0)
    assert old["_stopper"].stopped and old["phase"] == "stopping"
    assert not recent["_stopper"].stopped


def test_idle_stop_is_off_by_default(monkeypatch):
    monkeypatch.setattr(web, "IDLE_STOP", 0)
    old = job_polled_at(0.0)
    web.JOBS[old["id"]] = old
    web._reap_idle(now=10_000.0)
    assert not old["_stopper"].stopped


def test_polling_keeps_a_search_alive(monkeypatch):
    monkeypatch.setattr(web, "IDLE_STOP", 60)
    j = job_polled_at(0.0)
    web.JOBS[j["id"]] = j
    TestClient(web.app).get(f"/api/jobs/{j['id']}")
    web._reap_idle()
    assert not j["_stopper"].stopped


def test_health_reports_limits_the_page_uses(monkeypatch):
    monkeypatch.setattr(web, "DEFAULT_TIME", 60.0)
    h = TestClient(web.app).get("/health").json()
    assert h["default_time"] == 60 and h["max_time"] == web.MAX_TIME and h["max_running"] == web.MAX_RUNNING
