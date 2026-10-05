"""FastAPI web interface: paste a schema, get a certified layout."""
from __future__ import annotations

import os
import threading
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel

from .api import baseline_svg, solve, verify_and_render
from .parse import parse_any

STATIC = Path(__file__).parent / "static"
app = FastAPI(title="erlayout")


class LayoutRequest(BaseModel):
    text: str
    format: str | None = None
    k: int | None = None
    time_limit: float = 60.0


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/example", response_class=PlainTextResponse)
def example():
    return (STATIC / "example.sql").read_text()


def _parse(req: LayoutRequest):
    try:
        schema = parse_any(req.text, req.format)
    except Exception as exc:  # parser errors are user errors
        raise HTTPException(400, f"could not parse schema: {exc}")
    if not schema.tables:
        raise HTTPException(400, "no tables found")
    return schema


@app.post("/api/layout")
def layout(req: LayoutRequest):
    schema = _parse(req)
    if req.k is not None and not (2 <= req.k <= 40):
        raise HTTPException(400, "k must be between 2 and 40")
    t = max(1.0, min(req.time_limit, 1800.0))
    try:
        res = solve(schema, k=req.k, time_limit=t, workers=os.cpu_count() or 4)
    except RuntimeError as exc:
        raise HTTPException(422, str(exc))
    res["tables"] = len(schema.tables)
    res["edges"] = len(schema.edges)
    return res


@app.post("/api/baseline")
def baseline(req: LayoutRequest):
    schema = _parse(req)
    stats, svg = baseline_svg(schema)
    return {"crossings": stats["crossings"], "verification": stats["geo"], "svg": svg}


# ---------------------------------------------------------------- anytime jobs
# POST /api/jobs starts a search in a background thread; GET /api/jobs/{id}
# returns the best verified layout found so far, so the page can show a good
# diagram within seconds while the proof search continues.
JOBS: dict[str, dict] = {}
_JOBS_LOCK = threading.Lock()
MAX_JOBS = 20


def _run_job(job_id: str, schema, k, t):
    job = JOBS[job_id]

    def progress(ev):
        with _JOBS_LOCK:
            job["elapsed"] = ev["elapsed"]
            if ev["kind"] == "phase":
                job["phase"] = ev["phase"]
        if ev["kind"] == "improved":
            try:
                c, check, svg = verify_and_render(schema, ev["layout"])
            except AssertionError:
                return
            with _JOBS_LOCK:
                job.update(crossings=c, columns_used=ev["columns"], svg=svg, verification=check,
                           improvements=job["improvements"] + 1, best_at=ev["elapsed"])
        elif ev["kind"] == "bound":
            with _JOBS_LOCK:
                job["lower_bound"] = ev["bound"]

    try:
        res = solve(schema, k=k, time_limit=t, workers=os.cpu_count() or 4, on_progress=progress)
        res["tables"] = len(schema.tables)
        res["edges"] = len(schema.edges)
        with _JOBS_LOCK:
            job.update(res, done=True, phase="done")
    except Exception as exc:  # report, never crash the server
        with _JOBS_LOCK:
            job.update(done=True, phase="error", error=str(exc))


@app.post("/api/jobs")
def start_job(req: LayoutRequest):
    schema = _parse(req)
    if req.k is not None and not (2 <= req.k <= 40):
        raise HTTPException(400, "k must be between 2 and 40")
    t = max(1.0, min(req.time_limit, 1800.0))
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
                        "edges": len(schema.edges)}
    threading.Thread(target=_run_job, args=(job_id, schema, req.k, t), daemon=True).start()
    return {"id": job_id}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    with _JOBS_LOCK:
        job = JOBS.get(job_id)
        if job is None:
            raise HTTPException(404, "no such job")
        out = dict(job)
    if not out["done"]:
        out["elapsed"] = round(time.time() - out["started"], 1)
    return out
