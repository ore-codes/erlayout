"""Run one search in its own process.

CP-SAT allocates hundreds of megabytes while it solves, and a long-lived
server process keeps most of it after the search ends.  A search in a child
process gives all of it back when the child exits, and a crash inside the
solver ends that one search instead of the server.

The child is started from a forkserver that has already imported the solver
(the platform default start method, spawn, where there is no forkserver), so
starting a search costs milliseconds, not a second of imports.
"""
from __future__ import annotations

import functools
import multiprocessing as mp
import threading
import time

from .api import solve, solve_split, verify_and_draw
from .export import drawing
from .solver import Stopper

GRACE = 90.0   # seconds past its time budget before a search that has not stopped is killed


class SolveError(RuntimeError):
    """The solver rejected the request (for example, a model too large to build)."""


class SearchCrashed(Exception):
    """The search process died or did not stop in time."""


@functools.lru_cache(maxsize=1)
def _context():
    ctx = mp.get_context("forkserver" if "forkserver" in mp.get_all_start_methods() else "spawn")
    if ctx.get_start_method() == "forkserver":
        ctx.set_forkserver_preload(["erlayout.api"])
    return ctx


class EventStopper:
    """Stops a search running in another process: stop() sets the event that process waits on."""

    def __init__(self):
        self.event = _context().Event()

    @property
    def stopped(self) -> bool:
        return self.event.is_set()

    def stop(self):
        self.event.set()


def _child(conn, stop_event, schema, k, time_limit, workers, opts, split):
    stopper = Stopper()
    threading.Thread(target=lambda: (stop_event.wait(), stopper.stop()), daemon=True).start()

    def progress(ev):
        # verify and draw here, so the parent only stores what it receives
        if ev["kind"] == "improved" and not split:
            try:
                c, check, svg, sec = verify_and_draw(schema, ev["layout"])
            except AssertionError:
                return
            ev = {"kind": "improved", "elapsed": ev["elapsed"], "columns": ev["columns"], "crossings": c,
                  "svg": svg, "verification": check, "drawing": drawing([sec])}
        else:
            ev = {name: v for name, v in ev.items() if name != "layout"}
        try:
            conn.send(("event", ev))
        except Exception:   # an event that cannot be sent is only progress; the result still arrives
            pass

    try:
        if split:
            res = solve_split(schema, time_limit=time_limit, workers=workers, on_progress=progress,
                              stopper=stopper, **split, **opts)
        else:
            res = solve(schema, k=k, time_limit=time_limit, workers=workers, on_progress=progress,
                        stopper=stopper, **opts)
        conn.send(("done", res))
    except RuntimeError as exc:
        conn.send(("error", (True, str(exc))))
    except BaseException as exc:  # reported to the parent, which decides what the caller sees
        conn.send(("error", (False, f"{type(exc).__name__}: {exc}")))
    finally:
        conn.close()


def solve_isolated(schema, k, time_limit, workers, opts, split, on_progress=None, stopper=None, _target=_child) -> dict:
    """solve() or solve_split() (when split is the options dict) in a child process.

    on_progress receives the search's events; an "improved" event already
    carries its verified drawing (crossings, svg, drawing and, without a
    split, columns and verification).  stopper is an EventStopper.  Raises
    SolveError when the solver reports an error and SearchCrashed when the
    process dies or outlives its budget."""
    ctx = _context()
    stopper = stopper or EventStopper()
    recv, send = ctx.Pipe(duplex=False)
    proc = ctx.Process(target=_target, args=(send, stopper.event, schema, k, time_limit, workers, opts, split),
                       daemon=True)
    proc.start()
    send.close()   # only the child holds the sending end now, so its exit shows up as EOF
    deadline = time.monotonic() + time_limit + GRACE
    result = None
    try:
        while True:
            alive = proc.is_alive()   # asked before reading: all a dead child sent is already in the pipe
            try:
                message = recv.recv() if recv.poll(0.25) else None
            except (EOFError, BrokenPipeError):   # the child's end is closed; Windows raises the latter from poll()
                break
            if message is None:
                if not alive:
                    break
            else:
                kind, payload = message
                if kind == "event":
                    if on_progress:
                        on_progress(payload)
                elif kind == "done":
                    result = payload
                elif kind == "error":
                    solver_error, message = payload
                    raise (SolveError if solver_error else SearchCrashed)(message)
            if time.monotonic() > deadline:
                proc.kill()
                raise SearchCrashed("the search did not stop within its time budget and was ended")
    finally:
        recv.close()
        proc.join(5)
        if proc.is_alive():
            proc.kill()
            proc.join()
    if result is None:
        raise SearchCrashed(f"the search process ended unexpectedly (exit code {proc.exitcode})")
    return result
