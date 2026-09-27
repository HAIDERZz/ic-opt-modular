"""Run once, by hand, to produce the literal numbers ``icopt_bench/synthetic.py`` hardcodes: the calibrated
constraint thresholds (a quantile search against a large sample of the grid, so that a stated share of it is
feasible) and the "best known" reference objective for the six problems whose grid is too large to enumerate
(``syn_ackley10_c2`` through ``syn_amplifier_like``). Nothing here is imported by the benchmark itself -- it is a
one-off tool, run with ``PYTHONPATH=src:benchmarks path/to/venv/python benchmarks/tools/calibrate_synthetic.py``.

Two small problems (``syn_small_tight``, ``syn_multimodal_small``) are left out of the "best known" part: their
grids (504 and 1089 points) are enumerated exactly inside ``synthetic.py`` itself at problem-build time, which needs
no calibration. ``syn_small_tight`` still needs its five thresholds calibrated here.
"""

from __future__ import annotations

import numpy as np
from icopt_bench import _synthetic_math as m
from icopt_bench._gridsearch import best_known, enumerate_grid, levels, random_grid_points


def calibrate_small_tight() -> None:
    a = levels(0, 5, 1)          # unit-cube index for a (6 levels: 20..30 step 2)
    b = levels(0, 3, 1)          # 4 levels
    c = levels(0, 2, 1)          # 3 levels
    d = levels(0, 6, 1)          # 7 levels
    grid = enumerate_grid([a / 5, b / 3, c / 2, d / 6])          # already in u in [0, 1]
    m1, m2, m3, m4, m5 = m.small_tight_metrics(grid)
    metrics = {"m1": (m1, "gt"), "m2": (m2, "lt"), "m3": (m3, "gt"), "m4": (m4, "gt"), "m5": (m5, "gt")}
    n = grid.shape[0]
    target = round(0.02 * n)
    best_p, best_gap, best_t = None, np.inf, None
    for p in np.linspace(0.005, 0.4, 400):
        t = {}
        feasible = np.ones(n, dtype=bool)
        for name, (values, op) in metrics.items():
            q = 1 - p if op == "gt" else p
            t[name] = float(np.quantile(values, q))
            feasible &= (values > t[name]) if op == "gt" else (values < t[name])
        gap = abs(int(feasible.sum()) - target)
        if gap < best_gap:
            best_p, best_gap, best_t = p, gap, t
    feasible_count = None
    feasible = np.ones(n, dtype=bool)
    for name, (values, op) in metrics.items():
        feasible &= (values > best_t[name]) if op == "gt" else (values < best_t[name])
    feasible_count = int(feasible.sum())
    ranges = {name: float(values.max() - values.min()) for name, (values, _) in metrics.items()}
    print("syn_small_tight: p =", best_p, "thresholds =", best_t)
    print("  feasible", feasible_count, "/", n, f"({100 * feasible_count / n:.2f}%)")
    print("  ranges (for w_i = 0.25 * range):", ranges)


def calibrate_ackley10() -> None:
    dims = [(0, 15, 5)] * 3 + [(0, 15, 2.5)] * 3 + [(0, 15, 0.5)] * 4
    level_arrays = [levels(*d) for d in dims]

    def score(u: np.ndarray) -> np.ndarray:
        x = u - 5
        f = m.ackley10(x)
        c1, c2 = m.ackley10_constraints(x)
        violation = np.maximum(c1, 0) + np.maximum(c2, 0)
        feasible = violation == 0
        return np.where(feasible, f, 1e6 + violation)

    x, rank = best_known(level_arrays, score, seed=0, n_random=200_000, top_k=20, sweeps=10)
    u = x - 5
    c1, c2 = m.ackley10_constraints(u)
    print("syn_ackley10_c2: best f =", rank, "feasible =", bool(c1 <= 0 and c2 <= 0), "c1,c2 =", c1, c2)


