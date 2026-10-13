"""N-107: the base of every regressor measured behind the device library's model interface.

``StratumMean`` is ``gp.StratumGP`` with the joint model left to a subclass: the same constructor and attributes, the
same min-max / feature-map scaling (``_scale``), the same ``per_nt`` split (one sub-model per turns level with at least
``MIN_NT_SAMPLES`` rows), the same log target. Like N-104's ``StratumRBF`` it drops the scaled inputs that are constant
on each (sub-)model's training rows, at the fit and at the prediction. Sigma is NaN unless a subclass gives one: these
studies score the mean only. A subclass implements ``_fit_joint(z, t)`` and ``_predict_joint(z)`` on the kept, scaled
inputs ``z`` and the fitted-space target ``t`` (log y for a log target), and ``own_params()`` -- the settings a sub-model
of a turns level must get again. ``tune`` is the inner cross-validation every tuned regressor uses: the configuration
with the lowest RMSE in the fitted space over ``folds`` seeded folds of the training rows, nothing of the held-out rows.

Import this module before any job rebinds ``gp.StratumGP``: ``_GP`` must be the library's own class.
"""
from __future__ import annotations

import json
from collections.abc import Callable

import numpy as np
from sklearn.model_selection import KFold

from ic_opt.library import gp

_GP = gp.StratumGP                                   # the library's class, captured before any rebinding


class Unfit(Exception):
    """A model that cannot be fitted on this fold (too few rows, a singular system, ...): recorded, not an error."""


class StratumMean(_GP):
    MODEL = "mean"

    def __init__(self, *, n_threads: int = 1, **settings):
        super().__init__(**settings)
        self.n_threads = int(n_threads)
        self._keep: np.ndarray | None = None          # the scaled inputs that vary on the training rows

    # -- what a subclass gives ------------------------------------------------------------------------------------------

    def own_params(self) -> dict:
        """The subclass's own settings, handed to each turns level's sub-model (``n_threads`` included by the base)."""
        return {}

    def _fit_joint(self, z: np.ndarray, t: np.ndarray) -> None:
        raise NotImplementedError

    def _predict_joint(self, z: np.ndarray) -> np.ndarray:
        raise NotImplementedError

    # -- the gp.StratumGP interface ------------------------------------------------------------------------------------

    def fit(self, x, y):
        x, y = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
        if x.ndim != 2 or x.shape[1] != len(self.dims):
            raise ValueError(f"expected [N, {len(self.dims)}] inputs")
        if self.log_target and (y <= 0).any():
            raise ValueError("log_target requires positive targets")
        if self.nt_mode == "per_nt":                  # as gp.StratumGP: one sub-model per turns level with enough rows
            self._nt_idx = self.dims.index(self.nt_dim)
            others = [d for i, d in enumerate(self.dims) if i != self._nt_idx]
            levels = np.round(x[:, self._nt_idx]).astype(int)
            self._sub, self.unavailable_nt = {}, {}
            for level in sorted(set(levels.tolist())):
                mask = levels == level
                if mask.sum() < self.MIN_NT_SAMPLES:
                    self.unavailable_nt[level] = int(mask.sum())
                    continue
                sub = type(self)(dims=others, ranges={d: self.ranges[d] for d in others}, log_target=self.log_target,
                                 kernel=self.kernel, n_threads=self.n_threads, **self.own_params())
                self._sub[level] = sub.fit(np.delete(x[mask], self._nt_idx, axis=1), y[mask])
            return self
        z = self._scale(x)
        self._keep = np.ptp(z, axis=0) > 0
        if not self._keep.any():
            raise Unfit(f"{len(y)} rows, every one of the {z.shape[1]} inputs constant")
        self._fit_joint(z[:, self._keep], np.log(y) if self.log_target else y)
        return self

    def _predict(self, x) -> tuple[np.ndarray, np.ndarray]:
        if self.nt_mode == "per_nt":
            return super()._predict(x)                # the base class's per_nt path, on the sub-models
        x = np.asarray(x, dtype=float)
        if x.ndim != 2 or x.shape[1] != len(self.dims):
            raise ValueError(f"expected [N, {len(self.dims)}] inputs")
        if self._keep is None:
            raise RuntimeError("fit() first")
        m = np.asarray(self._predict_joint(self._scale(x)[:, self._keep]), dtype=float).reshape(-1)
        sigma = np.full(len(x), np.nan)
        with np.errstate(over="ignore"):
            return (np.exp(m), sigma) if self.log_target else (m, sigma)

    def posterior_cov(self, x):
        raise NotImplementedError(f"{self.MODEL} has no posterior")


def tune(z: np.ndarray, t: np.ndarray, configs: list[dict],
         fit_predict: Callable[[dict, np.ndarray, np.ndarray, np.ndarray], np.ndarray],
         *, folds: int = 3, seed: int = 0) -> tuple[dict, dict[str, float]]:
    """Inner cross-validation on the training rows only: ``fit_predict(config, z_train, t_train, z_test)`` returns the
    predictions in the fitted space; the configuration with the lowest RMSE over the folds wins (ties: the first).
    Returns (best config, {json(config): rmse}). A configuration that raises on any fold scores inf."""
    n = len(t)
    k = max(2, min(folds, n))
    splitter = KFold(n_splits=k, shuffle=True, random_state=seed)
    scores: dict[str, float] = {}
    for cfg in configs:
        key = json.dumps(cfg, sort_keys=True)
        err = []
        try:
            for tr, te in splitter.split(z):
                pred = np.asarray(fit_predict(cfg, z[tr], t[tr], z[te]), dtype=float).reshape(-1)
                err.append(np.mean((pred - t[te]) ** 2))
            scores[key] = float(np.sqrt(np.mean(err)))
        except Exception:  # noqa: BLE001 -- a singular fit, too few rows for this configuration, ...: the config scores inf
            scores[key] = float("inf")
        if not np.isfinite(scores[key]):
            scores[key] = float("inf")
    best = min(configs, key=lambda c: scores[json.dumps(c, sort_keys=True)])
    if not np.isfinite(scores[json.dumps(best, sort_keys=True)]):
        raise Unfit("every configuration failed in the inner cross-validation")
    return best, scores
