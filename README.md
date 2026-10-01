# erlayout

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
pip install -e .            # Python 3.10+
pip install -e ".[test]"    # with test dependencies
```

## Use

```bash
erlayout layout schema.sql -o diagram.svg --time 300 --json result.json
erlayout layout schema.sql --k 5          # fix the number of columns
erlayout serve --port 8000                # web interface at http://127.0.0.1:8000
```

Options: `--format sql|dbml|json` (auto-detected), `--k-max`, `--certify-time`,
`--workers` (defaults to all cores).

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
target). Exact solving is practical up to a few dozen tables.
