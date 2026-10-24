# How the solver works

This is a map of the code for contributors: what the drawing model is, how
crossings are counted, how the CP-SAT model encodes them, and how a result
earns each of its guarantees. File and function names are given so you can
follow along.

## 1. The drawing model

erlayout only considers *layered* drawings:

- Every table sits in one of K vertical columns. The tables of a column are
  stacked in a total order, and all of them get the column's width.
- A foreign key joins a row of one table to a row of a table in a
  **different** column. A foreign key between two tables of the same column
  is not allowed. Self-references (`employees.manager_id -> employees.id`) are
  left out of the model; `Schema.solver_edges()` in `model.py` drops them, and
  it draws two keys joining the same two rows only once.
- A line leaves each table on the side that faces the other end: the right
  side of the left table and the left side of the right table.
- A line that skips over columns passes through each skipped column as a thin
  *dummy* item, stacked between the tables of that column.
- In the gap between two neighbouring columns, every line is a curve that
  runs left to right, with its height blended smoothly between its two ends.

The last rule gives a simple fact: two lines in the same gap cross **exactly
once** if their top-to-bottom order is inverted between the two sides of the
gap, and never otherwise. Lines that share a row on one side only touch, so
that is not a crossing.

A layout is therefore purely combinatorial: a column for each table, and an
order of the items (tables and dummies) in each column. In the code this is
`solver.Layout`. Its `orders[c]` lists the items of column `c` from top to
bottom, as `("T", table_name)` or `("D", edge_index)`.

## 2. Counting crossings

On each side of a gap, a line has a *key*:

```
key(edge, column) = (position of the item the edge uses in that column, row offset)
```

The item is the line's source table, its target table, or its dummy. The row
offset is the row the line is attached to, or 0 for a dummy. A pair of lines
spanning gap g crosses when `key(g)` and `key(g+1)` order the pair in opposite
ways. The total is the sum over all gaps.

`crossings.count_crossings` implements exactly this formula and nothing else.
It is the reference that everything else is checked against.

## 3. The CP-SAT model (`solver.ExactSolver._build`)

The model has only Boolean variables, for a fixed number of columns K:

| Variables | Meaning |
| --- | --- |
| `inT[i][c]` | table i is in column c (exactly one per table) |
| `P[i][c]` | table i is in column c or to its left (prefix literals, so "left of" and "right of" are single literals) |
| `dum[j][c]` | edge j has a dummy in column c, i.e. its ends are on opposite sides of c |
| `above[u, v, c]` | item u is above item v in column c; forced false when either item is absent, transitive among present items |
| `rowv[i, a, b]` | with `--reorder-rows`: row a of table i is above row b (transitive, primary key first when pinned) |
| `x[j, k, g]` | edges j and k cross in gap g |

In each column, an edge is *represented* by exactly one of three items: its
source table, its target table, or its dummy. For each pair of edges, each gap
and each combination of representatives on the two sides, a clause sets
`x[j, k, g]` when those items are present and their order is inverted. An edge
is present in two neighbouring columns exactly when it spans that gap, so no
separate "spans" variable is needed.

The objective is `sum(x)`. A few extras keep the search small:

- **Mirror symmetry:** flipping a drawing left to right keeps its crossings,
  so the table with the most links is forced into the left half.
- **Absent items:** pairs involving an absent item are fixed to a single
  value, which removes duplicate solutions.
- **Warm start:** the barycenter layout (section 6) is given as a hint.
  `_complete_hint` turns that partial hint into a full assignment, which
  CP-SAT uses far more reliably.

The model grows with the cube of the items per column (the transitivity
clauses) and the square of the edges (the crossing clauses).
`ExactSolver.build_seconds` estimates the build time. A width whose model
would not build within the time budget is skipped instead of tried.

**Phase 2:** once the minimum crossing count is found, a second solve keeps
crossings at or below it and minimises the total column span of the lines,
so the drawing is compact.

## 4. What a result proves

