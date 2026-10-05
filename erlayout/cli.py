"""Command line interface.

  erlayout layout schema.sql -o diagram.svg [--k 5] [--time 120] [--json result.json]
  erlayout serve [--port 8000]
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from .api import solve, verify_and_render
from .parse import parse_any


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="erlayout", description="Provably crossing-optimal ER diagram layout")
    sub = ap.add_subparsers(dest="cmd", required=True)

    lp = sub.add_parser("layout", help="lay out a schema file (SQL DDL, DBML or JSON)")
    lp.add_argument("schema")
    lp.add_argument("-o", "--output", default="diagram.svg")
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
    lp.add_argument("--workers", type=int, default=os.cpu_count() or 4)
    lp.add_argument("--json", help="also write the full result as JSON")

    sp = sub.add_parser("serve", help="start the web interface")
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--port", type=int, default=8000)

    a = ap.parse_args(argv)
    if a.cmd == "serve":
        import uvicorn
        from .web import app
        uvicorn.run(app, host=a.host, port=a.port)
        return 0

    text = open(a.schema, encoding="utf-8").read()
    schema = parse_any(text, a.format)
    print(f"{len(schema.tables)} tables, {len(schema.edges)} foreign keys", file=sys.stderr)

    def progress(ev):
        t = f"[{ev['elapsed']:6.1f}s]"
        if ev["kind"] == "improved":
            # write every improvement straight away, so a usable file exists early
            try:
                c, _, svg = verify_and_render(schema, ev["layout"])
            except AssertionError:
                return
            with open(a.output, "w", encoding="utf-8") as f:
                f.write(svg)
            print(f"{t} {c} crossings in {ev['columns']} columns ({ev['source']}) -> {a.output}",
                  file=sys.stderr, flush=True)
        elif ev["kind"] == "phase":
            print(f"{t} {ev['phase']}" + (f" {ev['k']} columns" if "k" in ev else ""), file=sys.stderr, flush=True)
        elif ev["kind"] == "bound":
            print(f"{t} proven: every layered drawing has at least {ev['bound']} crossings",
                  file=sys.stderr, flush=True)

    res = solve(schema, k=a.k, time_limit=a.time, workers=a.workers, k_max=a.k_max,
                certify_time=a.certify_time, on_progress=progress,
                reorder_rows=a.reorder_rows, pin_pk=not a.free_pk)
    with open(a.output, "w", encoding="utf-8") as f:
        f.write(res["svg"])
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump({k: v for k, v in res.items() if k != "svg"}, f, indent=2)
    print(f"best found after {res['time_to_best']}s of {res['seconds']}s", file=sys.stderr)
    print(res["guarantee"])
    for t, order in res.get("row_order", {}).items():
        print(f"  reordered {t}: {', '.join(order)}")
    print(f"baseline (barycenter): {res['baseline']['crossings']} crossings; "
          f"verified: {res['verification']}; wrote {a.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
