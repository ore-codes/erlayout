"""High-level entry point used by the CLI and the web server.

Automatic mode is an anytime search:

1. main     - one long fresh run at a wide width: the larger of the baseline's
              width and a third of the table count; every improvement is
              reported as soon as it is found;
2. wider    - a fresh run two columns wider;
3. bound    - grow sub-schemas around the best drawing's crossings to prove a
              lower bound that holds for every layered drawing (certify.py), each
              attempt capped so one hard proof cannot eat the budget;
4. compact  - try narrower widths that reach the same crossing count.
"""
from __future__ import annotations

import logging
import threading
import time

from .certify import lower_bound
from .crossings import count_crossings
from .export import drawing, section
from .geometry import assign, geometric_check
from .heuristic import heuristic
from .model import Schema
from .render import render_svg, stack_svgs
from .solver import ExactSolver, Layout, Stopper
from .split import find_hubs, split_schema

log = logging.getLogger(__name__)


def _n(c: int, word: str = "crossing") -> str:
    return f"{c} {word}" + ("" if c == 1 else "s")


def guarantee(crossings: int, columns: int, width_proven: int | None, lb: int, cert: dict | None,
              n_tables: int, finished: bool = True, too_large: bool = False, stopped: bool = False) -> str:
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
    elif too_large:
        parts.append(f"Heuristic layout: {_n(crossings)} in {_n(columns, 'column')}. The schema is too "
                     f"large for the exact solver in this time budget, so this is the barycenter baseline.")
    else:
        parts.append(f"Best found: {_n(crossings)} in {_n(columns, 'column')}"
                     + (" (stopped early)." if stopped else " (time limit reached)." if finished
                        else " (still searching)."))
    if lb > 0:
        parts.append(f"Every layered drawing has at least {lb}, so at most {crossings - lb} more "
                     f"could be removed.")
    elif width_proven is None:
        parts.append("No lower bound proven yet, so a better drawing may exist.")
    return " ".join(parts)


def verify_and_draw(schema: Schema, lay: Layout) -> tuple[int, dict, str, dict]:
    """Independent recount + geometric check, then the SVG and the drawing
    the editable exports are made from (export.section).  Raises on any
    mismatch.  Uses the layout's own row order when rows were reordered."""
    schema = schema.with_row_order(lay.rows)
    recount = count_crossings(schema, lay.columns, lay.orders)
    geo = assign(schema, lay)
    check = geometric_check(schema, geo)
    if check["crossings"] != recount or check["edges_through_tables"] or check["table_overlaps"]:
        raise AssertionError(f"verification failed: recount={recount} geo={check}")
    svg = render_svg(schema, geo)
    return recount, check, svg, section(schema, geo, svg)


def verify_and_render(schema: Schema, lay: Layout) -> tuple[int, dict, str]:
    return verify_and_draw(schema, lay)[:3]


class _Search:
    def __init__(self, schema: Schema, time_limit: float, workers: int, on_progress,
                 reorder_rows: bool = False, pin_pk: bool = True, stopper: Stopper | None = None):
        self.schema, self.T, self.workers, self.cb = schema, time_limit, workers, on_progress
        self.stopper = stopper
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
        if self.stopper is not None and self.stopper.stopped:
            return 0.0
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
        if self.stopper is not None and self.stopper.stopped:
            return None
        use = None
        with self.lock:
            if hint and self.best is not None and len(self.best.orders) <= k:
                use = self.best
        try:
            lay = ExactSolver(self.schema, k, reorder_rows=self.reorder, pin_pk=self.pin_pk).solve(
                time_limit=slice_, workers=self.workers, hint=use, cap=cap, phase2=phase2,
                phase2_time=min(0.2 * slice_, 30) if phase2 else None,
                on_improve=lambda l: self.offer(l, f"{k} columns"), stopper=self.stopper)
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
        """Largest proven width whose optimum equals the best count.  An optimum at
        K columns covers every narrower width too, since columns may stay empty."""
        ws = [w for w, v in self.proven.items() if v == self.best.crossings and w >= len(self.best.orders)]
        return max(ws) if ws else None

    def done(self) -> bool:
        b = self.best
        return b is not None and (b.crossings == 0 or self.lb["bound"] >= b.crossings
                                  or (self.width_proven() or 0) >= self.n)


