"""FastAPI web interface: paste a schema, get a certified layout."""
from __future__ import annotations

import logging
import os
import re
import threading
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse, Response
from pydantic import BaseModel

from .api import baseline_split, baseline_svg, resolve_hubs, solve, solve_split, verify_and_draw, wants_split
from .export import FORMATS, drawing, export, export_each, pick
from .parse import parse_any

STATIC = Path(__file__).parent / "static"
log = logging.getLogger(__name__)
app = FastAPI(title="erlayout")

# Limits for a shared deployment: solves running at once (more get HTTP 429),
# the longest time budget a request may ask for, and CP-SAT threads per solve.
MAX_RUNNING = int(os.environ.get("ERLAYOUT_MAX_RUNNING", "4"))
MAX_TIME = float(os.environ.get("ERLAYOUT_MAX_TIME", "1800"))
WORKERS = int(os.environ.get("ERLAYOUT_WORKERS", "0")) or os.cpu_count() or 4
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


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/health")
def health():
    with _JOBS_LOCK:
        running = sum(1 for j in JOBS.values() if not j["done"])
    return {"status": "ok", "jobs_running": running, "max_running": MAX_RUNNING, "max_time": MAX_TIME}


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
    if not wants_split(schema, req.split, hubs, req.max_group):
        return None
    return {"max_group": req.max_group, "hubs": hubs}


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
        if split:
            res = solve_split(schema, time_limit=t, workers=WORKERS, **split, **opts)
        else:
            res = solve(schema, k=req.k, time_limit=t, workers=WORKERS, **opts)
    except RuntimeError as exc:
        raise HTTPException(422, str(exc)) from exc
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


def _names(d) -> list[str]:
    """What the page lists in its diagram picker."""
    return [s["name"] or "Diagram" for s in d["sections"]]


def _run_job(job_id: str, schema, k, t, opts, split):
    job = JOBS[job_id]

    def progress(ev):
        with _JOBS_LOCK:
            job["elapsed"] = ev["elapsed"]
            if ev["kind"] == "phase":
                job["phase"] = ev["phase"]
                if "group" in ev:
                    job.update(group=ev["group"], groups=ev["groups"])
        if ev["kind"] == "improved" and split:   # solve_split sends the drawing, already verified
            with _JOBS_LOCK:
                job.update(crossings=ev["crossings"], svg=ev["svg"], _drawing=ev["drawing"],
                           diagrams=_names(ev["drawing"]),
                           improvements=job["improvements"] + 1, best_at=ev["elapsed"])
        elif ev["kind"] == "improved":
            try:
                c, check, svg, sec = verify_and_draw(schema, ev["layout"])
            except AssertionError:
                return
            with _JOBS_LOCK:
                job.update(crossings=c, columns_used=ev["columns"], svg=svg, verification=check,
                           _drawing=drawing([sec]), diagrams=["Diagram"],
                           improvements=job["improvements"] + 1, best_at=ev["elapsed"])
        elif ev["kind"] == "bound":
            with _JOBS_LOCK:
                job["lower_bound"] = ev["bound"]

    try:
        if split:
            res = solve_split(schema, time_limit=t, workers=WORKERS, on_progress=progress, **split, **opts)
        else:
            res = solve(schema, k=k, time_limit=t, workers=WORKERS, on_progress=progress, **opts)
        res["tables"] = len(schema.tables)
        res["edges"] = len(schema.edges)
        res["_drawing"] = res.pop("drawing")
        res["diagrams"] = _names(res["_drawing"])
        with _JOBS_LOCK:
            job.update(res, done=True, phase="done")
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
    _claim_slot()
    job_id = uuid.uuid4().hex[:12]
    with _JOBS_LOCK:
        if len(JOBS) >= MAX_JOBS:   # forget the oldest finished jobs
            for old in sorted((j for j in JOBS.values() if j["done"]), key=lambda j: j["started"]):
                JOBS.pop(old["id"], None)
                if len(JOBS) < MAX_JOBS:
                    break
        JOBS[job_id] = {"id": job_id, "done": False, "phase": "starting", "started": time.time(),
                        "time_limit": t, "elapsed": 0.0, "crossings": None, "svg": None,
                        "improvements": 0, "lower_bound": 0, "tables": len(schema.tables),
                        "edges": len(schema.edges), "split": bool(split), "_schema": schema}
    log.info("job %s: %d tables, %d foreign keys, %.0fs budget", job_id, len(schema.tables), len(schema.edges), t)
    threading.Thread(target=_run_job, args=(job_id, schema, req.k, t, opts, split), daemon=True).start()
    return {"id": job_id}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    with _JOBS_LOCK:
        job = JOBS.get(job_id)
        if job is None:
            raise HTTPException(404, "no such job")
        out = {k: v for k, v in job.items() if not k.startswith("_")}   # drawings are fetched via export
    if not out["done"]:
        out["elapsed"] = round(time.time() - out["started"], 1)
    return out


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
