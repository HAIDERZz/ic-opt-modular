"""Eight synthetic benchmark problems: standard constrained test functions plus a few purpose-built ones (a tight
small grid, a multimodal one, a mostly-infeasible one, a partly-undefined one, an analytic amplifier), each on a
grid exactly as a real spec would state it.

Every problem's ``start`` is the grid point nearest the midpoint of every variable's range (``_start``, via
``ic_opt.space.bounds`` + ``snap``): a neutral "current design" in the absence of any domain-specific one, chosen the
same way for all eight so the choice needs stating once. Some problems (2, 6) turn out to make that point infeasible
-- documented per problem, not avoided, since a benchmark should include a run that starts outside the feasible set.

Two grids (``syn_small_tight``, 504 points; ``syn_multimodal_small``, 1089) are small enough to enumerate exactly at
problem-build time (:func:`icopt_bench._gridsearch.enumerate_grid`), so their ``reference`` is the true grid optimum,
computed here, not hardcoded. The other six range from ~2e10 to beyond 1e16 grid points; their ``reference`` is "best
known" -- 1e5-3e5 random grid points refined by coordinate descent from the best 20 (:func:`icopt_bench._gridsearch.
best_known`) -- computed once by ``benchmarks/tools/calibrate_synthetic.py`` and hardcoded below as a literal, per
problem, with the search's own result noted for cross-checking. The same script calibrates the constraint thresholds
that need a target feasible share (problems 1, 6, 7, 8): a quantile search against a large sample of the grid,
computed once, never at import time.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np

from ic_opt import space
from icopt_bench import _gridsearch as gs
from icopt_bench import _synthetic_math as sm
from icopt_bench.problem import Problem, child, make_spec


def _var(name: str, kind: str, lower: object, upper: object, step: object) -> dict:
    return {"name": name, "kind": kind, "lower": str(lower), "upper": str(upper), "step": str(step)}


def _start(spec) -> tuple[dict[str, str], ...]:
    """The grid point nearest the midpoint of every variable's range (module docstring)."""
    lows, highs = space.bounds(spec)
    raw = [(lo + hi) / 2 for lo, hi in zip(lows, highs, strict=True)]
    return (space.snap(spec, raw),)


# == syn_small_tight ================================================================================================
# Calibrated by benchmarks/tools/calibrate_synthetic.py (calibrate_small_tight): a common quantile p = 0.465 over the
# 504-point grid gives thresholds close to these; rounded to these numbers the feasible share is 11/504 = 2.18%
# (target ~2%, ~10 points).
_ST_THRESHOLDS = {"m1": 2.6, "m2": 8.4, "m3": 1.69, "m4": -4.3, "m5": 1.57}
_ST_OPS = {"m1": "gt", "m2": "lt", "m3": "gt", "m4": "gt", "m5": "gt"}


def _small_tight_z(name: str, value: np.ndarray, weight: float) -> np.ndarray:
    t = _ST_THRESHOLDS[name]
    raw = (value - t) / weight if _ST_OPS[name] == "gt" else (t - value) / weight
    return np.clip(raw, 0, 1)


def _small_tight_objective_expression(weights: dict[str, float]) -> str:
    """The bottleneck form of ``examples/spec.yaml``'s objective, inlined (the expression language has no let-binding):
    ``-(0.1*min(z1..z5) + 0.8*mean(z1..z5))`` with each ``z_i`` a clipped, normalized constraint margin."""
    terms = []
    for name in ("m1", "m2", "m3", "m4", "m5"):
        t, w = _ST_THRESHOLDS[name], weights[name]
        terms.append(f"max(0,min(1,({name}-{t})/{w}))" if _ST_OPS[name] == "gt" else f"max(0,min(1,({t}-{name})/{w}))")
    zmean = "+".join(f"0.2*{z}" for z in terms)
    return f"-(0.1*min({','.join(terms)})+0.8*({zmean}))"


