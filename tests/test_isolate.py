import os
import time

import pytest
from test_stop import hard

from erlayout import isolate
from erlayout.api import solve
from erlayout.isolate import EventStopper, SearchCrashed, SolveError, solve_isolated
from erlayout.parse import parse_any

SCHEMA = """
CREATE TABLE a (id INT PRIMARY KEY);
CREATE TABLE b (id INT PRIMARY KEY, a_id INT, FOREIGN KEY (a_id) REFERENCES a(id));
CREATE TABLE c (id INT PRIMARY KEY, a_id INT, b_id INT,
  FOREIGN KEY (a_id) REFERENCES a(id), FOREIGN KEY (b_id) REFERENCES b(id));
"""
OPTS = {"reorder_rows": False, "pin_pk": True}


def die(conn, *_):
    os._exit(3)


def hang(conn, *_):
    time.sleep(60)


def refuse(conn, *_):
    conn.send(("error", (True, "model too large")))
    conn.close()


def test_result_matches_the_in_process_search():
    s = parse_any(SCHEMA, None)
    inner = solve(s, time_limit=10, workers=2)
    outer = solve_isolated(s, None, 10, 2, OPTS, None)
    assert outer["crossings"] == inner["crossings"] == outer["verification"]["crossings"]
    assert outer["status"] == inner["status"] == "OPTIMAL"
    assert outer["svg"].startswith("<svg") and outer["drawing"]["sections"]


def test_progress_arrives_already_verified_and_drawn():
    events = []
    solve_isolated(parse_any(SCHEMA, None), None, 10, 2, OPTS, None, on_progress=events.append)
    improved = [e for e in events if e["kind"] == "improved"]
    assert improved and all("layout" not in e for e in events)
    last = improved[-1]
    assert last["svg"].startswith("<svg") and last["drawing"]["sections"]
    assert last["verification"]["crossings"] == last["crossings"] and last["columns"] >= 2
    assert any(e["kind"] == "phase" for e in events)


def test_stop_ends_the_search_in_the_child():
    stopper = EventStopper()
    s = hard()
    t0 = time.time()
    import threading
    threading.Timer(2.0, stopper.stop).start()
    res = solve_isolated(s, None, 120, 2, OPTS, None, stopper=stopper)
    assert time.time() - t0 < 30 and res["stopped"] and stopper.stopped
    assert res["crossings"] == res["verification"]["crossings"]


def test_solver_errors_are_reported_as_solve_error():
    with pytest.raises(SolveError, match="model too large"):
        solve_isolated(parse_any(SCHEMA, None), None, 5, 2, OPTS, None, _target=refuse)


def test_a_crashing_search_does_not_take_the_caller_down():
    with pytest.raises(SearchCrashed, match="exit code 3"):
        solve_isolated(parse_any(SCHEMA, None), None, 5, 2, OPTS, None, _target=die)


def test_a_search_that_outlives_its_budget_is_killed(monkeypatch):
    monkeypatch.setattr(isolate, "GRACE", 1.0)
    t0 = time.time()
    with pytest.raises(SearchCrashed, match="did not stop"):
        solve_isolated(parse_any(SCHEMA, None), None, 1, 2, OPTS, None, _target=hang)
    assert time.time() - t0 < 15


def test_no_child_process_is_left_behind():
    import multiprocessing as mp
    solve_isolated(parse_any(SCHEMA, None), None, 5, 2, OPTS, None)
    with pytest.raises(SearchCrashed):
        solve_isolated(parse_any(SCHEMA, None), None, 5, 2, OPTS, None, _target=die)
    assert [p for p in mp.active_children() if p.name.startswith("Process")] == []
