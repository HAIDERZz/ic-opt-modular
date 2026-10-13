"""N-108: a neural network as the mean, the library's Gaussian process on its residual -- does the combination keep the
tuned MLP's accuracy (N-107's ``mlp``) and give the GP's calibrated interval? Specification:
``docs/refactor/N108_MLP_GP_HYBRID_SPEC.md``. No EMX, the library only read, no product change.

``StratumMLPGP`` is a ``gp.StratumGP`` (through N-107's ``regressor_base.StratumMean`` and ``regressors_neural.StratumMLP``),
so ``predict``, ``predict_bounds``, ``available``, ``k_scale`` and ``sigma_floor_rel`` keep the library's meaning:

- ``fit(x, y)``: the per_nt split as the base class (one sub-model per turns level with at least ``MIN_NT_SAMPLES`` rows,
  ``StratumMean.fit``). Joint path: ``z = self._scale(x)`` with the inputs constant on the training rows dropped
  (``StratumMean``), ``t = log(y)`` for a log target; the MLP of N-107's ``mlp`` variant -- ``tune`` (3 folds, seed 0, RMSE
  in the fitted space) over its 8-configuration grid ``MLP_GRID`` on the training rows, then the chosen configuration
  refitted on all of them, ``MLPRegressor(solver="lbfgs", max_iter=2000)`` on the target standardized inside the model --
  with ``random_state=seed`` (in the inner fits and the refit; the inner folds stay seed 0, so only the network's
  initialisation changes with the seed); the residual ``r = t - mlp(z)`` on the training rows; then the library's GP on
  ``(z, r)``: ``GaussianProcessRegressor(gp._kernel(d, kernel), normalize_y=True, n_restarts_optimizer=4,
  random_state=0)`` -- Constant x Matern 5/2 ARD + White, the library's kernel setting (``matern52`` for every library
  column) -- the very GP ``gp.StratumGP`` fits, on the residual instead of the target.
- ``_predict(x)``: ``m = mlp(z) + gp_mu(z)``, ``s = gp_sigma(z)``; a log target returns ``(exp(m), exp(m) * s)`` as the base
  class does, so ``gp.prediction_bounds``, ``k_scale`` and the sigma floor apply unchanged. ``posterior_cov`` raises
  (``StratumMean``'s).
- ``seed`` is the MLP's random state; the GP's stays 0. With ``seed=0`` the MLP part is N-107's ``mlp`` exactly (the same
  grid, inner folds, refit and random state).

``StratumMLPSeeded`` is N-107's ``mlp`` with that seed and nothing else (sigma NaN): ``mlp_s1`` ... ``mlp_s4``, for the seed
spread of the MLP alone (N-107's ``mlp`` is seed 0 and comes from its job cache). ``StratumGPRefit`` is the library's GP
class itself, unchanged, under a name of its own: N-104's cached GP folds keep no sigma, so ``gp_refit`` refits the GP on the
same folds for its coverage, ``k_scale`` and interval width (its point errors repeat the cached ``gp``'s; the harness
compares them); it only accepts the harness's ``n_threads``.

What a fitted model says (JSON-ready; a per_nt model answers per turns level): ``chosen``, ``inner_scores`` and
``fit_info`` as N-107's ``mlp`` (``fit_info`` adds ``seed`` and, for the hybrid, the residual GP's fit: seconds, the
residual's std on the training rows next to the target's, the log marginal likelihood, the fitted noise level, constant
and length scales); ``kernel_fitted`` the residual GP's (the GP's for ``gp_refit``).

Registry (``VARIANTS``, N-104's shape with the class under ``"model"``): ``gp_refit`` (family ``R``, a reference, not a
challenger), ``mlp_s1`` ... ``mlp_s4`` (family C), ``mlp_gp_s0`` ... ``mlp_gp_s4`` (family H); ``params`` holds ``seed``;
``sigma`` marks the variants whose folds also keep each held-out row's mu and sigma; ``SEED_GROUPS`` names the seeds of
one model; ``DEPENDS`` the modules whose source is part of these variants' job key besides this one.
"""
from __future__ import annotations