def _build_small_tight() -> Problem:
    """4 variables (6x4x3x7 = 504 combinations), 5 metrics ``m1..m5`` -- near-linear/quadratic in the unit-cube
    coordinates ``ua, ub, uc, ud`` (see :func:`icopt_bench._synthetic_math.small_tight_metrics`) -- and one constraint
    per metric (``m1,m3,m4,m5 gt``, ``m2 lt``), thresholds calibrated so 11/504 points (2.18%) are feasible. The
    objective is the bottleneck form of ``examples/spec.yaml``: ``-(0.1*min(z) + 0.8*mean(z))`` with each ``z_i`` the
    constraint margin normalized by 25% of the metric's grid range, clipped to [0, 1]. Reference -0.5531029346286177,
    the true grid optimum (exact enumeration, computed here): a: 30, b: 0.8u.. actually plain (a=30, b=0.8, c=50,
    d=400 -- the grid point [1, 1/3, 1, 1] in unit-cube coordinates)."""
    variables = [_var("a", "integer", 20, 30, 2), _var("b", "continuous_step", "0.6", "1.2", "0.2"),
                 _var("c", "continuous_step", 30, 50, 10), _var("d", "continuous_step", 280, 400, 20)]
    a_lv, b_lv, c_lv, d_lv = gs.levels(20, 30, 2), gs.levels(0.6, 1.2, 0.2), gs.levels(30, 50, 10), gs.levels(280, 400, 20)
    grid = gs.enumerate_grid([a_lv, b_lv, c_lv, d_lv])
    u = np.stack([(grid[:, 0] - 20) / 10, (grid[:, 1] - 0.6) / 0.6, (grid[:, 2] - 30) / 20, (grid[:, 3] - 280) / 120], axis=-1)
    m1, m2, m3, m4, m5 = sm.small_tight_metrics(u)
    values = {"m1": m1, "m2": m2, "m3": m3, "m4": m4, "m5": m5}
    weights = {name: 0.25 * float(v.max() - v.min()) for name, v in values.items()}
    feasible = np.ones(grid.shape[0], dtype=bool)
    for name, v in values.items():
        feasible &= (v > _ST_THRESHOLDS[name]) if _ST_OPS[name] == "gt" else (v < _ST_THRESHOLDS[name])
    zs = [_small_tight_z(name, v, weights[name]) for name, v in values.items()]
    objective_values = -(0.1 * np.minimum.reduce(zs) + 0.8 * (sum(zs) / len(zs)))
    reference = float(objective_values[feasible].min()) if feasible.any() else None

    metrics = [{"name": n, "unit": "1", "testbench": tb} for n, tb in
               [("m1", "tb1"), ("m2", "tb1"), ("m3", "tb1"), ("m4", "tb2"), ("m5", "tb2")]]
    constraints = [{"metric": n, "op": _ST_OPS[n], "value": str(_ST_THRESHOLDS[n])} for n in values]
    objective = {"direction": "minimize", "expression": _small_tight_objective_expression(weights)}
    spec = make_spec("syn_small_tight", variables=variables, metrics=metrics, constraints=constraints, objective=objective,
                      description="Small, tightly-constrained grid: a bottleneck objective over 5 near-linear/quadratic metrics.")

    def evaluate(params: dict[str, str]) -> dict:
        ua = (float(params["a"]) - 20) / 10
        ub = (float(params["b"]) - 0.6) / 0.6
        uc = (float(params["c"]) - 30) / 20
        ud = (float(params["d"]) - 280) / 120
        v1, v2, v3, v4, v5 = sm.small_tight_metrics(np.array([ua, ub, uc, ud]))
        return {"tb1/nominal": child("tb1", {"m1": float(v1), "m2": float(v2), "m3": float(v3)}),
                "tb2/nominal": child("tb2", {"m4": float(v4), "m5": float(v5)})}

    return Problem(name="syn_small_tight", family="synthetic", scenario="around_design", spec=spec, evaluate=evaluate,
                    start=_start(spec), reference=reference,
                    notes=f"exact: 504-point enumeration; feasible {int(feasible.sum())}/504 ({feasible.mean():.2%})",
                    tags=("tight", "bottleneck"))


# == syn_multimodal_small ============================================================================================


