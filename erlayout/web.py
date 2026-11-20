"""FastAPI web interface: paste a schema, get a certified layout."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
import uuid
from collections import OrderedDict
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse, Response
from pydantic import BaseModel

from .api import baseline_split, baseline_svg, resolve_hubs, wants_split
from .export import FORMATS, export, export_each, pick
from .isolate import EventStopper, SearchCrashed, SolveError, solve_isolated
from .parse import parse_any
from .render import ARRANGEMENTS
from .solver import Stopper

STATIC = Path(__file__).parent / "static"
log = logging.getLogger(__name__)

# Limits for a shared deployment: solves running at once (more get HTTP 429),
# the longest time budget a request may ask for, CP-SAT threads per solve, the
# budget the page starts with, and how long a search may go unpolled (its tab
# closed) before it is stopped to free its slot; 0 never stops one.
MAX_RUNNING = int(os.environ.get("ERLAYOUT_MAX_RUNNING", "4"))
MAX_TIME = float(os.environ.get("ERLAYOUT_MAX_TIME", "1800"))
WORKERS = int(os.environ.get("ERLAYOUT_WORKERS", "0")) or os.cpu_count() or 4
DEFAULT_TIME = float(os.environ.get("ERLAYOUT_DEFAULT_TIME", "300"))
IDLE_STOP = float(os.environ.get("ERLAYOUT_IDLE_STOP", "0"))


@asynccontextmanager
async def _lifespan(_app):
    if IDLE_STOP:
        threading.Thread(target=_reap_loop, daemon=True).start()
    yield


app = FastAPI(title="erlayout", lifespan=_lifespan)
_running = 0
_running_lock = threading.Lock()


def _claim_slot():
    global _running
    with _running_lock:
        if _running >= MAX_RUNNING:
            raise HTTPException(429, f"server busy: {MAX_RUNNING} layouts already running, try again shortly")
        _running += 1


def _release_slot():
    global _running
    with _running_lock:
        _running -= 1


def _budget(req) -> float:
    return max(1.0, min(req.time_limit, MAX_TIME))


class LayoutRequest(BaseModel):
    text: str
    format: str | None = None
    k: int | None = None
    time_limit: float = 60.0
    rows: str = "keep"          # keep | reorder | reorder-free
    split: str = "auto"         # auto | on | off: one diagram per group of related tables
    hide_hubs: str = ""         # "" | auto | comma-separated table names drawn as row tags only
    max_group: int = 15
    arrange: str = "grid"       # grid | row | column: how the diagrams of a split schema are placed


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/health")
def health():
    with _JOBS_LOCK:
        running = sum(1 for j in JOBS.values() if not j["done"])
    return {"status": "ok", "jobs_running": running, "max_running": MAX_RUNNING, "max_time": MAX_TIME,
            "default_time": DEFAULT_TIME}


@app.get("/api/example", response_class=PlainTextResponse)
def example():
    return (STATIC / "example.sql").read_text()


def _row_opts(req: LayoutRequest) -> dict:
    if req.rows not in ("keep", "reorder", "reorder-free"):
        raise HTTPException(400, "rows must be keep, reorder or reorder-free")
    return {"reorder_rows": req.rows != "keep", "pin_pk": req.rows != "reorder-free"}


def _split_opts(req: LayoutRequest, schema) -> dict | None:
    """Split options when this request is laid out as several diagrams, else None."""
    if req.split not in ("auto", "on", "off"):
        raise HTTPException(400, "split must be auto, on or off")
    if not (4 <= req.max_group <= 40):
        raise HTTPException(400, "max_group must be between 4 and 40")
    try:
        hubs = resolve_hubs(schema, req.hide_hubs.strip())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if req.arrange not in ARRANGEMENTS:
        raise HTTPException(400, f"arrange must be one of {', '.join(ARRANGEMENTS)}")
    if not wants_split(schema, req.split, hubs, req.max_group):
        return None
    return {"max_group": req.max_group, "hubs": hubs, "arrange": req.arrange}


def _parse(req: LayoutRequest):
    try:
        schema = parse_any(req.text, req.format)
    except Exception as exc:  # parser errors are user errors
        msg = re.sub(r"\x1b\[[0-9;]*m", "", str(exc))  # sqlglot underlines the error with terminal codes
        raise HTTPException(400, f"could not parse schema: {msg}") from exc
    if not schema.tables:
        raise HTTPException(400, "no tables found")
    return schema


@app.post("/api/layout")
def layout(req: LayoutRequest):
    schema = _parse(req)
    if req.k is not None and not (2 <= req.k <= 40):
        raise HTTPException(400, "k must be between 2 and 40")
    t = _budget(req)
    opts = _row_opts(req)
    split = _split_opts(req, schema)
    _claim_slot()
    try:
        res = solve_isolated(schema, req.k, t, WORKERS, opts, split)
    except SolveError as exc:
        raise HTTPException(422, str(exc)) from exc
    except SearchCrashed as exc:
        raise HTTPException(500, str(exc)) from exc
    finally:
        _release_slot()
    res["tables"] = len(schema.tables)
    res["edges"] = len(schema.edges)
    return res


@app.post("/api/baseline")
def baseline(req: LayoutRequest):
    schema = _parse(req)
    split = _split_opts(req, schema)
    stats, svg = baseline_split(schema, **split) if split else baseline_svg(schema)
    return {"crossings": stats["crossings"], "verification": stats["geo"], "svg": svg}


# ---------------------------------------------------------------- anytime jobs
# POST /api/jobs starts a search in a background thread; GET /api/jobs/{id}
# returns the best verified layout found so far, so the page can show a good
# diagram within seconds while the proof search continues.
JOBS: dict[str, dict] = {}
_JOBS_LOCK = threading.Lock()
MAX_JOBS = 20

# Finished searches that proved their layout optimal, by request, so the same
# request (usually the built-in example) is answered without a solve or a slot.
_CACHE: OrderedDict[str, dict] = OrderedDict()
MAX_CACHE = 50
_UNSHARED = ("id", "started", "cached", "_stopper", "_last_poll")


def _cache_key(req, t: float) -> str:
    fields = [req.text, req.format, req.k, t, req.rows, req.split, req.hide_hubs, req.max_group, req.arrange]
    return hashlib.sha256(json.dumps(fields).encode()).hexdigest()


def _add_job(job: dict) -> str:
    with _JOBS_LOCK:
        if len(JOBS) >= MAX_JOBS:   # forget the oldest finished jobs
            for old in sorted((j for j in JOBS.values() if j["done"]), key=lambda j: j["started"]):
                JOBS.pop(old["id"], None)
                if len(JOBS) < MAX_JOBS:
                    break
        JOBS[job["id"]] = job
    return job["id"]


def _reap_idle(now: float | None = None):
    """Stop searches that nobody polls any more, so a closed tab frees its slot."""
    if not IDLE_STOP:
        return
    now = time.time() if now is None else now
    with _JOBS_LOCK:
        idle = [j for j in JOBS.values() if not j["done"] and now - j["_last_poll"] > IDLE_STOP]
        for j in idle:
            j["phase"] = "stopping"
    for j in idle:
        j["_stopper"].stop()


def _reap_loop():
    while True:
        time.sleep(max(1.0, IDLE_STOP / 4))
        _reap_idle()


def _names(d) -> list[str]:
    """What the page lists in its diagram picker."""
    return [s["name"] or "Diagram" for s in d["sections"]]


def _run_job(job_id: str, schema, k, t, opts, split, key):
    job = JOBS[job_id]

    def progress(ev):   # an "improved" event arrives from the search process already verified and drawn
        with _JOBS_LOCK:
            job["elapsed"] = ev["elapsed"]
            if ev["kind"] == "phase":
                job["phase"] = ev["phase"]
                if "group" in ev:
                    job.update(group=ev["group"], groups=ev["groups"])
            elif ev["kind"] == "improved":
                found = {"crossings": ev["crossings"], "svg": ev["svg"], "_drawing": ev["drawing"],
                         "improvements": job["improvements"] + 1, "best_at": ev["elapsed"]}
                if split:
                    job.update(found, diagrams=_names(ev["drawing"]))
                else:
                    job.update(found, diagrams=["Diagram"], columns_used=ev["columns"], verification=ev["verification"])
            elif ev["kind"] == "bound":
                job["lower_bound"] = ev["bound"]

    try:
        res = solve_isolated(schema, k, t, WORKERS, opts, split, on_progress=progress, stopper=job["_stopper"])
        res["tables"] = len(schema.tables)
        res["edges"] = len(schema.edges)
        res["_drawing"] = res.pop("drawing")
        res["diagrams"] = _names(res["_drawing"])
        with _JOBS_LOCK:
            job.update(res, done=True, phase="done")
            if res["status"] == "OPTIMAL" and not res.get("stopped"):
                _CACHE[key] = {name: v for name, v in job.items() if name not in _UNSHARED}
                while len(_CACHE) > MAX_CACHE:
                    _CACHE.popitem(last=False)
    except Exception as exc:  # report, never crash the server
        log.exception("job %s failed", job_id)
        with _JOBS_LOCK:
            job.update(done=True, phase="error", error=str(exc))
    finally:
        _release_slot()


@app.post("/api/jobs")
def start_job(req: LayoutRequest):
    schema = _parse(req)
    if req.k is not None and not (2 <= req.k <= 40):
        raise HTTPException(400, "k must be between 2 and 40")
    t = _budget(req)
    opts = _row_opts(req)
    split = _split_opts(req, schema)
    key = _cache_key(req, t)
    job_id, now = uuid.uuid4().hex[:12], time.time()
    with _JOBS_LOCK:
        hit = _CACHE.get(key)
        if hit:
            _CACHE.move_to_end(key)
    if hit:
        log.info("job %s: answered from the cache", job_id)
        return {"id": _add_job({**hit, "id": job_id, "started": now, "cached": True,
                                "_stopper": Stopper(), "_last_poll": now})}
    _claim_slot()
    _add_job({"id": job_id, "done": False, "phase": "starting", "started": now,
              "time_limit": t, "elapsed": 0.0, "crossings": None, "svg": None,
              "improvements": 0, "lower_bound": 0, "tables": len(schema.tables),
              "edges": len(schema.edges), "split": bool(split), "_schema": schema,
              "_stopper": EventStopper(), "_last_poll": now})
    log.info("job %s: %d tables, %d foreign keys, %.0fs budget", job_id, len(schema.tables), len(schema.edges), t)
    threading.Thread(target=_run_job, args=(job_id, schema, req.k, t, opts, split, key), daemon=True).start()
    return {"id": job_id}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    with _JOBS_LOCK:
        job = JOBS.get(job_id)
        if job is None:
            raise HTTPException(404, "no such job")
        job["_last_poll"] = time.time()
        out = {k: v for k, v in job.items() if not k.startswith("_")}   # drawings are fetched via export
    if not out["done"]:
        out["elapsed"] = round(time.time() - out["started"], 1)
    return out


@app.post("/api/jobs/{job_id}/stop")
def stop_job(job_id: str):
    """End the search now; the job then finishes with the best layout so far."""
    with _JOBS_LOCK:
        job = JOBS.get(job_id)
        if job is None:
            raise HTTPException(404, "no such job")
        if not job["done"]:
            job["phase"] = "stopping"
        stopper = job["_stopper"]
    stopper.stop()
    return {"stopping": True}


@app.get("/api/jobs/{job_id}/export/{fmt}")
def export_job(job_id: str, fmt: str, group: str | None = None):
    """The job's best layout so far as svg, drawio, excalidraw or mermaid.
    group=N gives diagram N of a split schema on its own; group=each gives a
    zip with one file per diagram."""
    if fmt not in FORMATS:
        raise HTTPException(400, f"format must be one of {', '.join(FORMATS)}")
    with _JOBS_LOCK:
        job = JOBS.get(job_id)
        if job is None:
            raise HTTPException(404, "no such job")
        d, schema = job.get("_drawing"), job["_schema"]
    if d is None:
        raise HTTPException(409, "no layout yet")
    name = "er-layout"
    if group == "each":
        return Response(export_each(fmt, d, schema), media_type="application/zip",
                        headers={"content-disposition": f'attachment; filename="{name}-{fmt}.zip"'})
    if group:
        try:
            d = pick(d, int(group))
        except ValueError as exc:
            raise HTTPException(400, f"group must be a diagram number or 'each': {exc}") from exc
        name += f"-group{int(group)}"
    media = {"svg": "image/svg+xml", "excalidraw": "application/json"}.get(fmt, "text/plain")
    return Response(export(fmt, d, schema), media_type=media,
                    headers={"content-disposition": f'attachment; filename="{name}{FORMATS[fmt]}"'})