def calibrate_hartmann6(bound: float) -> None:
    level_arrays = [levels(0, 1, 0.01)] * 6
    # the continuous unconstrained optimum, snapped to the grid: coordinate descent from pure random starts alone
    # under-shot it (-3.289 instead of -3.291) on a first pass.
    unconstrained = np.round(np.array([0.20169, 0.150011, 0.476874, 0.275332, 0.311652, 0.6573]) / 0.01) * 0.01

    def score(x: np.ndarray) -> np.ndarray:
        f = m.hartmann6(x)
        c = np.sum(x, axis=-1) - bound
        violation = np.maximum(c, 0)
        feasible = violation == 0
        return np.where(feasible, f, 1e6 + violation)

    x, rank = best_known(level_arrays, score, seed=0, n_random=400_000, top_k=40, sweeps=20, extra_starts=[unconstrained])
    print(f"syn_hartmann6_c1 bound={bound}: best f =", rank, "sum(x) =", float(x.sum()), "point =", x)


def calibrate_levy20() -> None:
    level_arrays = [levels(-10, 10, 0.05)] * 20
    # x=0 (feasible, sum=0) and the boundary point x=0.5 everywhere (sum=10): pure random starts missed both, landing
    # on a much worse basin (2.497 instead of 0.925) -- Levy's own optimum (x=1 everywhere) is just outside the grid's
    # feasible region, and coordinate descent from a random point rarely finds its way back to the all-ones corner.
    extra = [np.zeros(20), np.full(20, 0.5)]

    def score(x: np.ndarray) -> np.ndarray:
        f = m.levy20(x)
        c = np.sum(x, axis=-1) / 20 - 0.5
        violation = np.maximum(c, 0)
        feasible = violation == 0
        return np.where(feasible, f, 1e6 + violation)

    x, rank = best_known(level_arrays, score, seed=0, n_random=500_000, top_k=40, sweeps=15, extra_starts=extra)
    print("syn_levy20_c1: best f =", rank, "sum(x)/20 =", float(x.sum() / 20), "point =", x)


def calibrate_mostly_infeasible() -> None:
    level_arrays = [levels(0, 1, 0.01)] * m.INFEASIBLE_DIM
    sample = random_grid_points(level_arrays, 2_000_000, seed=1)
    r1, r2 = m.infeasible_distances(sample)

    lo, hi = 0.0, 1.0
    for _ in range(60):
        t = (lo + hi) / 2
        share = float(np.mean((r1 < t) & (r2 < t)))
        if share < 1e-3:
            lo = t
        else:
            hi = t
    t = (lo + hi) / 2
    share = float(np.mean((r1 < t) & (r2 < t)))
    print("syn_mostly_infeasible: t1=t2 =", t, "feasible share (2e6 sample) =", share)

    def score(u: np.ndarray) -> np.ndarray:
        r1, r2 = m.infeasible_distances(u)
        feasible = (r1 < t) & (r2 < t)
        violation = np.maximum(r1 - t, 0) + np.maximum(r2 - t, 0)
        obj = m.infeasible_objective(u)
        return np.where(feasible, obj, 1e6 + violation)

    # the all-equal points near the lens (by the symmetry of the two ball centres, an all-equal point is close to
    # optimal, though not exactly -- grid quantization lets an asymmetric point use the constraint budget better).
    extra = [np.full(m.INFEASIBLE_DIM, v) for v in (0.55, 0.56, 0.57)]
    x, rank = best_known(level_arrays, score, seed=2, n_random=400_000, top_k=40, sweeps=20, extra_starts=extra)
    r1x, r2x = m.infeasible_distances(x)
    print("  best known objective =", rank, "feasible =", bool(r1x < t and r2x < t), "point =", x)


