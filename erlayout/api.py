"""High-level entry point used by the CLI and the web server."""
from __future__ import annotations

import time

from .certify import certify
from .crossings import count_crossings
from .geometry import assign, geometric_check
from .heuristic import heuristic
from .model import Schema
from .render import render_svg
from .solver import ExactSolver, Layout


def guarantee(lay: Layout, n_tables: int, cert: dict | None = None) -> str:
    if cert is not None and lay.status == "OPTIMAL":
        return (f"Proven optimal: {lay.crossings} crossings is the minimum over every layered drawing "
                f"of this schema (all column counts). Certificate: a {len(cert['tables'])}-table "
                f"sub-schema that forces {cert['forced_crossings']} crossings in any drawing.")
    if lay.status != "OPTIMAL":
        return (f"Best found: {lay.crossings} crossings; proven lower bound "
                f"{int(lay.lower_bound)} for drawings with at most {lay.k} columns (time limit reached).")
    if lay.crossings == 0:
        return "Proven optimal: 0 crossings, the best any layout in this model can achieve."
    if lay.k >= n_tables:
        return (f"Proven optimal: {lay.crossings} crossings is the minimum over every "
                f"layered drawing of this schema (all column counts).")
    return (f"Proven optimal among drawings with at most {lay.k} columns: "
            f"no such layout has fewer than {lay.crossings} crossings.")


def solve(schema: Schema, k: int | None = None, time_limit: float = 60.0,
          workers: int = 8, k_max: int | None = None, certify_time: float | None = None) -> dict:
    """k given -> solve exactly that column budget.
    k None -> iterative deepening k = 2, 3, ... until a proven 0, the time
    budget, or k_max (default: number of tables, which covers all
    layerings)."""
    t0 = time.time()
    n = len(schema.tables)
    base = heuristic(schema) if schema.solver_edges() else None
    history = []
    best = None
    cert = None
    cert_tries = []
    ks = [k] if k is not None else list(range(2, (k_max or n) + 1))
    for kk in ks:
        left = time_limit - (time.time() - t0)
        if left <= 1 and best is not None:
            break
        hint = base if (base is not None and len(base.orders) <= kk) else None
        try:
            lay = ExactSolver(schema, kk).solve(time_limit=max(left, 1.0), workers=workers, hint=hint)
        except RuntimeError as exc:
            history.append({"k": kk, "error": str(exc)})
            continue
        history.append({"k": kk, "status": lay.status, "crossings": lay.crossings,
                        "lower_bound": lay.lower_bound, "seconds": round(lay.seconds, 2)})
        if best is None or lay.crossings < best.crossings or (
                lay.crossings == best.crossings and best.status != "OPTIMAL" and lay.status == "OPTIMAL"):
            best = lay
        if lay.status == "OPTIMAL" and lay.crossings == 0:
            break
        if lay.status != "OPTIMAL":
            break
        prev = [h for h in history[:-1] if h.get("status") == "OPTIMAL"]
        plateau = bool(prev) and prev[-1]["crossings"] == lay.crossings
        if k is None and lay is best and kk < n and (lay.crossings <= 3 or plateau):
            # few crossings left, or adding a column did not help: try to
            # certify this optimum globally before paying for a larger K
            left = time_limit - (time.time() - t0)
            budget = min(left, max(10.0, 0.25 * left)) if certify_time is None else min(certify_time, left)
            if budget >= 2:
                cert = certify(schema, lay, budget, workers)
                cert_tries.append({"k": kk, "found": cert is not None})
                if cert is not None:
                    break
    if best is None:
        raise RuntimeError("no layout found")
    # OPT(K) is non-increasing in K, so if a larger budget was proven to have
    # the same optimum, the (narrower) drawing we keep is optimal for it too.
    scope = best.k
    for h in history:
        if h.get("status") == "OPTIMAL" and h["crossings"] == best.crossings and best.status == "OPTIMAL":
            scope = max(scope, h["k"])
    best.k = scope
    if cert is not None and cert["forced_crossings"] < best.crossings:
        cert = None
    if cert is None and best.status == "OPTIMAL" and best.crossings > 0 and best.k < n:
        budget = certify_time if certify_time is not None else time_limit - (time.time() - t0)
        if budget >= 2:
            cert = certify(schema, best, budget, workers)
    recount = count_crossings(schema, best.columns, best.orders)
    if best.status == "OPTIMAL":
        if recount != best.crossings:
            raise AssertionError(f"solver objective {best.crossings} != model recount {recount}")
    else:
        # a non-optimal objective may over-count; report the true count of the drawing
        best.crossings = recount
    geo = assign(schema, best)
    check = geometric_check(schema, geo)
    if check["crossings"] != recount or check["edges_through_tables"] or check["table_overlaps"]:
        raise AssertionError(f"verification failed: solver={best.crossings} recount={recount} geo={check}")
    out = {
        "crossings": best.crossings,
        "status": best.status,
        "lower_bound": best.lower_bound,
        "k": best.k,
        "columns_used": len(best.orders),
        "span": best.span,
        "guarantee": guarantee(best, n, cert),
        "certificate": cert,
        "certificate_attempts": cert_tries,
        "history": history,
        "verification": {"model_recount": recount, **check},
        "baseline": {"method": "longest-path + barycenter", "crossings": base.crossings if base else 0,
                     "columns": len(base.orders) if base else 1},
        "seconds": round(time.time() - t0, 2),
        "layout": {"columns": best.columns,
                   "orders": [[[kind, ref] for kind, ref in o] for o in best.orders]},
        "svg": render_svg(schema, geo),
    }
    return out


def baseline_svg(schema: Schema) -> tuple[dict, str]:
    lay = heuristic(schema)
    geo = assign(schema, lay)
    return {"crossings": lay.crossings, "geo": geometric_check(schema, geo)}, render_svg(schema, geo)
