"""FastAPI web interface: paste a schema, get a certified layout."""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel

from .api import baseline_svg, solve
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