def calibrate_failure_region() -> None:
    level_arrays = [levels(0, 1, 0.02)] * 6
    sample = random_grid_points(level_arrays, 2_000_000, seed=3)
    boundary_ok = (sample[:, 0] + sample[:, 1]) <= 1.4
    q = m.failure_region_q(sample)
    p = m.failure_region_p(sample)
    print("syn_failure_region: P(boundary) =", float(boundary_ok.mean()))

    lo, hi = 1.0, 2.0
    for _ in range(40):
        tq = (lo + hi) / 2
        share = float(np.mean(boundary_ok & (q > tq) & (p < (2 - tq))))
        if share > 0.04:
            lo = tq
        else:
            hi = tq
    tq = (lo + hi) / 2
    tp = 2 - tq
    share = float(np.mean(boundary_ok & (q > tq) & (p < tp)))
    print("  tq =", tq, "tp =", tp, "feasible share (2e6 sample) =", share)

    def score(u: np.ndarray) -> np.ndarray:
        boundary = (u[:, 0] + u[:, 1]) <= 1.4
        qv, pv = m.failure_region_q(u), m.failure_region_p(u)
        feasible = boundary & (qv > tq) & (pv < tp)
        violation = np.maximum(u[:, 0] + u[:, 1] - 1.4, 0) + np.maximum(tq - qv, 0) + np.maximum(pv - tp, 0)
        obj = m.failure_region_f(u)
        return np.where(feasible, obj, 1e6 + violation)

    x, rank = best_known(level_arrays, score, seed=4, n_random=300_000, top_k=20, sweeps=10)
    print("  best known f =", rank, "u0+u1 =", float(x[0] + x[1]), "distance to boundary =", abs(1.4 - float(x[0] + x[1])))


def calibrate_amplifier() -> None:
    dims = [(0.5, 10, 0.01), (0.5, 10, 0.01), (0.5, 5, 0.01), (0.5, 5, 0.01), (1, 50, 1), (1, 50, 1),
            (1, 30, 0.1), (0.5, 30, 0.1)]
    level_arrays = [levels(*d) for d in dims]
    sample = random_grid_points(level_arrays, 200_000, seed=5)
    gain, gbw, pm, power, area = m.amplifier_stage(*sample.T)
    pm_ok = pm > 60
    print("syn_amplifier_like: P(PM > 60) =", float(pm_ok.mean()))

    best_p, best_gap, best_t = None, np.inf, None
    for p in np.linspace(0.01, 0.6, 300):
        t_gain, t_gbw = np.quantile(gain, 1 - p), np.quantile(gbw, 1 - p)
        t_power, t_area = np.quantile(power, p), np.quantile(area, p)
        feasible = pm_ok & (gain > t_gain) & (gbw > t_gbw) & (power < t_power) & (area < t_area)
        gap = abs(float(feasible.mean()) - 0.01)
        if gap < best_gap:
            best_p, best_gap = p, gap
            best_t = (float(t_gain), float(t_gbw), float(t_power), float(t_area))
    t_gain, t_gbw, t_power, t_area = best_t
    feasible = pm_ok & (gain > t_gain) & (gbw > t_gbw) & (power < t_power) & (area < t_area)
    print("  p =", best_p, "thresholds GAIN,GBW,POWER,AREA =", best_t, "feasible share =", float(feasible.mean()))

    def score(x: np.ndarray) -> np.ndarray:
        gain, gbw, pm, power, area = m.amplifier_stage(*x.T)
        feasible = (pm > 60) & (gain > t_gain) & (gbw > t_gbw) & (power < t_power) & (area < t_area)
        violation = (np.maximum(60 - pm, 0) + np.maximum(t_gain - gain, 0) + np.maximum(t_gbw - gbw, 0)
                     + np.maximum(power - t_power, 0) + np.maximum(area - t_area, 0))
        fom = (gbw / 1e6) * 10 / power                      # maximize -> minimization form is -fom
        return np.where(feasible, -fom, 1e6 + violation)

    x, rank = best_known(level_arrays, score, seed=6, n_random=500_000, top_k=60, sweeps=20)
    gain, gbw, pm, power, area = m.amplifier_stage(*x)
    print("  best known objective (minimization form) =", rank, "GAIN,GBW,PM,POWER,AREA =", gain, gbw, pm, power, area)
    print("  point W1,W2,L1,L2,M1,M2,IB,CC =", x)


if __name__ == "__main__":
    calibrate_small_tight()
    calibrate_ackley10()
    calibrate_hartmann6(3.0)
    calibrate_hartmann6(2.0)
    calibrate_levy20()
    calibrate_mostly_infeasible()
    calibrate_failure_region()
    calibrate_amplifier()
