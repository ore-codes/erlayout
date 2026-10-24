# Contributing to erlayout

Thanks for your interest. Bug reports, schemas that lay out badly, docs fixes
and pull requests are all welcome.

## Where to start

Open issues are labelled by size:

- [`good first issue`](https://github.com/ore-codes/erlayout/labels/good%20first%20issue):
  small and self-contained, a good way to learn the code.
- [`medium`](https://github.com/ore-codes/erlayout/labels/medium): touches a
  few modules, or needs a new test fixture.
- [`hard`](https://github.com/ore-codes/erlayout/labels/hard): changes the
  solver model, the certificates or the drawing model.

Comment on an issue before you start on it, so two people don't do the same
work. For anything not covered by an issue, open one first and describe what
you have in mind.

Reporting a layout problem? Attach the schema (SQL, DBML or JSON) or a cut-down
version of it, the command or web settings you used, and the guarantee line
from the output. Please don't post schemas you aren't allowed to share; a few
renamed tables that still show the problem are enough.

## Setting up

Python 3.10 or newer. With [uv](https://docs.astral.sh/uv/):

```bash
uv sync --locked --extra test
```

or with pip:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[test]"
```

Use a fresh virtual environment, not a conda base environment (see *Install*
in the README for why OR-Tools crashes there).

Run the web interface from your checkout with `erlayout serve`.

## Checks

CI runs these on Python 3.10 and 3.13; run them before opening a pull request:

```bash
pytest -q
ruff check .
```

A run of `pytest` reports some skipped tests. Those are random cases that
don't apply to the schema they drew, which is expected.

## Ground rules for changes

- **Every claim is verified.** A layout reported as optimal must also pass the
  combinatorial recount (`crossings.py`) and the geometric check
  (`geometry.py`), which share no code with the solver. Don't weaken either
  verifier to make a test pass.
- **Solver changes need brute-force tests.** `tests/test_solver.py` compares the
  solver with exhaustive enumeration (`bruteforce.py`) on random small
  schemas; extend it when you change the model.
- **Parser changes need fixtures.** Add the input that failed to
  `tests/test_io.py`, with the tables and links it should produce.
- **Exports stay openable.** If you change `export.py`, open the result in
  draw.io, Excalidraw or mermaid.live, not just the tests.
- `erlayout/static/example.sql` (loaded by the web interface) and
  `examples/sakila.sql` are the same file; change both or neither.
- If you change the README images, regenerate them with
  `python bench/readme_images.py`.

Keep pull requests to one change each, with a short description of what it
fixes and how you checked it. Match the style of the surrounding code.

## Releases

The maintainer publishes releases to PyPI from version tags. Please don't
change the version number in a pull request.

## Licence

By contributing you agree that your contribution is released under the
[MIT licence](LICENSE).