def _build_multimodal() -> Problem:
    """3 variables on [0, 1] (11x11x9 = 1089 combinations). ``f`` is the lower envelope of two quadratic bowls
    (:func:`icopt_bench._synthetic_math.multimodal_f`): centred at ``(0.15, 0.2, 0.25)`` and ``(0.85, 0.8, 0.75)``,
    the second 15% deeper, so it holds the global optimum. ``g`` (:func:`.multimodal_g`) is positive in a radius-0.3
    ball around the midpoint of the two centres and negative elsewhere -- a wall that the straight line between the
    bowls must cross (the ball's radius is less than half that line's length, 0.524, so it does not touch either
    bowl) but which does not touch either bowl. Minimize ``f`` subject to ``g lt 0``. Measured (exact, 1089-point
    enumeration): 92.0% feasible; reference -1.1475 at the grid point nearest the deeper bowl's centre, (0.8, 0.8,
    0.75). The start point, (0.5, 0.5, 0.5), is exactly the wall's centre and so is infeasible (g = 0.09)."""
    variables = [_var("x0", "continuous_step", 0, 1, 0.1), _var("x1", "continuous_step", 0, 1, 0.1),
                 _var("x2", "continuous_step", 0, 1, 0.125)]
    metrics = [{"name": "f", "unit": "1", "testbench": "tb1"}, {"name": "g", "unit": "1", "testbench": "tb1"}]
    constraints = [{"metric": "g", "op": "lt", "value": "0"}]
    objective = {"direction": "minimize", "expression": "f"}
    spec = make_spec("syn_multimodal_small", variables=variables, metrics=metrics, constraints=constraints, objective=objective,
                      description="Two quadratic bowls (the second 15% deeper) separated by a spherical infeasible wall.")

    def evaluate(params: dict[str, str]) -> dict:
        u = np.array([float(params["x0"]), float(params["x1"]), float(params["x2"])])
        return {"tb1/nominal": child("tb1", {"f": float(sm.multimodal_f(u)), "g": float(sm.multimodal_g(u))})}

    grid = gs.enumerate_grid([gs.levels(0, 1, 0.1), gs.levels(0, 1, 0.1), gs.levels(0, 1, 0.125)])
    f, g = sm.multimodal_f(grid), sm.multimodal_g(grid)
    feasible = g < 0
    reference = float(f[feasible].min()) if feasible.any() else None

    return Problem(name="syn_multimodal_small", family="synthetic", scenario="around_design", spec=spec, evaluate=evaluate,
                    start=_start(spec), reference=reference,
                    notes=f"exact: 1089-point enumeration; feasible {int(feasible.sum())}/1089 ({feasible.mean():.2%}); "
                          "the start point sits exactly on the infeasible wall",
                    tags=("multimodal", "constrained"))


# == syn_ackley10_c2 =================================================================================================


def _build_ackley10() -> Problem:
    """10-D Ackley (:func:`icopt_bench._synthetic_math.ackley10`) on x = u - 5 in [-5, 10]^10, with the SCBO paper's
    two constraints, c1 = sum(x) <= 0 and c2 = ||x||_2 - 5 <= 0. Mixed grid: u0-u2 step 5 (4 levels), u3-u5 step 2.5
    (7 levels), u6-u9 step 0.5 (31 levels) -- 4^3 x 7^3 x 31^4 ~= 2.0e10 combinations, too large to enumerate. No
    threshold needed calibration: both the function and its constraints are the paper's standard ones. Reference 0.0,
    exact: x = 0 (u = 5, which is exactly on the grid for every one of the three step sizes) is Ackley's global
    optimum and trivially satisfies both constraints (sum = 0, norm = 0); confirmed by
    ``calibrate_synthetic.calibrate_ackley10`` (best known 4.4e-16, feasible)."""
    dims = [(0, 15, 5)] * 3 + [(0, 15, 2.5)] * 3 + [(0, 15, 0.5)] * 4
    variables = [_var(f"u{i}", "continuous_step", lo, hi, st) for i, (lo, hi, st) in enumerate(dims)]
    metrics = [{"name": "f", "unit": "1", "testbench": "tb1"}, {"name": "c1", "unit": "1", "testbench": "tb1"},
               {"name": "c2", "unit": "1", "testbench": "tb1"}]
    constraints = [{"metric": "c1", "op": "le", "value": "0"}, {"metric": "c2", "op": "le", "value": "0"}]
    objective = {"direction": "minimize", "expression": "f"}
    spec = make_spec("syn_ackley10_c2", variables=variables, metrics=metrics, constraints=constraints, objective=objective,
                      description="10-D Ackley with the SCBO paper's two constraints, on a mixed-resolution grid.")

    def evaluate(params: dict[str, str]) -> dict:
        u = np.array([float(params[f"u{i}"]) for i in range(10)])
        x = u - 5
        c1, c2 = sm.ackley10_constraints(x)
        return {"tb1/nominal": child("tb1", {"f": float(sm.ackley10(x)), "c1": float(c1), "c2": float(c2)})}

    return Problem(name="syn_ackley10_c2", family="synthetic", scenario="wide_range", spec=spec, evaluate=evaluate,
                    start=_start(spec), reference=0.0,
                    notes="exact: x=0 is the analytic global optimum and lies on the grid; cross-checked by a 2e5-point "
                          "random search + coordinate descent (calibrate_synthetic.calibrate_ackley10)",
                    tags=("ackley", "scbo"))


