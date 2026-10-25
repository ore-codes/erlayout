"""Benchmark: exact solver vs. the Sugiyama baseline.

* Random schemas shaped like real ones: table i references 1-2 earlier
  tables; FK columns sit at random rows.  Exact solve at the baseline's own
  column count (same-width comparison).
* Public sample schemas in examples/ (Chinook, Northwind, Sakila): the
  automatic search of erlayout.api.solve, as the CLI runs it.

  python bench/run_bench.py random|dense [seconds]
  python bench/run_bench.py named <seconds> chinook northwind sakila

Writes one JSON line per run to bench/out/results.jsonl, and each named
schema's drawing to bench/out/<name>_optimal.svg.
"""
import json
import random
import sys
import time
from pathlib import Path

from erlayout.api import solve
from erlayout.crossings import count_crossings
from erlayout.geometry import assign, geometric_check
from erlayout.heuristic import heuristic
from erlayout.model import Column, Edge, Schema, Table
from erlayout.parse import parse_sql
from erlayout.solver import ExactSolver

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "bench" / "out"
WORKERS = 2


def realistic(seed: int, n: int, dense: bool = False) -> Schema:
    rng = random.Random(seed)
    tables, edges = [], []
    for i in range(n):
        ncols = rng.randint(3, 8)
        cols = [Column("id", "bigint", True)] + [Column(f"c{j}", "text") for j in range(1, ncols)]
        t = Table(f"t{i:02d}", cols)
        tables.append(t)
        if i == 0:
            continue
        if dense:
            k = min(i, rng.choice([2, 3]), ncols - 1)
        else:
            k = 2 if (rng.random() < 0.45 and i >= 2) else 1
        targets = rng.sample(range(i), k)
        rows = rng.sample(range(1, ncols), min(k, ncols - 1))
        for tgt, r in zip(targets, rows):
            t.columns[r] = Column(f"{tables[tgt].name}_id", "bigint")
            edges.append(Edge(t.name, t.columns[r].name, tables[tgt].name, "id"))
    return Schema(tables, edges).validate()


def record(rec):
    with open(OUT / "results.jsonl", "a") as f:
        f.write(json.dumps(rec) + "\n")
    print(json.dumps({k: v for k, v in rec.items() if k not in ("layout",)}), flush=True)


def run_random(sizes, seeds, tl, dense=False):
    for n in sizes:
        for seed in seeds:
            s = realistic(seed, n, dense)
            t0 = time.time()
            h = heuristic(s)
            th = time.time() - t0
            k = len(h.orders)
            lay = ExactSolver(s, k).solve(time_limit=tl, workers=WORKERS, hint=h)
            rc = count_crossings(s, lay.columns, lay.orders)
            geo = geometric_check(s, assign(s, lay))
            record(dict(kind="dense" if dense else "random", name=f"{'d' if dense else 'r'}{n}_{seed}", n=n, m=len(s.solver_edges()), k=k,
                        baseline=h.crossings, baseline_seconds=round(th, 3),
                        exact=rc, status=lay.status, lower_bound=lay.lower_bound,
                        seconds=round(lay.seconds, 2), geo=geo["crossings"],
                        vars=lay.stats.get("vars"), constraints=lay.stats.get("constraints")))


def run_named(names, tl):
    for name in names:
        s = parse_sql((ROOT / "examples" / f"{name}.sql").read_text(), "postgres")
        res = solve(s, k=None, time_limit=tl, workers=WORKERS)
        record(dict(kind="named", name=name, n=len(s.tables), m=len(s.solver_edges()),
                    baseline=res["baseline"]["crossings"], baseline_columns=res["baseline"]["columns"],
                    exact=res["crossings"], status=res["status"], k=res["k"],
                    columns_used=res["columns_used"], lower_bound=res["lower_bound"],
                    seconds=res["seconds"], history=res["history"], guarantee=res["guarantee"],
                    verification=res["verification"], layout=res["layout"]))
        (OUT / f"{name}_optimal.svg").write_text(res["svg"])


if __name__ == "__main__":
    what = sys.argv[1]
    if what == "random":
        run_random([6, 8, 10, 12, 14], range(4), tl=float(sys.argv[2]) if len(sys.argv) > 2 else 120)
    elif what == "dense":
        run_random([8, 10, 12], range(4), tl=float(sys.argv[2]) if len(sys.argv) > 2 else 300, dense=True)
    elif what == "named":
        run_named(sys.argv[3:], tl=float(sys.argv[2]))
