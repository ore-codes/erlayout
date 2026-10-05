import random

import pytest

from erlayout.bruteforce import brute_force
from erlayout.crossings import count_crossings
from erlayout.geometry import assign, geometric_check
from erlayout.heuristic import heuristic
from erlayout.model import Column, Edge, Schema, Table
from erlayout.solver import ExactSolver


def random_schema(rng: random.Random, n: int, m: int, max_cols: int = 4) -> Schema:
    tables = [Table(f"T{i}", [Column("id", "bigint", True)] +
                    [Column(f"c{j}", "bigint") for j in range(rng.randint(1, max_cols - 1))])
              for i in range(n)]
    edges = set()
    tries = 0
    while len(edges) < m and tries < 500:
        tries += 1
        a, b = rng.sample(range(n), 2)
        sc = rng.choice(tables[a].columns).name
        edges.add(Edge(tables[a].name, sc, tables[b].name, "id"))
    return Schema(tables, sorted(edges, key=lambda e: e.label)).validate()


def twist() -> Schema:
    """Two FKs whose row orders are inverted: one crossing is unavoidable."""
    a = Table("A", [Column("x"), Column("y")])
    b = Table("B", [Column("p"), Column("q")])
    return Schema([a, b], [Edge("A", "x", "B", "q"), Edge("A", "y", "B", "p")]).validate()


def chain() -> Schema:
    ts = [Table(f"T{i}", [Column("id"), Column("next")]) for i in range(5)]
    es = [Edge(f"T{i}", "next", f"T{i+1}", "id") for i in range(4)]
    return Schema(ts, es).validate()


@pytest.mark.parametrize("k", [2, 3, 4])
def test_twist_forces_one_crossing(k):
    lay = ExactSolver(twist(), k).solve(time_limit=30, workers=1)
    assert lay.status == "OPTIMAL" and lay.crossings == 1


def test_chain_is_crossing_free():
    lay = ExactSolver(chain(), 3).solve(time_limit=30, workers=1)
    assert lay.status == "OPTIMAL" and lay.crossings == 0


def test_twist_brute_force_agrees():
    assert brute_force(twist(), 3)[0] == 1


CASES = [(seed, n, m, k) for seed in range(30)
         for (n, m, k) in [(3, 4, 2), (4, 5, 2), (4, 5, 3), (5, 5, 3)]]


@pytest.mark.parametrize("seed,n,m,k", CASES)
def test_exact_matches_brute_force(seed, n, m, k):
    rng = random.Random(seed * 1000 + n * 10 + m + k)
    s = random_schema(rng, n, m)
    try:
        expected, _ = brute_force(s, k, limit=400_000)
    except RuntimeError as exc:
        if "no feasible" in str(exc):
            with pytest.raises(RuntimeError):
                ExactSolver(s, k).solve(time_limit=30, workers=1)
            return
        pytest.skip(str(exc))
    lay = ExactSolver(s, k).solve(time_limit=60, workers=1)
    assert lay.status == "OPTIMAL"
    assert lay.crossings == expected
    assert count_crossings(s, lay.columns, lay.orders) == expected


@pytest.mark.parametrize("seed", range(12))
def test_geometry_agrees_with_model(seed):
    rng = random.Random(seed)
    s = random_schema(rng, rng.randint(5, 8), rng.randint(6, 11), max_cols=6)
    k = 3
    try:
        lay = ExactSolver(s, k).solve(time_limit=60, workers=1)
    except RuntimeError:
        pytest.skip("infeasible in 3 columns (odd cycle)")
    geo = assign(s, lay)
    chk = geometric_check(s, geo)
    assert chk["crossings"] == lay.crossings == count_crossings(s, lay.columns, lay.orders)
    assert chk["edges_through_tables"] == 0 and chk["table_overlaps"] == 0


@pytest.mark.parametrize("seed", range(8))
def test_exact_never_worse_than_heuristic(seed):
    rng = random.Random(100 + seed)
    s = random_schema(rng, 7, 9, max_cols=5)
    h = heuristic(s)
    lay = ExactSolver(s, len(h.orders)).solve(time_limit=60, workers=1, hint=h)
    assert lay.crossings <= h.crossings
    geo = assign(s, h)
    assert geometric_check(s, geo)["crossings"] == h.crossings


# ------------------------------------------------------------ row reordering
@pytest.mark.parametrize("seed", range(40))
def test_reorder_matches_brute_force(seed):
    rng = random.Random(9000 + seed)
    s = random_schema(rng, rng.choice([3, 4]), rng.choice([4, 5]), max_cols=4)
    k = 3
    for pin in (True, False):
        try:
            expected, _ = brute_force(s, k, limit=600_000, reorder_rows=True, pin_pk=pin)
        except RuntimeError as exc:
            if "no feasible" in str(exc):
                return
            pytest.skip(str(exc))
        lay = ExactSolver(s, k, reorder_rows=True, pin_pk=pin).solve(time_limit=60, workers=1)
        assert lay.status == "OPTIMAL"
        assert lay.crossings == expected
        s2 = s.with_row_order(lay.rows)
        assert count_crossings(s2, lay.columns, lay.orders) == expected
        geo = geometric_check(s2, assign(s2, lay))
        assert geo["crossings"] == expected and geo["edges_through_tables"] == 0
        # reordering can only help
        assert expected <= brute_force(s, k, limit=600_000)[0]


def test_reorder_removes_forced_twist():
    lay = ExactSolver(twist(), 2, reorder_rows=True, pin_pk=False).solve(time_limit=30, workers=1)
    assert lay.status == "OPTIMAL" and lay.crossings == 0 and lay.rows


def test_reorder_keeps_pk_first_and_non_port_rows():
    rng = random.Random(77)
    for _ in range(10):
        s = random_schema(rng, 5, 7, max_cols=6)
        try:
            lay = ExactSolver(s, 3, reorder_rows=True).solve(time_limit=30, workers=1)
        except RuntimeError:
            continue
        s2 = s.with_row_order(lay.rows)
        for t, t2 in zip(s.tables, s2.tables):
            assert sorted(c.name for c in t.columns) == sorted(c.name for c in t2.columns)
            assert t2.columns[0].name == t.columns[0].name     # primary key stays on top
