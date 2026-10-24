# erlayout

[![CI](https://github.com/ore-codes/erlayout/actions/workflows/ci.yml/badge.svg)](https://github.com/ore-codes/erlayout/actions/workflows/ci.yml)

Lay out an ER diagram (SQL DDL, DBML or JSON) with the **fewest possible line
crossings, and a proof**. erlayout chooses the columns and the vertical order
of every table jointly with an exact constraint solver (OR-Tools CP-SAT). It
returns either a layout proven optimal or the best one found together with a
proven lower bound.

Author: Oreoluwa Adeleye.

## What "optimal" means here

Tables sit in columns; every foreign-key line attaches to its exact column
row; long lines pass between tables. Within this layered model erlayout
minimises crossings first, then total line length. Every result states which
guarantee it carries:

| Guarantee | Meaning |
| --- | --- |
| Proven optimal: 0 crossings | Nothing can beat it. |
| Proven optimal over every layered drawing (certificate) | A small sub-schema that forces the same number of crossings in *any* drawing is listed with the result. |
| Proven optimal among drawings with at most K columns | Wider drawings were not ruled out. |
| Best found, lower bound L | The time budget ran out first. |

Every result is re-checked by two verifiers that share no code with the solver: a
combinatorial recount and a geometric intersection test on the drawn curves.

## Install

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .            # Python 3.10+
pip install -e ".[test]"    # with test dependencies
```

`uv.lock` pins every dependency; `uv sync --locked --extra test` installs
exactly those versions (this is what CI and the Docker image use).

Use a fresh virtual environment rather than a conda base environment. In
Anaconda, OR-Tools imports pandas, pandas loads pyarrow, and pyarrow brings in
conda's own `libprotobuf`; OR-Tools then loads its bundled copy and the process
aborts with `File already exists in database`. If pip reports
`ResolutionImpossible`, install `ortools` on its own first, then the package.

## Use

```bash
erlayout layout schema.sql -o diagram.svg --time 300 --json result.json
erlayout layout schema.sql --k 5          # fix the number of columns
erlayout serve --port 8000                # web interface at http://127.0.0.1:8000
```

Options: `--format sql|dbml|json` (auto-detected), `--k-max`, `--certify-time`,
`--workers` (defaults to all cores).

### Docker and Railway

```bash
docker build -t erlayout .
docker run -p 8000:8000 erlayout          # or -e PORT=... to change the port
```

The image serves the web interface on `$PORT` and answers `GET /health`.
`railway.json` points Railway at the Dockerfile and that health check, so a
Railway service created from this repository deploys with no extra settings.
Environment variables for the web server:

| Variable | Meaning | Default (image) |
| --- | --- | --- |
| `ERLAYOUT_MAX_RUNNING` | solves at once; further requests get HTTP 429 | 4 (2) |
| `ERLAYOUT_MAX_TIME` | longest time budget, in seconds, a request may use | 1800 (300) |
| `ERLAYOUT_WORKERS` | CP-SAT threads per solve | all cores (2) |
| `ERLAYOUT_LOG_LEVEL` | log verbosity | `INFO` |

Peak CPU use is about `MAX_RUNNING × WORKERS` cores; override these in Railway's
service variables to trade cost against speed.

### Row order (opt-in)

By default every table keeps its columns in the order the schema declares.
`--reorder-rows` lets erlayout also choose the order of the rows that carry
foreign-key lines (other rows keep their places, the primary key stays on top);
add `--free-pk` to let the primary key move too.  The web interface has the same
three choices under **Row order**.  The new orders are listed in the output.

| Sakila, proven optimum | Crossings |
| --- | --- |
| Row order as declared | 2 |
| Reordered, primary key on top | 1 |
| Reordered freely | 0 |

### Large schemas: one diagram per group (opt-in for small ones)

A schema of hundreds of tables does not fit one readable drawing, and no
solver proves anything about it in reasonable time.  Above 40 tables erlayout
therefore splits it (`--split auto`, the default; `on` and `off` force it):

* tables that reference each other a lot are grouped together, at most
  `--max-group` tables (default 15) per group, and each group is laid out and
  solved as its own diagram, stacked one above the other in the SVG;
* a link between two groups is not drawn: its row shows `→ orders (group 3)`
  in place of the column type;
* tables with no drawn link go into one compact panel at the end.

`--hide-hubs auto` (or a list such as `--hide-hubs users,stores`) also takes
the tables that most others point to out of the groups.  Their links become
row tags (`→ stores (hidden hub)`), which removes the fans of lines such a
table draws.  `auto` picks tables referenced by at least 10 foreign keys and
by at least 5% of all of them.  The web interface has the same choices under
**Split**, **Group size** and **Hide hub links**.  Crossing counts and proofs
are then per diagram; the summary says how many diagrams were proven optimal.

### How automatic mode spends its time

The search is *anytime*: a usable diagram is written to `-o` within about a
second (the heuristic baseline), and the file is rewritten every time a better
layout is found, so you can stop it whenever the result is good enough.

1. **Main run**: one long fresh solve at a wide width (the larger of the
   baseline's width and a third of the table count).
2. **Certificate**: if that width was proven optimal, a small sub-schema proof
   often certifies global optimality in seconds.
3. **Wider**: a fresh solve two columns wider.
4. **Lower bound**: sub-schema proofs, each capped so one hard proof cannot use
   up the budget; the result says how many crossings are unavoidable.
5. **Compact**: narrower widths with the same number of crossings.

The web interface runs the same search as a background job and redraws the
diagram as better layouts arrive.

## Results (2 vCPU machine)

| Schema | Tables / FKs | Baseline crossings | erlayout | Guarantee |
| --- | --- | --- | --- | --- |
| Chinook | 11 / 10 | 1 | 0 | proven optimal |
| Northwind | 14 / 12 | 4 | 0 | proven optimal |
| Sakila (examples/sakila.sql) | 15 / 22 | 8 | 2 | globally optimal, 6-table certificate |

The baseline is the classic Sugiyama pipeline (longest-path layering and
barycenter sweeps) in the same drawing model.

## Layout of the code

| File | Role |
| --- | --- |
| `erlayout/parse.py` | SQL (via sqlglot), DBML and JSON input |
| `erlayout/solver.py` | exact CP-SAT model and two-phase solve |
| `erlayout/certify.py` | sub-schema certificates of global optimality |
| `erlayout/heuristic.py` | barycenter baseline and warm start |
| `erlayout/crossings.py` | independent crossing recount |
| `erlayout/bruteforce.py` | exhaustive optimiser for tests |
| `erlayout/geometry.py` | coordinate LP and geometric verifier |
| `erlayout/render.py` | SVG output |
| `erlayout/split.py` | grouping of large schemas, hub detection, row tags |
| `erlayout/api.py`, `cli.py`, `web.py`, `static/` | entry points |
| `bench/` | benchmark, certificate search and UI test scripts; `bench/out/results.jsonl` holds every run reported in the paper |

## Tests

```bash
pytest
```

The suite compares the solver with exhaustive enumeration on random small
schemas, checks certificates against brute force, checks geometry against the
model, and exercises the parsers, CLI and web API.

## Limits

Optimality is relative to the layered model above (curves between columns,
no foreign key inside one column, lines leave tables on the side facing their
target). Exact solving is practical up to a few dozen tables. For larger
schemas erlayout estimates how long each exact model would take to build and
skips the ones that do not fit the time budget; a schema of several hundred
tables gets the barycenter layout within seconds, labelled as such, when
splitting is turned off.  Splitting gives up the single-drawing guarantee:
each diagram is optimal on its own, and the links between diagrams are tags.