import sys
import time
import warnings
from pathlib import Path

import numpy as np
from sklearn.exceptions import ConvergenceWarning
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.neural_network import MLPRegressor
from threadpoolctl import threadpool_limits

from ic_opt.library import gp

_HERE = str(Path(__file__).resolve().parent)       # regressor_base and regressors_neural sit beside this file
if _HERE not in sys.path:
    sys.path.append(_HERE)
from regressor_base import _GP, tune
from regressors_neural import (
    GRID_CUT,
    INNER_FOLDS,
    INNER_SEED,
    MLP_FIXED,
    MLP_GRID,
    StandardizedTarget,
    StratumMLP,
    _key,
)

MLP_SEEDS = (0, 1, 2, 3, 4)
GP_RESTARTS, GP_RANDOM_STATE = 4, 0                  # the library's (gp.StratumGP.fit)


def seeded_mlp(cfg: dict, seed: int) -> StandardizedTarget:
    """N-107's ``mlp`` network (``regressors_neural._mlp``) with ``random_state=seed``."""
    return StandardizedTarget(MLPRegressor(**cfg, **{**MLP_FIXED, "random_state": int(seed)}))


class StratumMLPSeeded(StratumMLP):
    """``mlp_s<k>``: N-107's ``mlp`` -- tuned over ``MLP_GRID`` by ``tune`` (3 folds, seed 0), refitted -- with the network's
    random state ``seed`` (the inner folds unchanged). Sigma NaN."""

    MODEL = "mlp_seeded"

    def __init__(self, *, seed: int = 0, **settings):
        super().__init__(**settings)
        self.seed = int(seed)

    def own_params(self) -> dict:
        return {**super().own_params(), "seed": self.seed}

    def _fit_joint(self, z: np.ndarray, t: np.ndarray) -> None:
        seconds: dict[str, float] = {}

        def fit_predict(cfg: dict, z_tr: np.ndarray, t_tr: np.ndarray, z_te: np.ndarray) -> np.ndarray:
            t0 = time.perf_counter()
            try:
                return seeded_mlp(cfg, self.seed).fit(z_tr, t_tr).predict(z_te)
            finally:
                seconds[_key(cfg)] = seconds.get(_key(cfg), 0.0) + time.perf_counter() - t0

        best, scores = tune(z, t, self.grid, fit_predict, folds=INNER_FOLDS, seed=INNER_SEED)
        t0 = time.perf_counter()
        self._model = seeded_mlp(best, self.seed).fit(z, t)
        refit_s = time.perf_counter() - t0
        self._chosen, self._scores = best, scores
        self._info = {"rows": len(t), "inputs": int(z.shape[1]), "seed": self.seed, "n_iter": int(self._model.estimator.n_iter_),
                      "inner_s": {k: round(v, 3) for k, v in seconds.items()}, "refit_s": round(refit_s, 3)}