# == syn_hartmann6_c1 =================================================================================================
# Tightened from the textbook K=3 (see the docstring below): calibrate_synthetic.calibrate_hartmann6(2.0) confirms
# the constraint binds at K=2.0.
_HARTMANN_BOUND = 2.0


def _build_hartmann6() -> Problem:
    """Hartmann-6 (:func:`icopt_bench._synthetic_math.hartmann6`) on [0, 1]^6, grid step 0.01 (101^6 ~= 1.06e12
    combinations, too large to enumerate). Constraint c = sum(x) - K <= 0. The textbook K = 3 leaves the unconstrained
    optimum (x* ~= (0.2017, 0.15, 0.4769, 0.2753, 0.3117, 0.6573), sum ~= 2.071, f* ~= -3.3224) comfortably feasible
    (slack ~0.93 of a range of 6) -- checked by ``calibrate_synthetic.calibrate_hartmann6(3.0)``, which reproduces
    that point almost exactly on the grid. Tightened to K = 2.0, under which the best-known grid point (a 4e5-point
    random search + coordinate descent from its best 40 plus the unconstrained optimum snapped to the grid,
    ``calibrate_hartmann6(2.0)``) sits exactly on the constraint, sum(x) = 2.0: reference -3.3007706130601644,
    "best known"."""
    variables = [_var(f"x{i}", "continuous_step", 0, 1, 0.01) for i in range(6)]
    metrics = [{"name": "f", "unit": "1", "testbench": "tb1"}, {"name": "c", "unit": "1", "testbench": "tb1"}]
    constraints = [{"metric": "c", "op": "le", "value": "0"}]
    objective = {"direction": "minimize", "expression": "f"}
    spec = make_spec("syn_hartmann6_c1", variables=variables, metrics=metrics, constraints=constraints, objective=objective,
                      description="Hartmann-6 with a tightened sum constraint that binds at the constrained optimum.")

    def evaluate(params: dict[str, str]) -> dict:
        x = np.array([float(params[f"x{i}"]) for i in range(6)])
        return {"tb1/nominal": child("tb1", {"f": float(sm.hartmann6(x)), "c": float(np.sum(x) - _HARTMANN_BOUND)})}

    return Problem(name="syn_hartmann6_c1", family="synthetic", scenario="wide_range", spec=spec, evaluate=evaluate,
                    start=_start(spec), reference=-3.3007706130601644,
                    notes="best known: 4e5-point random search + coordinate descent, plus the unconstrained optimum as a "
                          "seed (calibrate_synthetic.calibrate_hartmann6); the constraint is active there (sum(x) = 2.0)",
                    tags=("hartmann6",))


# == syn_levy20_c1 =====================================================================================================


