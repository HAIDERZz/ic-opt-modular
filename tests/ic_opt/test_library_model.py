"""T13.2: the ported StratumGP and DomainGuard, with the T13.0 fixes (unfitted levels, per-level hull dims, sigma calibration)."""
from __future__ import annotations

import numpy as np
import pytest

from ic_opt.library import domain, gp

DIMS = ["od", "w", "s", "nt"]
RANGES = {"od": (60.0, 240.0), "w": (4.0, 10.0), "s": (2.0, 4.0), "nt": (1.0, 5.0)}


def grid(nts=(1, 2, 3), per_level=30, seed=0) -> tuple[np.ndarray, np.ndarray]:
    """A smooth positive response on a jittered grid; nt=1 rows all have s=2 (single turns ignore spacing)."""
    rng = np.random.default_rng(seed)
    rows = []
    for nt in nts:
        for _ in range(per_level):
            s = 2.0 if nt == 1 else rng.uniform(2, 4)
            rows.append([rng.uniform(80, 220), rng.uniform(4, 10), s, nt])
    x = np.array(rows)
    y = 1e-10 * x[:, 3] ** 2 * (x[:, 0] / 100) ** 1.4 * (6 / x[:, 1]) ** 0.2 * (1 - 0.03 * (x[:, 2] - 2))
    return x, y


def test_per_nt_answers_fitted_levels_and_returns_nan_elsewhere():
    x, y = grid(nts=(1, 2, 3))
    keep = ~((x[:, 3] == 3) & (np.arange(len(x)) % 3 != 0))                     # level 3 left with 10 rows: below the floor
    model = gp.StratumGP(dims=DIMS, ranges=RANGES, log_target=True, nt_mode="per_nt", kernel="matern52", nt_dim="nt").fit(x[keep], y[keep])
    assert model.unavailable_nt == {3: 10}
    q = np.array([[150, 6, 2, 1], [150, 6, 3, 2], [150, 6, 3, 3], [150, 6, 3, 2.5]])
    assert model.available(q).tolist() == [True, True, False, False]
    mu, sigma = model.predict(q)
    assert np.isfinite(mu[:2]).all() and np.isnan(mu[2:]).all() and np.isnan(sigma[2:]).all()
    truth = 1e-10 * q[1, 3] ** 2 * 1.5 ** 1.4 * 1.0 * (1 - 0.03)
    assert mu[1] == pytest.approx(truth, rel=2e-2)
    lo, hi = model.predict_bounds(q[:2], 2.0)
    wide = gp.StratumGP(dims=DIMS, ranges=RANGES, log_target=True, nt_mode="per_nt", kernel="matern52", nt_dim="nt", k_scale=2.0).fit(x[keep], y[keep])
    lo2, hi2 = wide.predict_bounds(q[:2], 2.0)
    assert (lo2 < lo).all() and (hi2 > hi).all() and (lo > 0).all()           # log-space bounds stay positive and k_scale widens them


def test_holdout_reports_errors_coverage_and_skips_unfitted_levels():
    x, y = grid(nts=(1, 2), per_level=40)
    r = gp.holdout(x, y, seeds=(0, 1), dims=DIMS, ranges=RANGES, log_target=True, nt_mode="per_nt", kernel="matern52", nt_dim="nt")
    assert r["n"] == 80 and r["n_scored"] == 2 * 16 and r["skipped_unfitted_level"] == 0
    assert r["median_rel"] < 0.02 and 0 <= r["coverage_2sigma"] <= 1 and set(r["median_rel_by_level"]) == {1, 2} and len(r["z"]) == 32
    assert gp.calibration_scale({"z": [0.1, 0.5, 1.0]}) == 1.0                  # never narrower than the GP's own interval
    assert gp.calibration_scale({"z": list(np.linspace(0, 5, 101))}) == pytest.approx(np.quantile(np.linspace(0, 5, 101), 0.95) / 2)
    assert gp.calibration_scale({"z": []}) == 1.0
    test, train = gp.split(10, 0)
    assert len(test) == 2 and len(set(test) | set(train)) == 10


