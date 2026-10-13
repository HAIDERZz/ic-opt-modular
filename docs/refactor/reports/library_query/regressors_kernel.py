"""N-107, family A: PACOSYT's kernel and neighbour regressors behind the device library's model interface.

Every class is a ``regressor_base.StratumMean``: the library's inputs (min-max on the ranges or the table's feature map,
``per_nt`` where the column has ``nt_dim``, the inputs constant on the training rows dropped) and target (log y for a
positive column); the joint model on the kept, scaled inputs ``z`` and the fitted-space target ``t`` is the subclass's.
Specification: ``docs/refactor/N107_REGRESSOR_STUDY_SPEC.md`` section 2.

  gp_paper  sklearn ``GaussianProcessRegressor`` with PACOSYT's configuration: kernel
            ``0.1**2 * RationalQuadratic(length_scale=1e-8, alpha=1e-8) + 1**2 * RBF(length_scale=5)``, ``alpha=1e-8``,
            no optimiser restarts, ``random_state=0``; the inputs standardized on the training rows (their
            ``PreProcess(standardize=True)``). Nothing tuned: the paper tuned nothing, so this shows what the kernel
            configuration costs. The target (log y) is standardized inside the model (``normalize_y=True``, the
            coordinator's decision of 2026-10-13): without it the zero-mean prior meets log y near -20 (inductance in H)
            and the variant measures the library's units, not the kernel (the side check before the decision: p90 up to
            ~1e12 % on the inductance columns). A fit sklearn refuses (a kernel matrix that is not positive definite) is
            ``Unfit``.
  krr       sklearn ``KernelRidge(kernel="rbf")`` on the centred target (each fit, the inner ones included, subtracts
            its training rows' mean and adds it back at prediction: KernelRidge has no intercept), ``gamma`` = g / d with
            g in {1/16, 1/8, 1/4, 1/2, 1, 2, 4, 8, 16, 32, 64} and d the kept inputs, ``alpha`` in {1e-10, 1e-8, 1e-6,
            1e-4, 1e-2}; tuned by ``regressor_base.tune`` (55 configurations). Centring and the widened grid are the
            coordinator's decisions of 2026-10-13 (the specification's grid, g 1/4 ... 16 x alpha 1e-8 ... 1e-2, put the
            chosen value on an edge in about half of the transformer tables' fits).
  knn       sklearn ``KNeighborsRegressor(weights="distance")``, ``n_neighbors`` in {1, 2, 3, 5, 8, 12, 16} (1 is the
            paper's nearest neighbour; 12 and 16 added on the same decision); tuned. A configuration with more neighbours
            than an inner fold's training rows raises in sklearn and scores inf there.

``VARIANTS`` (name -> class, family, tuned, grid, note) is this module's registration: ``regressor_study.py`` merges it
into its own registry when it imports the module. A fitted tuned (sub-)model gives the configuration chosen as
``chosen``, the inner cross-validation's RMSE of every configuration as ``cv_scores`` and the chosen one's as
``cv_rmse`` (B's and C's names); a ``gp_paper`` (sub-)model its fitted kernel as ``kernel_fitted``. The harness records
them per job, and the ``tune`` calls besides.
"""
from __future__ import annotations

import json
from typing import ClassVar

import numpy as np
from regressor_base import StratumMean, Unfit, tune
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import RBF, RationalQuadratic
from sklearn.kernel_ridge import KernelRidge
from sklearn.neighbors import KNeighborsRegressor
from sklearn.preprocessing import StandardScaler

SEED = 0
KRR_GAMMA_D = (1 / 16, 1 / 8, 1 / 4, 1 / 2, 1, 2, 4, 8, 16, 32, 64)   # gamma = g / d, d = the kept inputs
KRR_ALPHA = (1e-10, 1e-8, 1e-6, 1e-4, 1e-2)
KNN_NEIGHBORS = (1, 2, 3, 5, 8, 12, 16)


def paper_kernel():
    """PACOSYT's GPR kernel as its notebook writes it (sklearn turns each scalar into a ConstantKernel with its default
    bounds; the 1e-8 starting values lie below the default bounds of 1e-5 and the optimiser starts from there clipped)."""
    return 0.1**2 * RationalQuadratic(length_scale=1e-8, alpha=1e-8) + 1**2 * RBF(length_scale=5)