def _build_levy20() -> Problem:
    """Levy (:func:`icopt_bench._synthetic_math.levy20`) in 20 dimensions on [-10, 10], grid step 0.05 (401^20
    combinations, far too large to enumerate). Constraint c = sum(x)/20 - 0.5 <= 0: the unconstrained global optimum
    (x = 1 everywhere, f = 0) has sum(x)/20 = 1 - 0.5 = 0.5 > 0, already infeasible, so no tightening is needed.
    Reference 0.9251748848613778, "best known" (5e5-point random search + coordinate descent, seeded also with x=0
    and the boundary point x=0.5 everywhere -- pure random starts land the search in a much worse basin (2.497),
    since Levy's own optimum sits just outside the feasible region and coordinate descent from a random point rarely
    finds its way back to it, ``calibrate_synthetic.calibrate_levy20``); the found point sits at sum(x)/20 = 0.4975,
    essentially on the boundary, with ten of its twenty coordinates at Levy's own per-coordinate optimum, x_i = 1."""
    variables = [_var(f"x{i}", "continuous_step", -10, 10, 0.05) for i in range(20)]
    metrics = [{"name": "f", "unit": "1", "testbench": "tb1"}, {"name": "c", "unit": "1", "testbench": "tb1"}]
    constraints = [{"metric": "c", "op": "le", "value": "0"}]
    objective = {"direction": "minimize", "expression": "f"}
    spec = make_spec("syn_levy20_c1", variables=variables, metrics=metrics, constraints=constraints, objective=objective,
                      description="20-D Levy with a mean constraint that is already active at the unconstrained optimum.")

    def evaluate(params: dict[str, str]) -> dict:
        x = np.array([float(params[f"x{i}"]) for i in range(20)])
        return {"tb1/nominal": child("tb1", {"f": float(sm.levy20(x)), "c": float(np.sum(x) / 20 - 0.5)})}

    return Problem(name="syn_levy20_c1", family="synthetic", scenario="wide_range", spec=spec, evaluate=evaluate,
                    start=_start(spec), reference=0.9251748848613778,
                    notes="best known: 5e5-point random search + coordinate descent, seeded also with x=0 and x=0.5 "
                          "everywhere (calibrate_synthetic.calibrate_levy20)",
                    tags=("levy", "high_dim"))


# == syn_mostly_infeasible ============================================================================================
# Calibrated by calibrate_synthetic.calibrate_mostly_infeasible: a bisection on a shared radius t over a 2e6-point
# random sample of the grid gives this feasible share.
_INFEASIBLE_RADIUS = 0.212838


def _build_mostly_infeasible() -> Problem:
    """8 variables on [0, 1], grid step 0.01 (101^8 ~= 1.09e16 combinations). ``r1``, ``r2``
    (:func:`icopt_bench._synthetic_math.infeasible_distances`) are the Euclidean distance to two fixed centres,
    (0.35,)*8 and (0.65,)*8, scaled by 1/sqrt(8); feasible when both are below a shared calibrated radius, the
    intersection of two balls forming one connected lens (the centres are 0.3 apart in the scaled distance, the
    radius 0.2128 on each side, so the balls overlap but neither contains the other). Measured feasible share
    0.0999% (2e6-point random sample) -- target 0.1%. Objective: minimize the distance to a third centre, (0.9,)*8,
    outside the lens (:func:`.infeasible_objective`). Reference 0.9116, "best known" (4e5-point random search +
    coordinate descent, seeded also with a few all-equal points near the lens,
    ``calibrate_synthetic.calibrate_mostly_infeasible``); the found point, (0.58, 0.56, 0.56, 0.56, 0.56, 0.56, 0.56,
    0.56), is close to but not exactly all-equal -- the grid's 0.01 step does not let every coordinate land on the
    continuous optimum at once, so an asymmetric point uses the two balls' constraint budget slightly better."""
    variables = [_var(f"x{i}", "continuous_step", 0, 1, 0.01) for i in range(sm.INFEASIBLE_DIM)]
    metrics = [{"name": "r1", "unit": "1", "testbench": "tb1"}, {"name": "r2", "unit": "1", "testbench": "tb1"},
               {"name": "f", "unit": "1", "testbench": "tb1"}]
    constraints = [{"metric": "r1", "op": "lt", "value": str(_INFEASIBLE_RADIUS)},
                   {"metric": "r2", "op": "lt", "value": str(_INFEASIBLE_RADIUS)}]
    objective = {"direction": "minimize", "expression": "f"}
    spec = make_spec("syn_mostly_infeasible", variables=variables, metrics=metrics, constraints=constraints, objective=objective,
                      description="8-D: feasible only in the thin lens where two balls of radius 0.2128 overlap (~0.1% of the grid).")

    def evaluate(params: dict[str, str]) -> dict:
        u = np.array([float(params[f"x{i}"]) for i in range(sm.INFEASIBLE_DIM)])
        r1, r2 = sm.infeasible_distances(u)
        return {"tb1/nominal": child("tb1", {"r1": float(r1), "r2": float(r2), "f": float(sm.infeasible_objective(u))})}

    return Problem(name="syn_mostly_infeasible", family="synthetic", scenario="wide_range", spec=spec, evaluate=evaluate,
                    start=_start(spec), reference=0.9116,
                    notes="calibrated: 2e6-point random sample, feasible share 0.0999% (target 0.1%); reference is best "
                          "known (4e5-point random search + coordinate descent, calibrate_synthetic.calibrate_mostly_infeasible)",
                    tags=("mostly_infeasible", "lens"))