| Situation | Claim |
| --- | --- |
| CP-SAT reports OPTIMAL at K columns | No drawing with at most K columns has fewer crossings (empty columns are allowed, so this covers every smaller K too). |
| OPTIMAL at K ≥ number of tables | Optimal over **all** layered drawings: no drawing needs more columns than there are tables. |
| A sub-schema certificate | Optimal over all layered drawings, even though the full schema was never solved at full width. |
| Otherwise | Best found, together with the best proven lower bound. |

**Certificates** (`certify.py`) rest on one observation. Take any drawing of
the schema and delete some tables, with their lines. What remains is a valid
drawing of that sub-schema, and it has no more crossings than before. So if a
sub-schema needs c crossings in *every* drawing (proven by solving it at K =
its own size), the full schema needs at least c as well.

`lower_bound` builds such a sub-schema step by step:

1. Start from the tables involved in the best drawing's crossings.
2. Solve the sub-schema exactly at its own width.
3. If the bound is still below the best drawing's crossings, add the outside
   table with the most links into the set, and repeat.

It stops when the bound meets the drawing (a certificate), when a step can't
be proven within its time cap, or when time runs out. For Sakila, a 6-table
sub-schema already forces 2 crossings, which proves the 2-crossing drawing
optimal.

`api.guarantee` turns these facts into the sentence shown with every result.
A stopped or timed-out solve reports FEASIBLE, never OPTIMAL, so stopping
early can't produce a false claim.

## 5. The anytime search (`api.solve`)

Without `--k`, the time budget is spent in phases, and every better layout is
reported as it is found:

1. **Baseline:** the barycenter layout, available in about a second.
2. **Main:** one long solve at a wide K. K is the larger of the baseline's
   width and a third of the table count, narrowed until the model builds in
   time.
3. **Certificate:** if K was proven, a certificate is tried at once; it often
   succeeds within seconds.
4. **Wider:** a fresh solve at K + 2.
5. **Lower bound:** the `lower_bound` search above, each step capped.
6. **Compact:** narrower widths, with crossings capped at the best count.

`solver.Stopper` lets another thread interrupt every running CP-SAT search.
It is used by the web interface's **Stop** button and by Ctrl-C in the CLI.

## 6. The baseline (`heuristic.py`)

The baseline is the classic Sugiyama pipeline in the same drawing model:

1. **Longest-path layering:** referenced tables go to the left; foreign-key
   cycles are broken by ignoring back edges.
2. **Barycenter sweeps:** items are reordered using row-accurate port
   positions, with a few random restarts, keeping the best order seen.

It serves as both the comparison in the README and the solver's warm start.

## 7. Independent checks

Every result is re-checked before it is shown, by two pieces of code that
share nothing with `_build`:

- **Recount:** `crossings.count_crossings` recounts from the definition in
  section 2, and `check_layout` confirms every required item is present.
- **Geometry:** `geometry.assign` places the drawing. A linear program sets
  heights that keep every column's order, and the curves are drawn as Bézier
  paths. `geometric_check` then intersects the actual curves, and also checks
  that no line passes through a table and no two tables overlap.

The tests add a third check: `bruteforce.py` enumerates every layering and
every column order of small random schemas, and the solver's optimum must
match it.

## 8. Large schemas (`split.py`)

Above 40 tables, the schema is split before solving:

1. **Hubs (optional):** hidden hub tables are removed first, and links to them
   become row tags.
2. **Grouping:** tables are grouped by greedy modularity merging
   (Clauset–Newman–Moore) with a size cap. Each group is solved on its own
   with the search above.
3. **Tags:** links between groups become row tags, such as
   `→ orders (group 3)`.
4. **Loose tables:** tables with no drawn link go into one panel.

Proofs then hold per diagram, not for the schema as a whole.

## Changing the model

A change to the drawing model touches several files. For example, letting
lines leave a table on either side means changing all of these:

- the representatives and comparisons in `_build`;
- `crossings.count_crossings`;
- the port coordinates in `geometry.assign`;
- `bruteforce.py`.

The tests compare the solver with brute force and the geometry with the
recount, so a mismatch shows up quickly. Run them with `pytest -q`.
