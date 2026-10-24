"""Command line interface.

  erlayout layout schema.sql -o diagram.svg [--k 5] [--time 120] [--json result.json]
  erlayout layout big.sql --split on --hide-hubs auto --max-group 15
  erlayout layout schema.dbml -o diagram.drawio -o diagram.excalidraw -o diagram.mmd [--each-group]
  erlayout serve [--port 8000]
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import threading
from pathlib import Path

from .api import resolve_hubs, solve, solve_split, verify_and_draw, wants_split
from .export import FORMATS, drawing, export, pick, slug
from .parse import parse_any
from .solver import Stopper


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="erlayout", description="Provably crossing-optimal ER diagram layout")
    sub = ap.add_subparsers(dest="cmd", required=True)

    lp = sub.add_parser("layout", help="lay out a schema file (SQL DDL, DBML or JSON)")
    lp.add_argument("schema")
    lp.add_argument("-o", "--output", action="append",
                    help="output file, repeatable; the extension picks the format: .svg, .drawio, "
                         ".excalidraw or .mmd (Mermaid) (default: diagram.svg)")
    lp.add_argument("--each-group", action="store_true",
                    help="with a split schema, also write each diagram to its own file next to every output")
    lp.add_argument("--format", choices=["sql", "dbml", "json"])
    lp.add_argument("--k", type=int, help="number of columns (default: anytime search from the baseline width outward)")
    lp.add_argument("--k-max", type=int, help="largest column count to try in automatic mode")
    lp.add_argument("--time", type=float, default=120.0, help="total time budget in seconds")
    lp.add_argument("--certify-time", type=float,
                    help="extra seconds to search for a global optimality certificate (default: what is left of --time)")
    lp.add_argument("--reorder-rows", action="store_true",
                    help="let erlayout reorder the rows that carry foreign-key lines inside each table")
    lp.add_argument("--free-pk", action="store_true",
                    help="with --reorder-rows, allow the primary key to move below other key rows")
    lp.add_argument("--split", choices=["auto", "on", "off"], default="auto",
                    help="draw one diagram per group of related tables (auto: schemas over 40 tables)")
    lp.add_argument("--arrange", choices=["grid", "row", "column"], default="grid",
                    help="how the diagrams of a split schema are placed (default: grid)")
    lp.add_argument("--max-group", type=int, default=15, help="most tables in one diagram when splitting")
    lp.add_argument("--hide-hubs", default="",
                    help="'auto' or comma-separated tables whose links become row tags instead of lines (implies --split)")
    lp.add_argument("--workers", type=int, default=os.cpu_count() or 4)
    lp.add_argument("--json", help="also write the full result as JSON")

    sp = sub.add_parser("serve", help="start the web interface")
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--port", type=int, default=8000)

    a = ap.parse_args(argv)
    logging.basicConfig(level=os.environ.get("ERLAYOUT_LOG_LEVEL", "INFO" if a.cmd == "serve" else "WARNING").upper(),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if a.cmd == "serve":
        import uvicorn

        from .web import app
        uvicorn.run(app, host=a.host, port=a.port)
        return 0

    outputs = a.output or ["diagram.svg"]
    by_ext = {ext: fmt for fmt, ext in FORMATS.items()} | {".mermaid": "mermaid", ".md": "mermaid"}
    for o in outputs:
        if Path(o).suffix.lower() not in by_ext:
            ap.error(f"{o}: unknown output format; use .svg, .drawio, .excalidraw or .mmd")

    text = open(a.schema, encoding="utf-8").read()
    schema = parse_any(text, a.format)
    print(f"{len(schema.tables)} tables, {len(schema.edges)} foreign keys", file=sys.stderr)

    try:
        hubs = resolve_hubs(schema, a.hide_hubs.strip())
    except ValueError as exc:
        ap.error(str(exc))
    split = wants_split(schema, a.split, hubs, a.max_group)

    def write(d, final=False):
        """Every output, from the drawing; per-group files only at the end."""
        for o in outputs:
            fmt = by_ext[Path(o).suffix.lower()]
            Path(o).write_text(export(fmt, d, schema), encoding="utf-8")
            if final and a.each_group and len(d["sections"]) > 1:
                base = Path(o)
                for i, sec in enumerate(d["sections"], 1):
                    out = base.with_name(f"{base.stem}-{i:02d}-{slug(sec['name'] or 'diagram')}{base.suffix}")
                    out.write_text(export(fmt, pick(d, i), schema), encoding="utf-8")
        return ", ".join(outputs)

    def progress(ev):
        t = f"[{ev['elapsed']:6.1f}s]"
        if ev["kind"] == "improved" and split:
            print(f"{t} {ev['crossings']} crossings in total -> {write(ev['drawing'])}", file=sys.stderr, flush=True)
        elif ev["kind"] == "phase" and "group" in ev:
            print(f"{t} diagram {ev['group']} of {ev['groups']}", file=sys.stderr, flush=True)
        elif ev["kind"] == "improved":
            # write every improvement straight away, so a usable file exists early
            try:
                c, _, _, sec = verify_and_draw(schema, ev["layout"])
            except AssertionError:
                return
            print(f"{t} {c} crossings in {ev['columns']} columns ({ev['source']}) -> {write(drawing([sec]))}",
                  file=sys.stderr, flush=True)
        elif ev["kind"] == "phase":
            print(f"{t} {ev['phase']}" + (f" {ev['k']} columns" if "k" in ev else ""), file=sys.stderr, flush=True)
        elif ev["kind"] == "bound":
            print(f"{t} proven: every layered drawing has at least {ev['bound']} crossings",
                  file=sys.stderr, flush=True)

    stopper, out = Stopper(), {}

    def run():
        try:
            if split:
                out["res"] = solve_split(schema, time_limit=a.time, workers=a.workers, max_group=a.max_group,
                                         hubs=hubs, arrange=a.arrange, on_progress=progress, stopper=stopper,
                                         reorder_rows=a.reorder_rows, pin_pk=not a.free_pk)
            else:
                out["res"] = solve(schema, k=a.k, time_limit=a.time, workers=a.workers, k_max=a.k_max,
                                   certify_time=a.certify_time, on_progress=progress, stopper=stopper,
                                   reorder_rows=a.reorder_rows, pin_pk=not a.free_pk)
        except BaseException as exc:  # handed to the main thread
            out["error"] = exc

    # the search runs in a thread so Ctrl-C can stop it and still write the best layout
    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    try:
        while worker.is_alive():
            worker.join(0.2)
    except KeyboardInterrupt:
        print("stopping: finishing with the best layout so far (Ctrl-C again to quit at once)",
              file=sys.stderr, flush=True)
        stopper.stop()
        worker.join()
    if "error" in out:
        raise out["error"]
    res = out["res"]
    wrote = write(res["drawing"], final=True)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump({k: v for k, v in res.items() if k not in ("svg", "drawing")}, f, indent=2)
    print(f"best found after {res['time_to_best']}s of {res['seconds']}s", file=sys.stderr)
    print(res["guarantee"])
    for g in res.get("groups", []):
        print(f"  {g['title']}: {g['tables']} tables, {g['crossings']} crossing{'' if g['crossings'] == 1 else 's'}"
              + (" (optimal)" if g["global_optimal"] else ""))
    for t, order in res.get("row_order", {}).items():
        print(f"  reordered {t}: {', '.join(order)}")
    print(f"baseline (barycenter): {res['baseline']['crossings']} crossings; "
          f"verified: {res['verification']}; wrote {wrote}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