# == syn_failure_region ===============================================================================================
# Calibrated by calibrate_synthetic.calibrate_failure_region: a bisection over a 2e6-point random sample, holding
# tp = 2 - tq, to land the joint (boundary AND q AND p) feasible share in [3%, 5%].
_FAILURE_TQ = 1.34
_FAILURE_TP = 0.66


def _build_failure_region() -> Problem:
    """6 variables on [0, 1], grid step 0.02. Unit ``tb2``'s metric ``p`` (:func:`icopt_bench._synthetic_math.
    failure_region_p`, ``u4 + u5``) has no value wherever ``u0 + u1 > 1.4`` (``child(..., missing=["p"])``, which
    ``ic_opt.sim.corner.aggregate`` turns into ``metric_failed`` -- infeasible); elsewhere it is smooth. ``q``
    (``u2 + u3``) is tb1's other constrained metric. Minimize ``f = (u0-1)^2 + (u1-1)^2`` (:func:`.failure_region_f`,
    over u0, u1 only) subject to ``q gt 1.34``, ``p lt 0.66`` and the implicit ``u0+u1<=1.4``. Measured feasible
    share 4.30% (2e6-point sample), inside the 3-5% target. Because f wants u0, u1 as large as possible and the other
    four variables are unconstrained by f, the true optimum sits exactly on the failure boundary: u0 = u1 = 0.7 (on
    the grid, sum = 1.4), with e.g. u2 = u3 = 1 (q = 2 > 1.34) and u4 = u5 = 0 (p = 0 < 0.66). Reference 0.18, exact
    (this closed form); the search (``calibrate_synthetic.calibrate_failure_region``) finds 0.1808, within one grid
    step of it -- distance from the optimum to the boundary: 0.0."""
    variables = [_var(f"u{i}", "continuous_step", 0, 1, 0.02) for i in range(6)]
    metrics = [{"name": "f", "unit": "1", "testbench": "tb1"}, {"name": "q", "unit": "1", "testbench": "tb1"},
               {"name": "p", "unit": "1", "testbench": "tb2"}]
    constraints = [{"metric": "q", "op": "gt", "value": str(_FAILURE_TQ)}, {"metric": "p", "op": "lt", "value": str(_FAILURE_TP)}]
    objective = {"direction": "minimize", "expression": "f"}
    spec = make_spec("syn_failure_region", variables=variables, metrics=metrics, constraints=constraints, objective=objective,
                      description="One testbench's metric is undefined past u0+u1>1.4; the optimum sits on that boundary.")

    def evaluate(params: dict[str, str]) -> dict:
        u = np.array([float(params[f"u{i}"]) for i in range(6)])
        f, q = float(sm.failure_region_f(u)), float(sm.failure_region_q(u))
        if bool(sm.failure_region_missing(u)):
            tb2 = child("tb2", {}, missing=["p"])
        else:
            tb2 = child("tb2", {"p": float(sm.failure_region_p(u))})
        return {"tb1/nominal": child("tb1", {"f": f, "q": q}), "tb2/nominal": tb2}

    return Problem(name="syn_failure_region", family="synthetic", scenario="wide_range", spec=spec, evaluate=evaluate,
                    start=_start(spec), reference=0.18,
                    notes="calibrated: 2e6-point random sample, feasible share 4.30% (target 3-5%); reference is exact "
                          "(closed form, u0=u1=0.7 on the boundary), cross-checked by best-known search (0.1808)",
                    tags=("failure_region", "missing_metric"))


# == syn_amplifier_like ================================================================================================
# Calibrated by calibrate_synthetic.calibrate_amplifier: a common quantile p = 0.62 over a 2e5-point random sample of
# the grid, jointly with the fixed PM > 60 constraint, rounded to these numbers.
_AMP_GAIN_MIN = 85.8            # dB
_AMP_GBW_MIN = 7_700_000.0      # Hz
_AMP_POWER_MAX = 0.96           # mW
_AMP_AREA_MAX = 1141.0          # um^2


