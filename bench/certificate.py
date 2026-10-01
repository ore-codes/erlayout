"""Search for a K-independent lower-bound certificate for a schema.

Lemma 3: if E1, ..., Er are pairwise disjoint edge sets, every drawing of
the full schema with any K restricts to drawings of the sub-schemas, so
cr >= OPT_1 + ... + OPT_r at every K.  Each sub-schema optimum is global
when solved at K = its table count (Corollary of Lemma 2).

usage: certificate.py <schema file> <comma-separated tables> <sizes>
"""
import itertools
import json
import sys
import time

from erlayout.model import Schema
from erlayout.parse import parse_any
from erlayout.solver import ExactSolver


def induced(s, names):
    names = set(names)
    tabs = [t for t in s.tables if t.name in names]
    edges = [e for e in s.edges if e.src_table in names and e.dst_table in names]
    return Schema(tabs, edges).validate()


def connected(sub):
    adj = {t.name: set() for t in sub.tables}
    for e in sub.solver_edges():
        adj[e.src_table].add(e.dst_table)
        adj[e.dst_table].add(e.src_table)
    start = next(iter(adj))
    seen, st = {start}, [start]
    while st:
        u = st.pop()
        for v in adj[u]:
            if v not in seen:
                seen.add(v)
                st.append(v)
    return len(seen) == len(adj)


def search(s, core, sizes, out):
    found = []
    t0 = time.time()
    for size in sizes:
        for combo in itertools.combinations(core, size):
            sub = induced(s, combo)
            if not connected(sub) or len(sub.solver_edges()) < 3:
                continue
            try:
                lay = ExactSolver(sub, size).solve(time_limit=300, workers=2, phase2=False)
            except RuntimeError:
                continue
            if lay.status != "OPTIMAL":
                print("UNPROVEN", combo, flush=True)
                continue
            if lay.crossings >= 1:
                edges = sorted(e.label for e in sub.solver_edges())
                found.append(dict(tables=list(combo), opt=lay.crossings, edges=edges,
                                  seconds=round(lay.seconds, 2)))
                print("OPT", lay.crossings, combo, round(lay.seconds, 2), flush=True)
    json.dump(found, open(out, "w"), indent=1)
    print("done", len(found), round(time.time() - t0, 1))
    return found


if __name__ == "__main__":
    s = parse_any(open(sys.argv[1]).read())
    search(s, sys.argv[2].split(","), [int(x) for x in sys.argv[3].split(",")],
           "bench/out/certificate_candidates.json")