class StratumGPPaper(StratumMean):
    MODEL = "gp_paper"

    def _fit_joint(self, z: np.ndarray, t: np.ndarray) -> None:
        self._scaler = StandardScaler().fit(z)
        self._gpr = GaussianProcessRegressor(kernel=paper_kernel(), alpha=1e-8, n_restarts_optimizer=0, normalize_y=True,
                                             random_state=SEED)
        try:
            self._gpr.fit(self._scaler.transform(z), t)
        except (np.linalg.LinAlgError, ValueError) as exc:
            first = str(exc).splitlines()[0] if str(exc) else ""
            raise Unfit(f"{len(t)} rows, {z.shape[1]} inputs: sklearn refused the fit: {type(exc).__name__}: {first[:240]}") from exc
        self.kernel_fitted = str(self._gpr.kernel_)

    def _predict_joint(self, z: np.ndarray) -> np.ndarray:
        return self._gpr.predict(self._scaler.transform(z))


def _krr(cfg: dict, d: int) -> KernelRidge:
    return KernelRidge(kernel="rbf", gamma=cfg["gamma_d"] / d, alpha=cfg["alpha"])


def _krr_centred(cfg: dict, d: int, zt: np.ndarray, tt: np.ndarray, zv: np.ndarray) -> np.ndarray:
    """One inner fit of the centred KernelRidge: its own training rows' mean out, back in at the prediction."""
    mean = float(np.mean(tt))
    return _krr(cfg, d).fit(zt, tt - mean).predict(zv) + mean


class StratumKRR(StratumMean):
    MODEL = "krr"
    GRID: ClassVar[dict] = {"gamma_d": KRR_GAMMA_D, "alpha": KRR_ALPHA}

    def _fit_joint(self, z: np.ndarray, t: np.ndarray) -> None:
        d = z.shape[1]
        configs = [{"gamma_d": g, "alpha": a} for g in KRR_GAMMA_D for a in KRR_ALPHA]
        self.chosen, self.cv_scores = tune(z, t, configs, lambda cfg, zt, tt, zv: _krr_centred(cfg, d, zt, tt, zv), seed=SEED)
        self.cv_rmse = self.cv_scores[json.dumps(self.chosen, sort_keys=True)]
        self._mean = float(np.mean(t))
        self._model = _krr(self.chosen, d).fit(z, t - self._mean)

    def _predict_joint(self, z: np.ndarray) -> np.ndarray:
        return self._model.predict(z) + self._mean


def _knn(cfg: dict) -> KNeighborsRegressor:
    return KNeighborsRegressor(n_neighbors=cfg["n_neighbors"], weights="distance")


class StratumKNN(StratumMean):
    MODEL = "knn"
    GRID: ClassVar[dict] = {"n_neighbors": KNN_NEIGHBORS}

    def _fit_joint(self, z: np.ndarray, t: np.ndarray) -> None:
        configs = [{"n_neighbors": k} for k in KNN_NEIGHBORS]
        self.chosen, self.cv_scores = tune(z, t, configs, lambda cfg, zt, tt, zv: _knn(cfg).fit(zt, tt).predict(zv), seed=SEED)
        self.cv_rmse = self.cv_scores[json.dumps(self.chosen, sort_keys=True)]
        self._model = _knn(self.chosen).fit(z, t)

    def _predict_joint(self, z: np.ndarray) -> np.ndarray:
        return self._model.predict(z)


VARIANTS = {
    "gp_paper": {"cls": StratumGPPaper, "family": "A", "tuned": False, "grid": None,
                 "note": "sklearn GaussianProcessRegressor, PACOSYT's kernel 0.1**2 * RationalQuadratic(1e-8, alpha 1e-8) + "
                         "1**2 * RBF(5), alpha 1e-8, no restarts, inputs standardized, target standardized (normalize_y); untuned"},
    "krr": {"cls": StratumKRR, "family": "A", "tuned": True, "grid": StratumKRR.GRID,
            "note": "sklearn KernelRidge(kernel='rbf') on the centred target, gamma = g / d, alpha; inner 3-fold CV"},
    "knn": {"cls": StratumKNN, "family": "A", "tuned": True, "grid": StratumKNN.GRID,
            "note": "sklearn KNeighborsRegressor(weights='distance'); inner 3-fold CV (1 neighbour is the paper's NN)"},
}
