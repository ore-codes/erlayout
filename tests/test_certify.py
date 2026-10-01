import random

import pytest

from erlayout.api import solve
from erlayout.bruteforce import brute_force
from erlayout.certify import certify, induced
from erlayout.model import Column, Edge, Schema, Table
from erlayout.solver import ExactSolver
from test_solver import random_schema


def twist_plus_chain() -> Schema:
    a = Table("A", [Column("x"), Column("y"), Column("id")])
    b = Table("B", [Column("p"), Column("q")])
    rest = [Table(n, [Column("id"), Column("a_id")]) for n in ("C", "D", "E")]
    edges = [Edge("A", "x", "B", "q"), Edge("A", "y", "B", "p"),
             Edge("C", "a_id", "A", "id"), Edge("D", "a_id", "C", "id"), Edge("E", "a_id", "D", "id")]
    return Schema([a, b] + rest, edges).validate()


def test_certificate_found_for_embedded_twist():
    res = solve(twist_plus_chain(), time_limit=60, workers=1)
    assert res["crossings"] == 1 and res["status"] == "OPTIMAL"
    assert res["certificate"] is not None
    assert set(res["certificate"]["tables"]) >= {"A", "B"}
    assert "Certificate" in res["guarantee"]


@pytest.mark.parametrize("seed", range(60))
def test_certificates_are_sound(seed):
    """Whenever a certificate is issued from a K=2 optimum, the true global
    optimum (brute force over all K = n column drawings) must equal it."""
    rng = random.Random(7000 + seed)
    s = random_schema(rng, 4, rng.choice([4, 5]), max_cols=5)
    try:
        lay = ExactSolver(s, 2).solve(time_limit=30, workers=1)
    except RuntimeError:
        pytest.skip("not 2-colourable")
    if lay.crossings == 0:
        pytest.skip("nothing to certify")
    cert = certify(s, lay, time_limit=60, workers=1)
    glob, _ = brute_force(s, len(s.tables), limit=3_000_000)
    if cert is not None:
        assert cert["forced_crossings"] == lay.crossings == glob
        sub = induced(s, cert["tables"])
        assert brute_force(sub, len(sub.tables), limit=3_000_000)[0] >= cert["forced_crossings"]
    else:
        assert glob < lay.crossings