def test_guard_uses_each_levels_varying_dims():
    x, _ = grid(nts=(1, 2), per_level=40)
    guard = domain.DomainGuard(x, DIMS, RANGES, nt_dim="nt")
    for row in x:                                                              # every library row is in domain, single turns included
        assert guard.check(dict(zip(DIMS, row))).ok
    inside_nt1 = {"od": float(np.median(x[x[:, 3] == 1, 0])), "w": 7.0, "s": 2.0, "nt": 1}
    assert guard.check(inside_nt1).ok and len(guard.check(inside_nt1).nearest) == 3
    with pytest.raises(domain.OutOfDomainError, match="s is 2 for every row at nt=1") as exc:
        guard.check({**inside_nt1, "s": 3.0})
    assert exc.value.criterion == 1 and exc.value.fill_points[0]["s"] == 3.0
    cases = [({"od": 50, "w": 7, "s": 3, "nt": 2}, 1), ({"od": 150, "w": 7, "s": 3, "nt": 1.5}, 2), ({"od": 150, "w": 7, "s": 3, "nt": 5}, 1)]
    for params, criterion in cases:
        with pytest.raises(domain.OutOfDomainError) as exc:
            guard.check(params)
        assert exc.value.criterion == criterion


def test_guard_rejects_thin_levels_holes_and_degenerate_hulls():
    x, _ = grid(nts=(2,), per_level=40)
    corner = x[:, 0].min() + 1, x[:, 1].max() - 0.01            # achieved box corner: inside per-dim ranges, outside the hull
    guard = domain.DomainGuard(x, DIMS, RANGES, nt_dim="nt")
    with pytest.raises(domain.OutOfDomainError, match="convex hull") as exc:
        guard.check({"od": corner[0], "w": corner[1], "s": float(x[:, 2].min()) + 0.01, "nt": 2})
    assert exc.value.criterion == 3
    thin = domain.DomainGuard(x[:10], DIMS, RANGES, nt_dim="nt")
    with pytest.raises(domain.OutOfDomainError, match="has 10 rows") as exc:
        thin.check(dict(zip(DIMS, x[0])))
    assert exc.value.criterion == 2
    line = np.array([[100 + i, 5 + 0.1 * i, 2 + 0.01 * i, 2] for i in range(30)])            # collinear: no hull in 3 varying dims
    with pytest.raises(domain.OutOfDomainError, match="degenerate hull"):
        domain.DomainGuard(line, DIMS, RANGES, nt_dim="nt").check(dict(zip(DIMS, line[5])))
    flat = domain.DomainGuard(x[:, :3], DIMS[:3], {d: RANGES[d] for d in DIMS[:3]})         # no turns dim: one global hull
    assert flat.check(dict(zip(DIMS[:3], x[0, :3]))).ok
    assert domain.sigma_ok([1.0, 1.0, 0.0], [0.1, 0.2, 0.1]).tolist() == [True, False, False]


def test_sigma_is_floored_at_a_fraction_of_the_mean():
    """The library floors each model's sigma at its held-out median relative error: an answer never claims to be more
    certain than the model's typical error (the T13.6 sign-off found |z| 7-9 at 0.2-0.4 % error on box-edge designs)."""
    rng = np.random.default_rng(3)
    x = rng.uniform(0, 1, (60, 2))
    y = np.exp(1 + x[:, 0] + 0.5 * x[:, 1])
    ranges = {"a": (0.0, 1.0), "b": (0.0, 1.0)}
    plain = gp.StratumGP(dims=["a", "b"], ranges=ranges, log_target=True, kernel="matern52").fit(x, y)
    floored = gp.StratumGP(dims=["a", "b"], ranges=ranges, log_target=True, kernel="matern52", sigma_floor_rel=0.02).fit(x, y)
    mu, sigma = plain.predict(x[:10])
    mu_f, sigma_f = floored.predict(x[:10])
    assert np.allclose(mu, mu_f) and (sigma < 0.02 * mu).any()                   # in-sample the GP is (over)confident
    assert np.all(sigma_f >= 0.02 * np.abs(mu_f) - 1e-12) and np.all(sigma_f >= sigma)
    _lo, hi = floored.predict_bounds(x[:10], 2.0)
    assert np.all(hi / mu_f >= np.exp(2 * 0.02) - 1e-9)                          # the 2-sigma interval is at least +-2 x 2 %


def test_hull_membership_matches_a_simplex_walk_and_keeps_vertices_inside():
    """in_hull is the half-space form of the same convex hull Delaunay.find_simplex walks: identical verdicts on
    random points around a 4-D cloud, and every hull vertex counts as inside (boundary tolerance)."""
    from scipy.spatial import ConvexHull, Delaunay

    rng = np.random.default_rng(3)
    pts = rng.uniform(0, 1, size=(300, 4)) ** 1.5
    hull, tri = ConvexHull(pts), Delaunay(pts)
    probe = rng.uniform(-0.2, 1.2, size=(5000, 4))
    assert (domain.in_hull(hull, probe) == (tri.find_simplex(probe) >= 0)).all()
    assert domain.in_hull(hull, pts[hull.vertices]).all()
    assert not domain.in_hull(hull, np.full((1, 4), 1.5))[0]
