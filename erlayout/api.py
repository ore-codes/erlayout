"""High-level entry point used by the CLI and the web server.

Automatic mode is an anytime search:

1. main     - one long fresh run at a wide width: the larger of the baseline's
              width and a third of the table count; every improvement is
              reported as soon as it is found;
2. wider    - a fresh run two columns wider;
3. bound    - grow sub-schemas around the best drawing's crossings to prove a
              lower bound that holds for every layered drawing (Lemma 3), each
              attempt capped so one hard proof cannot eat the budget;
4. compact  - try narrower widths that reach the same crossing count.
"""
from __future__ import annotations

import logging
import threading
import time

from .certify import lower_bound
from .crossings import count_crossings
from .geometry import assign, geometric_check
from .heuristic import heuristic
from .model import Schema
from .render import render_svg
from .solver import ExactSolver, Layout

log = logging.getLogger(__name__)


def _n(c: int, word: str = "crossing") -> str:
    return f"{c} {word}" + ("" if c == 1 else "s")


def guarantee(crossings: int, columns: int, width_proven: int | None, lb: int, cert: dict | None,
              n_tables: int, finished: bool = True) -> str:
    if crossings == 0:
        return "Proven optimal: 0 crossings, the best any layout in this model can achieve."
    if cert is not None or (width_proven is not None and width_proven >= n_tables):
        why = (f" Certificate: a {len(cert['tables'])}-table sub-schema that forces "
               f"{_n(cert['bound'])} in any drawing.") if cert is not None else ""
        return (f"Proven optimal: {_n(crossings)} is the minimum over every layered drawing "
                f"of this schema (all column counts).{why}")
    parts = []
    if width_proven is not None:
        parts.append(f"Proven optimal among drawings with at most {width_proven} columns: "
                     f"none has fewer than {_n(crossings)}.")
    else:
        parts.append(f"Best found: {_n(crossings)} in {_n(columns, 'column')}"
                     + (" (time limit reached)." if finished else " (still searching)."))
    if lb > 0:
        parts.append(f"Every layered drawing has at least {lb}, so at most {crossings - lb} more "
                     f"could be removed.")
    elif width_proven is None:
        parts.append("No lower bound proven yet, so a better drawing may exist.")
    return " ".join(parts)


def verify_and_render(schema: Schema, lay: Layout) -> tuple[int, dict, str]:
    """Independent recount + geometric check + SVG.  Raises on any mismatch.
    Uses the layout's own row order when rows were reordered."""
    schema = schema.with_row_order(lay.rows)
    recount = count_crossings(schema, lay.columns, lay.orders)
    geo = assign(schema, lay)
    check = geometric_check(schema, geo)
    if check["crossings"] != recount or check["edges_through_tables"] or check["table_overlaps"]:
        raise AssertionError(f"verification failed: recount={recount} geo={check}")
    return recount, check, render_svg(schema, geo)


class _Search:
    def __init__(self, schema: Schema, time_limit: float, workers: int, on_progress,
                 reorder_rows: bool = False, pin_pk: bool = True):
        self.schema, self.T, self.workers, self.cb = schema, time_limit, workers, on_progress
        self.reorder, self.pin_pk = reorder_rows, pin_pk
        self.n = len(schema.tables)
        self.t0 = time.time()
        self.lock = threading.Lock()
        self.best: Layout | None = None
        self.best_at = 0.0
        self.history: list[dict] = []
        self.proven: dict[int, int] = {}      # width -> proven OPT(width)
        self.lb = {"bound": 0, "tables": [], "edges": [], "complete": False, "attempts": []}

    def elapsed(self):
        return time.time() - self.t0

    def left(self):
        return self.T - self.elapsed()

    def emit(self, kind, **kw):
        if self.cb:
            try:
                self.cb({"kind": kind, "elapsed": round(self.elapsed(), 1), **kw})
            except Exception:  # a broken listener must not stop the search
                log.exception("progress callback failed on %r event", kind)

    def offer(self, lay: Layout, source: str):
        """Accept a candidate if it has fewer crossings, or as many in fewer columns."""
        try:
            c = count_crossings(self.schema.with_row_order(lay.rows), lay.columns, lay.orders)
        except Exception:
            log.exception("could not recount a candidate layout from %s; ignoring it", source)
            return
        with self.lock:
            b = self.best
            if b is not None and (c, len(lay.orders)) >= (b.crossings, len(b.orders)):
                return
            lay.crossings = c
            self.best, self.best_at = lay, self.elapsed()
        self.emit("improved", crossings=c, columns=len(lay.orders), source=source, layout=lay)

    def run_width(self, k: int, slice_: float, cap: int | None = None, phase2: bool = True,
                  hint: bool = True):
        use = None
        with self.lock:
            if hint and self.best is not None and len(self.best.orders) <= k:
                use = self.best
        try:
            lay = ExactSolver(self.schema, k, reorder_rows=self.reorder, pin_pk=self.pin_pk).solve(
                time_limit=slice_, workers=self.workers, hint=use, cap=cap, phase2=phase2,
                phase2_time=min(0.2 * slice_, 30) if phase2 else None,
                on_improve=lambda l: self.offer(l, f"{k} columns"))
        except RuntimeError as exc:
            self.history.append({"k": k, "error": str(exc), "seconds": round(slice_, 1)})
            return None
        if lay.status == "OPTIMAL":
            self.proven[k] = lay.crossings
        self.history.append({"k": k, "status": lay.status, "crossings": lay.crossings,
                             "lower_bound": lay.lower_bound, "seconds": round(lay.seconds, 2)})
        self.offer(lay, f"{k} columns")
        self.emit("width", k=k, status=lay.status, crossings=lay.crossings)
        return lay

    def width_proven(self) -> int | None:
        """Largest proven width whose optimum equals the best count (Lemma 1)."""
        ws = [w for w, v in self.proven.items() if v == self.best.crossings and w >= len(self.best.orders)]
        return max(ws) if ws else None

    def done(self) -> bool:
        b = self.best
        return b is not None and (b.crossings == 0 or self.lb["bound"] >= b.crossings
                                  or (self.width_proven() or 0) >= self.n)