def solve(schema: Schema, k: int | None = None, time_limit: float = 60.0, workers: int = 8,
          k_max: int | None = None, certify_time: float | None = None, on_progress=None,
          reorder_rows: bool = False, pin_pk: bool = True, stopper: Stopper | None = None) -> dict:
    """k given -> that column budget only.  k None -> anytime search (module doc).
    on_progress(event) receives {"kind": "improved"|"width"|"bound"|"phase", "elapsed", ...};
    "improved" events carry the unverified candidate under "layout".
    stopper.stop() from another thread ends the search; the best layout so far
    is then verified and returned as usual, with "stopped": True."""
    S = _Search(schema, time_limit, workers, on_progress, reorder_rows, pin_pk, stopper)
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
                             should_stop=lambda: S.left() < 1, reorder_rows=reorder_rows, pin_pk=pin_pk,
                             stopper=stopper)
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
        # large schemas: narrow the width until the model can be built in the slice
        probe = ExactSolver(schema, main)
        while main > kb and probe.build_seconds(main) > 0.5 * S.left() * 0.45:
            main -= 1
        S.emit("phase", phase="main", k=main)
        S.run_width(main, S.left() * 0.45, hint=False)
        # a proven width is often certified globally in seconds: try that first
        if main in S.proven:
            bound_phase(0.3)
        # 2. wider: check two more columns with a fresh run
        if not S.done() and main + 2 <= top and S.left() > 5:
            S.emit("phase", phase="wider", k=main + 2)
            S.run_width(main + 2, S.left() * 0.5, hint=False)
    # 3. bound: capped sub-schema proofs around the best drawing
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
    # every exact run was skipped because its model could not be built in time
    too_large = bool(S.history) and all("to build" in h.get("error", "") for h in S.history) \
        and not (stopper and stopper.stopped)
    recount, check, svg, sec = verify_and_draw(schema, best)
    stopped = bool(stopper and stopper.stopped)
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
        "guarantee": guarantee(best.crossings, len(best.orders), wp, lb, cert, n, too_large=too_large,
                               stopped=stopped),
        "stopped": stopped,
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
        "drawing": drawing([sec]),
    }


def baseline_svg(schema: Schema) -> tuple[dict, str]:
    lay = heuristic(schema)
    geo = assign(schema, lay)
    return {"crossings": lay.crossings, "geo": geometric_check(schema, geo)}, render_svg(schema, geo)


# ------------------------------------------------------------------ split mode
def wants_split(schema: Schema, split: str = "auto", hubs=None, max_group: int = 15) -> bool:
    """split: "on", "off" or "auto" (only when the schema is bigger than a group)."""
    if hubs or split == "on":
        return True
    return split == "auto" and len(schema.tables) > max(40, max_group)


def resolve_hubs(schema: Schema, hubs) -> list[str]:
    """None or "" (no hubs), "auto" (find_hubs) or table names, as a list or a comma string."""
    if hubs in (None, "", [], ()):
        return []
    if hubs == "auto":
        return find_hubs(schema)
    names = [h.strip() for h in (hubs.split(",") if isinstance(hubs, str) else hubs) if h.strip()]
    known = {t.name.lower(): t.name for t in schema.tables}
    missing = [h for h in names if h.lower() not in known]
    if missing:
        raise ValueError(f"unknown hub table(s): {', '.join(missing)}")
    return [known[h.lower()] for h in names]


def _panel(tables: list) -> tuple[str, dict]:
    """Tables with no drawn link, packed into columns of similar height."""
    from .geometry import table_height
    panel = Schema(list(tables), [])
    ncols = max(1, min(8, round((len(tables) / 2) ** 0.5)))
    cols, heights = [[] for _ in range(ncols)], [0.0] * ncols
    for t in sorted(tables, key=table_height, reverse=True):
        c = heights.index(min(heights))
        cols[c].append(t)
        heights[c] += table_height(t)
    cols = [c for c in cols if c]
    lay = Layout({t.name: i for i, c in enumerate(cols) for t in c},
                 [[("T", t.name) for t in sorted(c, key=lambda t: t.name)] for c in cols], 0, 0, "PANEL")
    geo = assign(panel, lay)
    svg = render_svg(panel, geo)
    return svg, section(panel, geo, svg)


