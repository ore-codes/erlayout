"""Exact crossing minimisation for row-anchored layered ER drawings.

Drawing model (see paper, Section 3)
------------------------------------
* Tables are placed in K vertical columns; within a column they are stacked
  in a total order.  Every table of a column has the column's width.
* An FK edge joins a row port of one table to a row port of a table in a
  *different* column.  It leaves each table on the face that points towards
  the other endpoint.  Where it passes a column strictly between its
  endpoints it occupies a thin "dummy" slot in that column's stack.
* In the gap between columns g and g+1 every spanning edge is drawn as a
  curve monotone in x whose vertical position is a convex blend of its
  endpoints; two such curves cross exactly once iff their vertical orders
  at the two sides of the gap are strictly inverted, otherwise never.

So for a layering + per-column orders, the number of crossings is
    sum_g  #{ {e,f} spanning g : key_e(g) < key_f(g) and key_e(g+1) > key_f(g+1) (or vice versa) }
where key_e(c) = (position of the item e uses in column c, row offset).

The CP-SAT model below represents exactly this quantity; minimising it
gives a certified optimum (status OPTIMAL) over all layerings into <= K
columns and all orders.  Phase 2 then minimises total edge span among
crossing-optimal drawings.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from ortools.sat.python import cp_model

from .model import Schema


@dataclass
class Layout:
    """Combinatorial layout: column of each table and top-to-bottom item
    order in each column.  Items are ('T', table_name) or ('D', edge_index)."""
    columns: dict[str, int]
    orders: list[list[tuple[str, object]]]
    crossings: int
    span: int
    status: str = "UNKNOWN"          # OPTIMAL / FEASIBLE / HEURISTIC / ...
    lower_bound: float = 0.0
    seconds: float = 0.0
    k: int = 0
    stats: dict = field(default_factory=dict)
    rows: dict | None = None          # table -> column order, when rows were reordered


def _status_name(code) -> str:
    return {cp_model.OPTIMAL: "OPTIMAL", cp_model.FEASIBLE: "FEASIBLE",
            cp_model.INFEASIBLE: "INFEASIBLE", cp_model.MODEL_INVALID: "MODEL_INVALID",
            cp_model.UNKNOWN: "UNKNOWN"}[code]


class ExactSolver:
    def __init__(self, schema: Schema, k: int | None = None, reorder_rows: bool = False,
                 pin_pk: bool = True):
        """reorder_rows: the vertical order of the rows that carry foreign-key
        lines inside each table becomes part of the optimisation (other rows
        keep their places).  pin_pk keeps the primary key above them."""
        self.reorder = reorder_rows
        self.pin_pk = pin_pk
        self.schema = schema
        self.tables = [t.name for t in schema.tables]
        self.tidx = {n: i for i, n in enumerate(self.tables)}
        self.edges = schema.solver_edges()
        self.n = len(self.tables)
        self.k = k if k is not None else self.n
        # row offset (1-based; 0 reserved for dummies) of each edge end
        self.row_s = [schema.table(e.src_table).row(e.src_col) + 1 for e in self.edges]
        self.row_t = [schema.table(e.dst_table).row(e.dst_col) + 1 for e in self.edges]
        self.R = max([len(t.columns) for t in schema.tables] + [1]) + 2

    # ------------------------------------------------------------------ model
    def _build(self, hint: Layout | None = None, crossing_cap: int | None = None,
               minimize: str = "crossings"):
        """Pure-Boolean encoding.

        * inT[i][c]  table i sits in column c (exactly one c).
        * dum[j][c]  edge j has a dummy slot in column c (lo_j < c < hi_j).
        * above[(u,v,c)] for item pair u<v: u is above v in column c.
          Items are tables ('T',i) and dummies ('D',j).  Forced to 0 when
          either item is absent (removes symmetry), transitive among present
          items.
        * x[j,k,g] crossing indicator; implied by clauses over the
          representative items of both edges at columns g and g+1.  An edge
          is represented at column c by exactly one of: its source table,
          its target table, its dummy -- and is present in two consecutive
          columns iff it spans that gap, so no span literal is needed.
        """
        m = cp_model.CpModel()
        n, K, E = self.n, self.k, len(self.edges)
        cols = range(K)
        es = [self.tidx[e.src_table] for e in self.edges]
        et = [self.tidx[e.dst_table] for e in self.edges]

        inT = [[m.NewBoolVar(f"in_{i}_{c}") for c in cols] for i in range(n)]
        for i in range(n):
            m.AddExactlyOne(inT[i])
        L = [sum(c * inT[i][c] for c in cols) for i in range(n)]

        # lo/hi expressed through prefix literals:  P[i][c] <=> L_i <= c
        P = [[m.NewBoolVar(f"P{i}_{c}") for c in cols] for i in range(n)]
        for i in range(n):
            for c in cols:
                m.Add(sum(inT[i][d] for d in range(c + 1)) == 1).OnlyEnforceIf(P[i][c])
                m.Add(sum(inT[i][d] for d in range(c + 1)) == 0).OnlyEnforceIf(P[i][c].Not())

        dum = [[None] * K for _ in range(E)]
        for j in range(E):
            a, b = es[j], et[j]
            for c in cols:
                m.AddBoolOr([inT[a][c].Not(), inT[b][c].Not()])      # different columns
            for c in range(1, K - 1):
                # dummy in c  <=>  one endpoint left of c and the other right of c
                d = m.NewBoolVar(f"d{j}_{c}")
                # left(a) = P[a][c-1], right(b) = not P[b][c]
                ab = m.NewBoolVar(f"ab{j}_{c}")
                ba = m.NewBoolVar(f"ba{j}_{c}")
                m.AddBoolAnd([P[a][c - 1], P[b][c].Not()]).OnlyEnforceIf(ab)
                m.AddBoolOr([P[a][c - 1].Not(), P[b][c]]).OnlyEnforceIf(ab.Not())
                m.AddBoolAnd([P[b][c - 1], P[a][c].Not()]).OnlyEnforceIf(ba)
                m.AddBoolOr([P[b][c - 1].Not(), P[a][c]]).OnlyEnforceIf(ba.Not())
                m.AddBoolOr([ab, ba]).OnlyEnforceIf(d)
                m.AddImplication(ab, d); m.AddImplication(ba, d)
                dum[j][c] = d

        items = [("T", i) for i in range(n)] + [("D", j) for j in range(E)]

        def present(it, c):
            return inT[it[1]][c] if it[0] == "T" else dum[it[1]][c]

        above = {}
        for c in cols:
            live = [it for it in items if present(it, c) is not None]
            idx = {it: q for q, it in enumerate(live)}
            for p_ in range(len(live)):
                for q in range(p_ + 1, len(live)):
                    u, v = live[p_], live[q]
                    bv = m.NewBoolVar(f"a{u}{v}{c}")
                    # canonical value when either absent
                    m.AddImplication(present(u, c).Not(), bv.Not())
                    m.AddImplication(present(v, c).Not(), bv.Not())
                    above[(u, v, c)] = bv

            def ab_(u, v, idx=idx, c=c):
                if idx[u] < idx[v]:
                    return above[(u, v, c)]
                return above[(v, u, c)].Not()

            for p_ in range(len(live)):
                for q in range(p_ + 1, len(live)):
                    for r in range(q + 1, len(live)):
                        u, v, w = live[p_], live[q], live[r]
                        pres = [present(u, c).Not(), present(v, c).Not(), present(w, c).Not()]
                        # forbid cycles u>v>w>u and u<v<w<u
                        m.AddBoolOr(pres + [ab_(u, v).Not(), ab_(v, w).Not(), ab_(w, u).Not()])
                        m.AddBoolOr(pres + [ab_(v, u).Not(), ab_(w, v).Not(), ab_(u, w).Not()])
            self._ab = getattr(self, "_ab", {})
            self._ab[c] = (idx, ab_)

        # representatives: list of (item, indicator literal, row offset)
        def reps(j, c):
            out = [(("T", es[j]), inT[es[j]][c], self.row_s[j]),
                   (("T", et[j]), inT[et[j]][c], self.row_t[j])]
            if dum[j][c] is not None:
                out.append((("D", j), dum[j][c], 0))
            return out

        TRUE = m.NewConstant(1)
        FALSE = m.NewConstant(0)

        # row reordering: rowv[(i, a, b)] (a < b, 1-based offsets) = row a above row b
        ports = {i: set() for i in range(n)}
        for j in range(E):
            ports[es[j]].add(self.row_s[j])
            ports[et[j]].add(self.row_t[j])
        rowv = {}
        if self.reorder:
            for i in range(n):
                pr = sorted(ports[i])
                for x in range(len(pr)):
                    for y in range(x + 1, len(pr)):
                        rowv[(i, pr[x], pr[y])] = m.NewBoolVar(f"r{i}_{pr[x]}_{pr[y]}")
                lt_ = lambda a, b, i=i: rowv[(i, a, b)] if a < b else rowv[(i, b, a)].Not()
                for x in range(len(pr)):
                    for y in range(x + 1, len(pr)):
                        for z in range(y + 1, len(pr)):
                            a, b, c3 = pr[x], pr[y], pr[z]
                            m.AddBoolOr([lt_(a, b).Not(), lt_(b, c3).Not(), lt_(c3, a).Not()])
                            m.AddBoolOr([lt_(b, a).Not(), lt_(c3, b).Not(), lt_(a, c3).Not()])
                if self.pin_pk:
                    t = self.schema.tables[i]
                    pk = next((r + 1 for r, col in enumerate(t.columns) if col.pk), None)
                    if pk in ports[i]:
                        for o in pr:
                            if o != pk:
                                m.Add(lt_(pk, o) == 1)

        def row_lt(i, a, b):
            return rowv[(i, a, b)] if a < b else rowv[(i, b, a)].Not()

        def rel(ra, rb, c):
            """literals (lt, gt) for key(ra) < key(rb) at column c."""
            (ia, _, roa), (ib, _, rob) = ra, rb
            if ia == ib:
                if roa == rob: return FALSE, FALSE
                if self.reorder and ia[0] == "T":
                    lt = row_lt(ia[1], roa, rob)
                    return lt, lt.Not()
                if roa < rob: return TRUE, FALSE
                if roa > rob: return FALSE, TRUE
                return FALSE, FALSE
            idx, ab_ = self._ab[c]
            lt = ab_(ia, ib)
            return lt, lt.Not()

        cross = []
        for j in range(E):
            for k2 in range(j + 1, E):
                for g in range(K - 1):
                    x = m.NewBoolVar(f"x{j}_{k2}_{g}")
                    ok = False
                    for ra in reps(j, g):
                        for rb in reps(k2, g):
                            lt0, gt0 = rel(ra, rb, g)
                            for ra2 in reps(j, g + 1):
                                if ra2[0] == ra[0] and ra[0][0] == "T":
                                    continue          # same table cannot be in two columns
                                for rb2 in reps(k2, g + 1):
                                    if rb2[0] == rb[0] and rb[0][0] == "T":
                                        continue
                                    lt1, gt1 = rel(ra2, rb2, g + 1)
                                    base = [ra[1].Not(), rb[1].Not(), ra2[1].Not(), rb2[1].Not()]
                                    for p0, p1 in ((lt0, gt1), (gt0, lt1)):
                                        if p0 is FALSE or p1 is FALSE:
                                            continue
                                        lits = list(base)
                                        if p0 is not TRUE: lits.append(p0.Not())
                                        if p1 is not TRUE: lits.append(p1.Not())
                                        m.AddBoolOr(lits + [x])
                                        ok = True
                    if ok:
                        cross.append(x)
                    else:
                        m.Add(x == 0)

        span_terms = []
        for j in range(E):
            # span = |L_a - L_b|
            d = m.NewIntVar(0, K - 1, f"span{j}")
            m.AddAbsEquality(d, L[es[j]] - L[et[j]])
            span_terms.append(d)
        total_cross = sum(cross) if cross else 0
        total_span = sum(span_terms) if span_terms else 0

        if n:
            hub = max(range(n), key=lambda i: sum(1 for j in range(E) if es[j] == i or et[j] == i))
            m.Add(2 * L[hub] <= K - 1)

        if crossing_cap is not None:
            m.Add(total_cross <= crossing_cap)
        if minimize == "crossings":
            m.Minimize(total_cross)
        else:
            m.Minimize(total_span)

        if hint is not None:
            hub_ok = True
            if n:
                hub_ok = 2 * hint.columns[self.tables[hub]] <= K - 1
            width = len(hint.orders)
            def hc(c):
                return c if hub_ok else (width - 1 - c)
            for i, t in enumerate(self.tables):
                for c in cols:
                    m.AddHint(inT[i][c], 1 if hc(hint.columns[t]) == c else 0)
            for c0, order in enumerate(hint.orders):
                c = hc(c0)
                if c >= K:
                    continue
                pos = {}
                for p_, (kind, ref) in enumerate(order):
                    pos[("T", self.tidx[ref]) if kind == "T" else ("D", ref)] = p_
                for (u, v, cc), bv in above.items():
                    if cc == c and u in pos and v in pos:
                        m.AddHint(bv, 1 if pos[u] < pos[v] else 0)

        if hint is not None and self.reorder:
            for (i, a, b), rv in rowv.items():
                t = self.tables[i]
                if hint.rows and t in hint.rows:
                    names = [c.name for c in self.schema.tables[i].columns]
                    pos = {nm: q for q, nm in enumerate(hint.rows[t])}
                    m.AddHint(rv, 1 if pos[names[a - 1]] < pos[names[b - 1]] else 0)
                else:
                    m.AddHint(rv, 1)

        self._vars = dict(inT=inT, dum=dum, above=above, items=items, rowv=rowv, ports=ports)
        return m, total_cross, total_span, len(cross)

    # ---------------------------------------------------------------- extract
    def _extract(self, solver: cp_model.CpSolver) -> tuple[dict, list]:
        v = self._vars
        K = self.k
        columns = {}
        for i, t in enumerate(self.tables):
            columns[t] = next(c for c in range(K) if solver.Value(v["inT"][i][c]))
        orders = []
        for c in range(K):
            live = [("T", i) for i in range(self.n) if columns[self.tables[i]] == c]
            live += [("D", j) for j in range(len(self.edges))
                     if v["dum"][j][c] is not None and solver.Value(v["dum"][j][c])]
            idx, _ = self._ab[c]
            def above_count(u):
                cnt = 0
                for w in live:
                    if w == u: continue
                    a, b = (u, w) if idx[u] < idx[w] else (w, u)
                    val = solver.Value(v["above"][(a, b, c)])
                    if (a == u and val) or (a == w and not val):
                        cnt += 1      # u above w
                return cnt
            counts = {u: above_count(u) for u in list(live)}
            live = sorted(live, key=lambda u: -counts[u])
            orders.append([("T", self.tables[r]) if k == "T" else ("D", r) for k, r in live])
        return columns, orders

    def _extract_rows(self, solver) -> dict | None:
        """New column order per table: the rows that carry lines are put back
        into the slots they occupied before, in the solved order; every other
        row stays where it was."""
        if not self.reorder:
            return None
        v = self._vars
        rows = {}
        for i, t in enumerate(self.tables):
            pr = sorted(v["ports"][i])
            if len(pr) < 2:
                continue
            def wins(a):
                return sum(1 for b in pr if b != a and solver.Value(v["rowv"][(i, min(a, b), max(a, b))]) == (a < b))
            order = sorted(pr, key=lambda a: -wins(a))
            names = [c.name for c in self.schema.tables[i].columns]
            new = list(names)
            for slot, a in zip(pr, order):
                new[slot - 1] = names[a - 1]
            if new != names:
                rows[t] = new
        return rows

    # ------------------------------------------------------------------ solve
    @staticmethod
    def _complete_hint(m: cp_model.CpModel, workers: int, limit: float):
        """The layout hint only names placement and order literals; CP-SAT
        often fails to rebuild the remaining ~95% of the assignment from that.
        Fix the hinted literals in a copy, let propagation fill in the rest
        (dummy slots, crossing indicators, spans), then hint every variable."""
        proto = m.Proto()
        pairs = list(zip(proto.solution_hint.vars, proto.solution_hint.values))
        if not pairs:
            return False
        c = m.clone()
        for idx, val in pairs:
            c.Add(c.GetIntVarFromProtoIndex(idx) == val)
        cs = cp_model.CpSolver()
        cs.parameters.max_time_in_seconds = limit
        cs.parameters.num_workers = workers
        st = cs.Solve(c)
        if st not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return False
        m.ClearHints()
        for i in range(len(proto.variables)):
            v = m.GetIntVarFromProtoIndex(i)
            m.AddHint(v, cs.Value(v))
        return True

    @staticmethod
    def _compact(columns, orders):
        """Drop empty outer columns and renumber from 0."""
        used = [c for c in range(len(orders)) if any(k == "T" for k, _ in orders[c])]
        lo_c, hi_c = min(used), max(used)
        return {t: c - lo_c for t, c in columns.items()}, orders[lo_c:hi_c + 1]

    def solve(self, time_limit: float = 60.0, workers: int = 8, hint: Layout | None = None,
              phase2: bool = True, log: bool = False, cap: int | None = None,
              phase2_time: float | None = None, on_improve=None) -> Layout:
        """cap: only accept drawings with at most `cap` crossings.
        phase2_time: limit for the span-minimisation pass (default: what is left).
        on_improve(layout): called from the solver thread for every improving
        phase-1 solution (crossings = objective value, not yet re-verified)."""
        t0 = time.time()
        if not self.edges:
            cols = {t: 0 for t in self.tables}
            return Layout(cols, [[("T", t) for t in self.tables]], 0, 0, "OPTIMAL", 0, 0.0, self.k)

        m, cross, span, ncross = self._build(hint=hint, crossing_cap=cap)
        if hint is not None:
            self._complete_hint(m, workers, max(1.0, min(15.0, 0.25 * time_limit)))
        s = cp_model.CpSolver()
        p1 = time_limit - (phase2_time if (phase2 and phase2_time) else 0)
        s.parameters.max_time_in_seconds = max(0.5, p1)
        s.parameters.num_workers = workers
        s.parameters.log_search_progress = log
        if on_improve is not None:
            outer = self

            class _CB(cp_model.CpSolverSolutionCallback):
                def on_solution_callback(self):
                    try:
                        cols, ords = outer._extract(self)
                        cols, ords = outer._compact(cols, ords)
                        on_improve(Layout(cols, ords, int(round(self.ObjectiveValue())), 0, "FEASIBLE",
                                          self.BestObjectiveBound(), time.time() - t0, outer.k,
                                          rows=outer._extract_rows(self)))
                    except Exception:  # a progress report must never break the search
                        pass
            st = s.Solve(m, _CB())
        else:
            st = s.Solve(m)
        if st not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            if st == cp_model.INFEASIBLE:
                raise RuntimeError(f"no valid layering with {self.k} columns")
            raise RuntimeError(f"no solution found with {self.k} columns within the time limit")
        best = int(round(s.ObjectiveValue()))
        bound = s.BestObjectiveBound()
        status = _status_name(st)
        columns, orders = self._extract(s)
        rows = self._extract_rows(s)
        stats = {"vars": len(m.Proto().variables), "constraints": len(m.Proto().constraints),
                 "crossing_vars": ncross, "phase1_seconds": round(time.time() - t0, 3),
                 "conflicts": s.NumConflicts(), "branches": s.NumBranches()}
        sp = sum(abs(columns[e.src_table] - columns[e.dst_table]) for e in self.edges)

        left = time_limit - (time.time() - t0)
        if phase2 and left > 0.5:
            hint2 = Layout(columns, orders, best, sp, rows=rows)
            m2, _, _, _ = self._build(hint=hint2, crossing_cap=best, minimize="span")
            s2 = cp_model.CpSolver()
            s2.parameters.max_time_in_seconds = max(0.5, min(left, phase2_time) if phase2_time else left)
            s2.parameters.num_workers = workers
            st2 = s2.Solve(m2)
            if st2 in (cp_model.OPTIMAL, cp_model.FEASIBLE):
                columns, orders = self._extract(s2)
                rows = self._extract_rows(s2)
                sp = int(round(s2.ObjectiveValue()))
                stats["phase2_status"] = _status_name(st2)
                stats["span_lower_bound"] = s2.BestObjectiveBound()

        columns, orders = self._compact(columns, orders)
        return Layout(columns, orders, best, sp, status, bound, time.time() - t0, self.k, stats, rows=rows)
