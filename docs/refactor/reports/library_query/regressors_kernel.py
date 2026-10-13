"""N-107, family A: PACOSYT's kernel and neighbour regressors behind the device library's model interface.

Every class is a ``regressor_base.StratumMean``: the library's inputs (min-max on the ranges or the table's feature map,
``per_nt`` where the column has ``nt_dim``, the inputs constant on the training rows dropped) and target (log y for a
positive column); the joint model on the kept, scaled inputs ``z`` and the fitted-space target ``t`` is the subclass's.
Specification: ``docs/refactor/N107_REGRESSOR_STUDY_SPEC.md`` section 2.

  gp_paper  sklearn ``GaussianProcessRegressor`` as PACOSYT configured it: kernel
            ``0.1**2 * RationalQuadratic(length_scale=1e-8, alpha=1e-8) + 1**2 * RBF(length_scale=5)``, ``alpha=1e-8``,
            no optimiser restarts, ``random_state=0``; the inputs standardized on the training rows (their
            ``PreProcess(standardize=True)``). Nothing tuned: the paper tuned nothing, so this shows what the kernel
            configuration costs. The target is the library's (log y) as it is: the specification standardizes the inputs
            only, so ``normalize_y`` keeps sklearn's default (False). A fit sklearn refuses (a kernel matrix that is not
            positive definite) is ``Unfit``.
  krr       sklearn ``KernelRidge(kernel="rbf")``, ``gamma`` = g / d with g in {0.25, 0.5, 1, 2, 4, 8, 16} and d the
            kept inputs, ``alpha`` in {1e-8, 1e-6, 1e-4, 1e-2}; tuned by ``regressor_base.tune`` (28 configurations).
            As the table says: no intercept, the target not centred.
  knn       sklearn ``KNeighborsRegressor(weights="distance")``, ``n_neighbors`` in {1, 2, 3, 5, 8} (1 is the paper's
            nearest neighbour); tuned. A configuration with more neighbours than an inner fold's training rows raises in
            sklearn and scores inf there.

``VARIANTS`` (name -> class, family, tuned, grid, note) is this module's registration: ``regressor_study.py`` merges it
into its own registry when it imports the module. The chosen configuration of a tuned (sub-)model is ``config`` (with
the inner cross-validation's scores in ``cv_rmse``); the harness records it per job from the ``tune`` calls.
"""
from __future__ import annotations

from typing import ClassVar

import numpy as np
from regressor_base import StratumMean, Unfit, tune
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import RBF, RationalQuadratic
from sklearn.kernel_ridge import KernelRidge
from sklearn.neighbors import KNeighborsRegressor
from sklearn.preprocessing import StandardScaler

SEED = 0
KRR_GAMMA_D = (0.25, 0.5, 1, 2, 4, 8, 16)            # gamma = g / d, d = the kept inputs
KRR_ALPHA = (1e-8, 1e-6, 1e-4, 1e-2)
KNN_NEIGHBORS = (1, 2, 3, 5, 8)


def paper_kernel():
    """PACOSYT's GPR kernel as its notebook writes it (sklearn turns each scalar into a ConstantKernel with its default
    bounds; the 1e-8 starting values lie below the default bounds of 1e-5 and the optimiser starts from there clipped)."""
    return 0.1**2 * RationalQuadratic(length_scale=1e-8, alpha=1e-8) + 1**2 * RBF(length_scale=5)


class StratumGPPaper(StratumMean):
    MODEL = "gp_paper"

    def _fit_joint(self, z: np.ndarray, t: np.ndarray) -> None:
        self._scaler = StandardScaler().fit(z)
        self._gpr = GaussianProcessRegressor(kernel=paper_kernel(), alpha=1e-8, n_restarts_optimizer=0, random_state=SEED)
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


class StratumKRR(StratumMean):
    MODEL = "krr"
    GRID: ClassVar[dict] = {"gamma_d": KRR_GAMMA_D, "alpha": KRR_ALPHA}

    def _fit_joint(self, z: np.ndarray, t: np.ndarray) -> None:
        d = z.shape[1]
        configs = [{"gamma_d": g, "alpha": a} for g in KRR_GAMMA_D for a in KRR_ALPHA]
        self.config, self.cv_rmse = tune(z, t, configs, lambda cfg, zt, tt, zv: _krr(cfg, d).fit(zt, tt).predict(zv), seed=SEED)
        self._model = _krr(self.config, d).fit(z, t)

    def _predict_joint(self, z: np.ndarray) -> np.ndarray:
        return self._model.predict(z)


def _knn(cfg: dict) -> KNeighborsRegressor:
    return KNeighborsRegressor(n_neighbors=cfg["n_neighbors"], weights="distance")


class StratumKNN(StratumMean):
    MODEL = "knn"
    GRID: ClassVar[dict] = {"n_neighbors": KNN_NEIGHBORS}

    def _fit_joint(self, z: np.ndarray, t: np.ndarray) -> None:
        configs = [{"n_neighbors": k} for k in KNN_NEIGHBORS]
        self.config, self.cv_rmse = tune(z, t, configs, lambda cfg, zt, tt, zv: _knn(cfg).fit(zt, tt).predict(zv), seed=SEED)
        self._model = _knn(self.config).fit(z, t)

    def _predict_joint(self, z: np.ndarray) -> np.ndarray:
        return self._model.predict(z)


VARIANTS = {
    "gp_paper": {"cls": StratumGPPaper, "family": "A", "tuned": False, "grid": None,
                 "note": "sklearn GaussianProcessRegressor, PACOSYT's kernel 0.1**2 * RationalQuadratic(1e-8, alpha 1e-8) + "
                         "1**2 * RBF(5), alpha 1e-8, no restarts, inputs standardized; untuned"},
    "krr": {"cls": StratumKRR, "family": "A", "tuned": True, "grid": StratumKRR.GRID,
            "note": "sklearn KernelRidge(kernel='rbf'), gamma = g / d, alpha; inner 3-fold CV"},
    "knn": {"cls": StratumKNN, "family": "A", "tuned": True, "grid": StratumKNN.GRID,
            "note": "sklearn KNeighborsRegressor(weights='distance'); inner 3-fold CV (1 neighbour is the paper's NN)"},
}