def _group_heading(sp_group, res: dict | None, crossings: int) -> str:
    if res is None:
        state = "heuristic, still to be searched"
    elif res["global_optimal"]:
        state = "proven optimal"
    elif res["status"] == "OPTIMAL":
        state = f"optimal for {res['k']} columns"
    else:
        state = f"best found, at least {res['lower_bound']}" if res["lower_bound"] else "best found"
    return f"{sp_group.title}  ·  {_n(crossings)}, {state}"


def _compose(sp, sections, crossings, results, panel=None, arrange="grid") -> tuple[str, dict]:
    """The stacked SVG and the drawing of a split schema."""
    secs = [dict(sections[i], name=g.title, title=_group_heading(g, results[i], crossings[i]))
            for i, g in enumerate(sp.groups)]
    if sp.loose:
        what = "Hidden hubs and tables" if sp.hubs else "Tables"
        name = f"{what} without drawn links"
        secs.append(dict(panel or _panel(sp.loose)[1], name=name, title=f"{name} ({len(sp.loose)})"))
    return stack_svgs([(s["title"], s["svg"]) for s in secs], arrange), drawing(secs, arrange)


def baseline_split(schema: Schema, max_group: int = 15, hubs=None, arrange: str = "grid") -> tuple[dict, str]:
    sp = split_schema(schema, max_group, resolve_hubs(schema, hubs))
    secs, crossings, geo = [], [], {"crossings": 0, "edges_through_tables": 0, "table_overlaps": 0}
    for g in sp.groups:
        lay = heuristic(g.schema)
        gg = assign(g.schema, lay)
        svg = render_svg(g.schema, gg)
        secs.append(section(g.schema, gg, svg))
        crossings.append(lay.crossings)
        for key, v in geometric_check(g.schema, gg).items():
            geo[key] += v
    return {"crossings": sum(crossings), "geo": geo}, _compose(sp, secs, crossings, [None] * len(secs), arrange=arrange)[0]