class StratumMLPGP(StratumMLPSeeded):
    """``mlp_gp_s<k>``: the seeded MLP as the mean, the library's GP fitted on its training residual (module docstring)."""

    MODEL = "mlp_gp"

    def __init__(self, **settings):
        super().__init__(**settings)
        self._resid_gp: GaussianProcessRegressor | None = None

    def _fit_joint(self, z: np.ndarray, t: np.ndarray) -> None:
        super()._fit_joint(z, t)                     # the MLP: tuned, refitted with random_state=seed
        t0 = time.perf_counter()
        r = t - self._model.predict(z)
        gpr = GaussianProcessRegressor(kernel=gp._kernel(z.shape[1], self.kernel), normalize_y=True,
                                       n_restarts_optimizer=GP_RESTARTS, random_state=GP_RANDOM_STATE)
        with warnings.catch_warnings():              # hyperparameters at a bound are expected (the library silences them too)
            warnings.simplefilter("ignore", ConvergenceWarning)
            gpr.fit(z, r)
        self._resid_gp = gpr
        k = gpr.kernel_                              # Constant * Matern + White, as gp._kernel builds it
        self._info.update({"gp_s": round(time.perf_counter() - t0, 3), "resid_std": float(np.std(r)), "target_std": float(np.std(t)),
                           "gp_lml": float(gpr.log_marginal_likelihood_value_), "gp_noise_level": float(k.k2.noise_level),
                           "gp_constant": float(k.k1.k1.constant_value),
                           "gp_length_scale": np.atleast_1d(k.k1.k2.length_scale).astype(float).tolist()})

    @property
    def kernel_fitted(self):
        if self.nt_mode == "per_nt":
            return {str(level): sub.kernel_fitted for level, sub in sorted(self._sub.items())}
        return None if self._resid_gp is None else str(self._resid_gp.kernel_)

    def _predict(self, x) -> tuple[np.ndarray, np.ndarray]:
        if self.nt_mode == "per_nt":
            return super()._predict(x)               # the base class's per_nt path; each level's sub-model answers below
        x = np.asarray(x, dtype=float)
        if x.ndim != 2 or x.shape[1] != len(self.dims):
            raise ValueError(f"expected [N, {len(self.dims)}] inputs")
        if self._keep is None or self._model is None or self._resid_gp is None:
            raise RuntimeError("fit() first")
        with threadpool_limits(limits=self.n_threads):
            z = self._scale(x)[:, self._keep]
            mu_r, s = self._resid_gp.predict(z, return_std=True)
            m = np.asarray(self._model.predict(z), dtype=float).reshape(-1) + mu_r
        with np.errstate(over="ignore"):
            return (np.exp(m), np.exp(m) * s) if self.log_target else (m, s)


class StratumGPRefit(_GP):
    """``gp_refit``: the library's ``gp.StratumGP``, unchanged, refitted on the study's folds so that its sigma is kept (N-104's
    cached GP folds hold only the relative errors). ``n_threads`` is accepted and not used: the harness caps the job's threads."""

    MODEL = "gp_refit"

    def __init__(self, *, n_threads: int = 1, **settings):
        super().__init__(**settings)
        self.n_threads = int(n_threads)

    @property
    def kernel_fitted(self):
        return None if self._gp is None else str(self._gp.kernel_)


_NOTE_HYBRID = ("N-107's mlp (8-configuration grid, inner folds seed 0, lbfgs, target standardized) with random_state=seed as the mean; "
                "the library's GP (Matern 5/2 ARD + White, normalize_y, 4 restarts, random_state 0) on its training residual; "
                "every part of a composed column the hybrid")
VARIANTS = {
    "gp_refit": {"model": StratumGPRefit, "family": "R", "tuned": False, "grid": None, "grid_cut": [], "params": {}, "sigma": True,
                 "reference": True, "cost_weight": 1.0,
                 "note": "the library's GP refitted on the same folds for its sigma (N-104's cached folds keep none); a reference, not a challenger"},
    **{f"mlp_s{k}": {"model": StratumMLPSeeded, "family": "C", "tuned": True, "grid": MLP_GRID, "grid_cut": GRID_CUT, "params": {"seed": k},
                     "cost_weight": 3.0, "note": f"N-107's mlp with the network's random_state {k} (inner folds seed 0)"}
       for k in MLP_SEEDS if k},
    **{f"mlp_gp_s{k}": {"model": StratumMLPGP, "family": "H", "tuned": True, "grid": MLP_GRID, "grid_cut": GRID_CUT, "params": {"seed": k},
                        "sigma": True, "cost_weight": 4.0, "note": f"{_NOTE_HYBRID}; MLP seed {k}"}
       for k in MLP_SEEDS},
}
#: the seeds of one model: N-107's ``mlp`` is seed 0 of the MLP alone
SEED_GROUPS = {"mlp": ["mlp", *(f"mlp_s{k}" for k in MLP_SEEDS if k)], "mlp_gp": [f"mlp_gp_s{k}" for k in MLP_SEEDS]}
#: modules whose source is part of these variants' job key besides this one
DEPENDS = ("regressors_neural",)
