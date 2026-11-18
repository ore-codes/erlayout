"""MCP server: lets an assistant lay out an ER diagram with erlayout.

  erlayout mcp [--output-dir DIR] [--max-time 120]

One tool, `layout_er_diagram`, takes a schema (SQL DDL, DBML or JSON) and
returns the crossing count, the guarantee and the diagram.  Files are written
only inside the output directory, and never over an existing file unless asked.
"""
from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path
from typing import Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from .api import resolve_hubs, solve, solve_split, wants_split
from .export import FORMATS, export
from .parse import parse_any
from .solver import Stopper

MAX_INLINE = 20_000   # characters of diagram returned in the reply; larger ones must be saved to a file

INSTRUCTIONS = """\
Lays out entity-relationship diagrams with the fewest line crossings and says what it can prove about the result.
Pass the schema text (SQL CREATE TABLE statements, DBML or JSON). The reply states the crossing count, how it compares
with a standard layered layout, and a guarantee: either proven optimal, or the best found with a proven lower bound.
Report the guarantee as given; do not claim more than it says. SVG, draw.io and Excalidraw diagrams are large:
pass save_as to write them to a file instead of receiving them in the reply."""


def _safe_path(output_dir: Path, name: str, fmt: str, overwrite: bool) -> Path:
    """The file to write: inside output_dir, with the format's extension, and not an existing file unless overwrite."""
    p = Path(name)
    if p.is_absolute() or ".." in p.parts:
        raise ValueError("save_as must be a relative path without '..'")
    if p.suffix.lower() != FORMATS[fmt]:
        raise ValueError(f"save_as must end in {FORMATS[fmt]} for format {fmt}")
    root = output_dir.resolve()
    target = (root / p).resolve()
    if root not in target.parents:
        raise ValueError("save_as must stay inside the output directory")
    if target.exists() and not overwrite:
        raise ValueError(f"{name} already exists; choose another name or pass overwrite=true")
    return target


def run_layout(text: str, input_format: str | None, fmt: str, time_limit: float, reorder_rows: bool,
               workers: int, stopper: Stopper | None = None) -> tuple[dict, str]:
    """Solve and export; returns the summary and the diagram text."""
    try:
        schema = parse_any(text, input_format)
    except Exception as exc:  # parser errors are user errors; sqlglot underlines them with terminal codes
        msg = re.sub(r"\x1b\[[0-9;]*m", "", str(exc))
        raise ValueError(f"could not parse schema: {msg}") from exc
    if not schema.tables:
        raise ValueError("no tables found")
    opts = {"reorder_rows": reorder_rows, "pin_pk": True}
    hubs = resolve_hubs(schema, "")
    if wants_split(schema, "auto", hubs, 15):
        res = solve_split(schema, time_limit=time_limit, workers=workers, max_group=15, hubs=hubs, stopper=stopper, **opts)
    else:
        res = solve(schema, time_limit=time_limit, workers=workers, stopper=stopper, **opts)
    d = res["drawing"]
    summary = {
        "tables": len(schema.tables),
        "foreign_keys": len(schema.edges),
        "crossings": res["crossings"],
        "baseline_crossings": res["baseline"]["crossings"],
        "proven_optimal": res["status"] == "OPTIMAL",
        "guarantee": res["guarantee"],
        "diagrams": len(d["sections"]),
        "independent_checks": res["verification"],
        "seconds": res["seconds"],
        "format": fmt,
    }
    if res.get("row_order"):
        summary["reordered_rows"] = res["row_order"]
    return summary, export(fmt, d, schema)


def build_server(output_dir: Path, max_time: float = 120.0, workers: int | None = None) -> MCPServer:
    workers = workers or os.cpu_count() or 4
    server = MCPServer("erlayout", instructions=INSTRUCTIONS)
    busy = asyncio.Lock()   # one search at a time: each one uses every core it is given

    @server.tool()
    async def layout_er_diagram(
        schema: str,
        format: Literal["svg", "drawio", "excalidraw", "mermaid"] = "svg",
        input_format: Literal["sql", "dbml", "json"] | None = None,
        time_limit: float = 30.0,
        reorder_rows: bool = False,
        save_as: str | None = None,
        overwrite: bool = False,
    ) -> dict:
        """Lay out an ER diagram with the fewest possible line crossings.

        schema: the schema text (SQL DDL, DBML or JSON).
        format: the diagram format to produce.
        input_format: sql, dbml or json; detected when omitted.
        time_limit: seconds to search (1 to the server's maximum). A usable layout exists within about a second;
            more time can improve it and can prove it optimal.
        reorder_rows: also reorder the foreign-key rows inside each table (fewer crossings, changes how tables read).
        save_as: relative file name inside the output directory, ending in the format's extension (.svg, .drawio,
            .excalidraw or .mmd). Required for svg, drawio and excalidraw diagrams, which are too large to return.
        overwrite: allow replacing an existing file.
        """
        t = max(1.0, min(time_limit, max_time))
        stopper = Stopper()
        try:    # a ToolError reaches the model with its message; any other exception would be hidden from it
            target = _safe_path(output_dir, save_as, format, overwrite) if save_as else None
            async with busy:
                try:
                    summary, diagram = await asyncio.to_thread(run_layout, schema, input_format, format, t,
                                                               reorder_rows, workers, stopper)
                except asyncio.CancelledError:
                    stopper.stop()
                    raise
            if target:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(diagram, encoding="utf-8")
                summary["saved_to"] = str(target)
            elif len(diagram) > MAX_INLINE:
                raise ValueError(f"the {format} diagram is {len(diagram):,} characters; "
                                 "pass save_as to write it to a file")
            else:
                summary["diagram"] = diagram
        except (ValueError, RuntimeError) as exc:
            raise ToolError(str(exc)) from exc
        return summary

    return server