def solve_split(schema: Schema, time_limit: float = 60.0, workers: int = 8, max_group: int = 15,
                hubs=None, on_progress=None, reorder_rows: bool = False, pin_pk: bool = True,
                arrange: str = "grid", stopper: Stopper | None = None) -> dict:
    """Lay out each group of a split schema (split.py) as its own diagram.
    Every group gets a heuristic drawing at once; the groups are then solved
    one after the other, each with a share of the remaining time in proportion
    to its size.  arrange places the diagrams: "grid" (default), "row" or
    "column" (render.place).  stopper.stop() ends the search; groups not
    searched yet keep their heuristic drawing.  on_progress receives {"kind": "improved", "svg", "drawing", "crossings",
    "elapsed"} with the combined drawing, and {"kind": "phase", ...}."""
    t0 = time.time()
    sp = split_schema(schema, max_group, resolve_hubs(schema, hubs))
    G = len(sp.groups)
    lock = threading.Lock()
    secs, crossings, results = [None] * G, [0] * G, [None] * G
    panel = _panel(sp.loose)[1] if sp.loose else None
    base_total, heuristic_results = 0, []
    for i, g in enumerate(sp.groups):
        lay = heuristic(g.schema)
        crossings[i], check, _, secs[i] = verify_and_draw(g.schema, lay)
        base_total += crossings[i]
        heuristic_results.append({
            "crossings": crossings[i], "status": "HEURISTIC", "global_optimal": False, "lower_bound": 0,
            "k": len(lay.orders), "columns_used": len(lay.orders), "seconds": 0.0, "row_order": {},
            "guarantee": f"Heuristic layout: {_n(crossings[i])}; stopped before this diagram was searched.",
            "verification": {"model_recount": crossings[i], **check}})

    def emit(kind, **kw):
        if on_progress is None:
            return
        try:
            on_progress({"kind": kind, "elapsed": round(time.time() - t0, 1), **kw})
        except Exception:  # a broken listener must not stop the search
            log.exception("progress callback failed on %r event", kind)

    def publish():
        with lock:
            (svg, d), total = _compose(sp, secs, crossings, results, panel, arrange), sum(crossings)
        emit("improved", svg=svg, drawing=d, crossings=total)

    publish()
    weight = [len(g.schema.tables) * max(1, len(g.schema.solver_edges())) for g in sp.groups]
    # smallest first: time a small group does not need goes to the bigger ones after it
    order = sorted(range(G), key=lambda i: weight[i])
    for done, i in enumerate(order):
        if stopper is not None and stopper.stopped:
            break
        g = sp.groups[i]
        left = time_limit - (time.time() - t0)
        share = max(1.0, left * weight[i] / sum(weight[j] for j in order[done:]))
        emit("phase", phase="group", group=i + 1, groups=G)

        def progress(ev, i=i, g=g):
            if ev["kind"] != "improved":
                return
            try:
                c, _, _, sec = verify_and_draw(g.schema, ev["layout"])
            except AssertionError:
                return
            with lock:
                if c >= crossings[i]:
                    return
                crossings[i], secs[i] = c, sec
            publish()

        res = solve(g.schema, time_limit=share, workers=workers, on_progress=progress,
                    reorder_rows=reorder_rows, pin_pk=pin_pk, stopper=stopper)
        with lock:
            results[i], crossings[i], secs[i] = res, res["crossings"], res["drawing"]["sections"][0]
        publish()

    stopped = bool(stopper and stopper.stopped)
    total = sum(crossings)
    svg, d = _compose(sp, secs, crossings, results, panel, arrange)
    results = [r if r is not None else h for r, h in zip(results, heuristic_results)]
    proven = sum(1 for r in results if r["global_optimal"])
    verification = {"model_recount": sum(r["verification"]["model_recount"] for r in results),
                    "crossings": sum(r["verification"]["crossings"] for r in results),
                    "edges_through_tables": sum(r["verification"]["edges_through_tables"] for r in results),
                    "table_overlaps": sum(r["verification"]["table_overlaps"] for r in results)}
    drawn = sum(len(g.schema.tables) for g in sp.groups)
    hidden = f", {_n(len(sp.hubs), 'hub table')} hidden ({', '.join(sp.hubs)})" if sp.hubs else ""
    summary = (f"Split into {_n(G, 'diagram')} ({drawn} tables) plus {_n(len(sp.loose), 'table')} without drawn links"
               f"{hidden}. Links between diagrams are shown as row tags, not lines. "
               f"{proven} of {G} diagrams proven optimal; {_n(total)} in total"
               + (" (stopped early)." if stopped else "."))
    return {
        "split": True,
        "stopped": stopped,
        "crossings": total,
        "status": "OPTIMAL" if proven == G else "FEASIBLE",
        "global_optimal": proven == G,
        "lower_bound": sum(r["lower_bound"] for r in results),
        "columns_used": max([r["columns_used"] for r in results] + [0]),
        "guarantee": summary,
        "groups": [{"title": g.title, "tables": len(g.schema.tables), "edges": len(g.schema.solver_edges()),
                    "crossings": r["crossings"], "global_optimal": r["global_optimal"],
                    "guarantee": r["guarantee"], "seconds": r["seconds"]} for g, r in zip(sp.groups, results)],
        "unlinked_tables": [t.name for t in sp.loose],
        "hidden_hubs": sp.hubs,
        "links_as_tags": len(sp.notes),
        "history": [{"k": f"group {i + 1}", "status": r["status"], "crossings": r["crossings"],
                     "seconds": r["seconds"]} for i, r in enumerate(results)],
        "verification": verification,
        "baseline": {"method": "longest-path + barycenter, per diagram", "crossings": base_total},
        "seconds": round(time.time() - t0, 2),
        "time_to_best": round(time.time() - t0, 2),
        "row_order": {t: o for r in results for t, o in (r.get("row_order") or {}).items()},
        "svg": svg,
        "drawing": d,
    }