def solve(schema: Schema, k: int | None = None, time_limit: float = 60.0, workers: int = 8,
          k_max: int | None = None, certify_time: float | None = None, on_progress=None,
          reorder_rows: bool = False, pin_pk: bool = True) -> dict:
    """k given -> that column budget only.  k None -> anytime search (module doc).
    on_progress(event) receives {"kind": "improved"|"width"|"bound"|"phase", "elapsed", ...};
    "improved" events carry the unverified candidate under "layout"."""
    S = _Search(schema, time_limit, workers, on_progress, reorder_rows, pin_pk)
    n = S.n
    base = heuristic(schema) if schema.solver_edges() else None
    if base is None:
        lay = ExactSolver(schema, 1).solve(time_limit=1, workers=1)
        S.offer(lay, "trivial")
    else:
        S.offer(base, "baseline")

    def bound_phase(share):
        if base is None or S.done() or S.left() <= 2:
            return
        S.emit("phase", phase="bound")
        budget = S.left() * share if certify_time is None else min(certify_time, S.left())
        if budget >= 2:
            lb = lower_bound(schema, S.best, budget, workers, attempt_cap=max(10.0, 0.15 * time_limit),
                             should_stop=lambda: S.left() < 1, reorder_rows=reorder_rows, pin_pk=pin_pk)
            if lb["bound"] > S.lb["bound"] or lb["complete"]:
                S.lb = lb
            S.emit("bound", bound=S.lb["bound"], complete=S.lb["complete"])

    if k is not None:
        S.emit("phase", phase="solve", k=k)
        S.run_width(k, max(1.0, S.left() * (0.7 if certify_time is None else 1.0)))
    elif base is not None:
        kb = max(2, len(base.orders))
        top = min(n, k_max or n)
        # 1. main: one long fresh run at a wide width.  Fewer columns force more
        #    crossings, and short slices are wasted on large models (presolve alone
        #    can take ~40 s), so the time goes to one width that is likely good.
        main = max(kb, -(-n // 3))
        main = min(main, top)
        S.emit("phase", phase="main", k=main)
        S.run_width(main, S.left() * 0.45, hint=False)
        # a proven width is often certified globally in seconds (Lemma 3): try that first
        if main in S.proven:
            bound_phase(0.3)
        # 2. wider: check two more columns with a fresh run
        if not S.done() and main + 2 <= top and S.left() > 5:
            S.emit("phase", phase="wider", k=main + 2)
            S.run_width(main + 2, S.left() * 0.5, hint=False)
    # 3. bound: capped sub-schema proofs around the best drawing (Lemma 3)
    bound_phase(0.8)
    if k is None and base is not None and S.left() > 2:
        # 4. compact: narrower widths with the same number of crossings
        S.emit("phase", phase="compact")
        w = len(S.best.orders) - 1
        while w >= 2 and S.left() > 2:
            lay = S.run_width(w, min(S.left(), max(5.0, 0.05 * time_limit)), cap=S.best.crossings)
            if lay is None or lay.crossings > S.best.crossings:
                break
            w -= 1

    best = S.best
    recount, check, svg = verify_and_render(schema, best)
    wp = S.width_proven()
    cert = S.lb if S.lb["bound"] >= best.crossings > 0 else None
    global_opt = best.crossings == 0 or cert is not None or (wp or 0) >= n
    status = "OPTIMAL" if (global_opt or wp is not None) else "FEASIBLE"
    lb = best.crossings if global_opt else S.lb["bound"]
    return {
        "crossings": best.crossings,
        "status": status,
        "global_optimal": global_opt,
        "lower_bound": lb,
        "k": wp or len(best.orders),
        "columns_used": len(best.orders),
        "span": best.span,
        "guarantee": guarantee(best.crossings, len(best.orders), wp, lb, cert, n),
        "certificate": ({"tables": cert["tables"], "forced_crossings": cert["bound"],
                         "edges": cert["edges"], "attempts": cert["attempts"]} if cert else None),
        "lower_bound_search": {k2: S.lb.get(k2) for k2 in ("bound", "tables", "complete", "attempts")},
        "history": S.history,
        "verification": {"model_recount": recount, **check},
        "baseline": {"method": "longest-path + barycenter", "crossings": base.crossings if base else 0,
                     "columns": len(base.orders) if base else 1},
        "seconds": round(S.elapsed(), 2),
        "time_to_best": round(S.best_at, 2),
        "layout": {"columns": best.columns, "orders": [[[kind, ref] for kind, ref in o] for o in best.orders]},
        "row_order": best.rows or {},
        "rows_reordered": reorder_rows,
        "svg": svg,
    }


def baseline_svg(schema: Schema) -> tuple[dict, str]:
    lay = heuristic(schema)
    geo = assign(schema, lay)
    return {"crossings": lay.crossings, "geo": geometric_check(schema, geo)}, render_svg(schema, geo)