def _build_amplifier() -> Problem:
    """An analytic two-stage amplifier (:func:`icopt_bench._synthetic_math.amplifier_stage`): W1, W2 (0.5-10 um, step
    0.01), L1, L2 (0.5-5 um, step 0.01), M1, M2 (1-50, integer), IB (1-30 uA, step 0.1), CC (0.5-30 pF, step 0.1) --
    a wide-range problem where a log-scale search matters (W, L, M, IB, CC all span more than one decade). Metrics
    GAIN (dB), GBW (Hz), PM (deg) on tb1; POWER (mW), AREA (um^2) on tb2. Constraints: GAIN > 85.8, PM > 60 (fixed),
    GBW > 7.7 MHz, POWER < 0.96 mW, AREA < 1141 um^2 -- the four free thresholds calibrated at a common quantile
    (jointly with the fixed PM constraint) so 1.07% of a 2e5-point random sample is feasible (target 1%). Objective:
    maximize (GBW/1e6)*10/POWER. Reference -2996.460372658108 (minimization form; the maximized value is 2996.46),
    "best known" (5e5-point random search + coordinate descent from its best 60, ``calibrate_synthetic.
    calibrate_amplifier``), at W1=0.83, W2=8.2, L1=4.51, L2=0.5, M1=37, M2=50, IB=1, CC=1.1 -- IB at its lower bound
    (POWER is monotone in IB, so the cheapest feasible design always takes the smallest one)."""
    variables = [_var("W1", "continuous_step", "0.5", "10", "0.01"), _var("W2", "continuous_step", "0.5", "10", "0.01"),
                 _var("L1", "continuous_step", "0.5", "5", "0.01"), _var("L2", "continuous_step", "0.5", "5", "0.01"),
                 _var("M1", "integer", 1, 50, 1), _var("M2", "integer", 1, 50, 1),
                 _var("IB", "continuous_step", 1, 30, "0.1"), _var("CC", "continuous_step", "0.5", 30, "0.1")]
    metrics = [{"name": "GAIN", "unit": "dB", "testbench": "tb1"}, {"name": "GBW", "unit": "Hz", "testbench": "tb1"},
               {"name": "PM", "unit": "deg", "testbench": "tb1"}, {"name": "POWER", "unit": "mW", "testbench": "tb2"},
               {"name": "AREA", "unit": "um2", "testbench": "tb2"}]
    constraints = [{"metric": "GAIN", "op": "gt", "value": str(_AMP_GAIN_MIN)}, {"metric": "PM", "op": "gt", "value": "60"},
                   {"metric": "GBW", "op": "gt", "value": str(_AMP_GBW_MIN)}, {"metric": "POWER", "op": "lt", "value": str(_AMP_POWER_MAX)},
                   {"metric": "AREA", "op": "lt", "value": str(_AMP_AREA_MAX)}]
    objective = {"direction": "maximize", "expression": "(GBW/1e6)*10/POWER"}
    spec = make_spec("syn_amplifier_like", variables=variables, metrics=metrics, constraints=constraints, objective=objective,
                      description="An analytic two-stage amplifier: wide-range sizing variables, a gain/bandwidth/power/area tradeoff.")

    def evaluate(params: dict[str, str]) -> dict:
        names = ("W1", "W2", "L1", "L2", "M1", "M2", "IB", "CC")
        gain, gbw, pm, power, area = sm.amplifier_stage(*(float(params[n]) for n in names))
        return {"tb1/nominal": child("tb1", {"GAIN": float(gain), "GBW": float(gbw), "PM": float(pm)}),
                "tb2/nominal": child("tb2", {"POWER": float(power), "AREA": float(area)})}

    return Problem(name="syn_amplifier_like", family="synthetic", scenario="wide_range", spec=spec, evaluate=evaluate,
                    start=_start(spec), reference=-2996.460372658108,
                    notes="calibrated: 2e5-point random sample, feasible share 1.07% (target 1%); reference is best known "
                          "(5e5-point random search + coordinate descent, calibrate_synthetic.calibrate_amplifier)",
                    tags=("amplifier", "log_scale"))


PROBLEMS: dict[str, Callable[[], Problem]] = {
    "syn_small_tight": _build_small_tight,
    "syn_multimodal_small": _build_multimodal,
    "syn_ackley10_c2": _build_ackley10,
    "syn_hartmann6_c1": _build_hartmann6,
    "syn_levy20_c1": _build_levy20,
    "syn_mostly_infeasible": _build_mostly_infeasible,
    "syn_failure_region": _build_failure_region,
    "syn_amplifier_like": _build_amplifier,
}
