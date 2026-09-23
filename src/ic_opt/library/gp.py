"""Per-stratum forward Gaussian-process model: one per (stratum, quantity), never across metal bodies.

Ported from em-opt ``surrogate/model.py`` (2026-09-23), numerically unchanged: kernel
``Constant x (RBF | Matern 5/2) + White``, ``normalize_y``, 4 optimiser restarts, ``random_state=0``;
min-max scaling on the nominal ranges; ``per_nt`` fits one sub-GP per integer turns level with at least
``MIN_NT_SAMPLES`` rows over the other dims, ``joint`` fits one GP over all dims; log targets predict
``exp(mu_log)`` with the delta-method sigma, and ``prediction_bounds`` gives the exact k-sigma interval in
log space.

Changed after the T13.0 verification:
- a turns level without a sub-GP is not an exception: ``predict`` returns NaN there and ``available``
  says which rows the model can answer (the query layer reports those as domain criterion 2);
- ``k_scale`` widens every interval; ``calibration_scale`` derives it from held-out residuals so the
  nominal 2-sigma interval covers 95% (never narrower than the GP's own);
- the transformer dimensionless k feature maps are not here: they come with T13.7 in the new
  primary / secondary vocabulary and are re-validated on the new transformer library.
"""

from __future__ import annotations

import warnings

import numpy as np
from sklearn.exceptions import ConvergenceWarning
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import RBF, ConstantKernel, Matern, WhiteKernel

KERNELS = ("rbf", "matern52")
NT_MODES = ("joint", "per_nt")
SEEDS, HOLDOUT = (0, 1, 2, 3, 4), 0.2               # the T13.0 verification protocol


def prediction_bounds(mu, sigma, *, log_target: bool, k: float) -> tuple[np.ndarray, np.ndarray]:
    """Exact k-sigma bounds from a ``predict`` pair: in log space for a log target (``sigma / mu`` is sigma_log)."""
    mu = np.asarray(mu, dtype=float)
    sigma = np.asarray(sigma, dtype=float)
    if log_target:
        sigma_log = sigma / np.maximum(np.abs(mu), 1e-300)
        return mu * np.exp(-k * sigma_log), mu * np.exp(k * sigma_log)
    return mu - k * sigma, mu + k * sigma


def _kernel(n_dims: int, kernel: str):
    if kernel == "rbf":
        base = RBF(length_scale=[0.3] * n_dims, length_scale_bounds=(1e-2, 1e2))
    elif kernel == "matern52":
        base = Matern(nu=2.5, length_scale=[0.3] * n_dims, length_scale_bounds=(1e-2, 1e2))
    else:
        raise ValueError(f"unknown kernel {kernel!r}; expected one of {KERNELS}")
    return ConstantKernel(1.0, (1e-3, 1e3)) * base + WhiteKernel(1e-6, (1e-10, 1e-1))


class StratumGP:
    MIN_NT_SAMPLES = 25                              # a turns level needs this many rows for its own sub-GP

    def __init__(self, *, dims: list[str], ranges: dict[str, tuple[float, float]], log_target: bool = False,
                 nt_mode: str = "joint", kernel: str = "rbf", nt_dim: str | None = None, k_scale: float = 1.0):
        if nt_mode not in NT_MODES:
            raise ValueError(f"unknown nt_mode {nt_mode!r}; expected one of {NT_MODES}")
        if kernel not in KERNELS:
            raise ValueError(f"unknown kernel {kernel!r}; expected one of {KERNELS}")
        if nt_mode == "per_nt" and (nt_dim is None or nt_dim not in dims):
            raise ValueError(f"per_nt needs an nt_dim among the dims {dims}")
        self.dims, self.ranges, self.log_target = list(dims), dict(ranges), log_target
        self.nt_mode, self.kernel, self.nt_dim, self.k_scale = nt_mode, kernel, nt_dim, k_scale
        self._gp: GaussianProcessRegressor | None = None
        self._sub: dict[int, StratumGP] = {}
        self._nt_idx: int | None = None
        self.unavailable_nt: dict[int, int] = {}      # level -> rows, for levels below MIN_NT_SAMPLES

    # -- fit ---------------------------------------------------------------------------------------------

    def fit(self, x, y) -> StratumGP:
        x, y = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
        if x.ndim != 2 or x.shape[1] != len(self.dims):
            raise ValueError(f"expected [N, {len(self.dims)}] inputs")
        if self.log_target and (y <= 0).any():
            raise ValueError("log_target requires positive targets")
        if self.nt_mode == "per_nt":
            self._nt_idx = self.dims.index(self.nt_dim)
            others = [d for i, d in enumerate(self.dims) if i != self._nt_idx]
            levels = np.round(x[:, self._nt_idx]).astype(int)
            self._sub, self.unavailable_nt = {}, {}
            for level in sorted(set(levels.tolist())):
                mask = levels == level
                if mask.sum() < self.MIN_NT_SAMPLES:
                    self.unavailable_nt[level] = int(mask.sum())
                    continue
                sub = StratumGP(dims=others, ranges={d: self.ranges[d] for d in others}, log_target=self.log_target, kernel=self.kernel)
                self._sub[level] = sub.fit(np.delete(x[mask], self._nt_idx, axis=1), y[mask])
            return self
        self._gp = GaussianProcessRegressor(kernel=_kernel(len(self.dims), self.kernel), normalize_y=True,
                                            n_restarts_optimizer=4, random_state=0)
        with warnings.catch_warnings():              # hyperparameters at a bound (near-noiseless EM data) are expected, not news
            warnings.simplefilter("ignore", ConvergenceWarning)
            self._gp.fit(self._scale(x), np.log(y) if self.log_target else y)
        return self

    # -- predict -----------------------------------------------------------------------------------------

    def available(self, x) -> np.ndarray:
        """Rows the model can answer: all of them for a joint model; integer turns levels that got a sub-GP for per_nt."""
        x = np.asarray(x, dtype=float)
        if self.nt_mode != "per_nt":
            return np.ones(len(x), dtype=bool)
        levels = x[:, self.dims.index(self.nt_dim)]
        return np.array([abs(v - round(v)) <= 1e-9 and round(v) in self._sub for v in levels], dtype=bool)

    def predict(self, x) -> tuple[np.ndarray, np.ndarray]:
        """(mu, sigma) in the target's units; NaN where ``available`` is False. For log targets sigma is the delta-method exp(mu_log)*sigma_log."""
        x = np.asarray(x, dtype=float)
        if x.ndim != 2 or x.shape[1] != len(self.dims):
            raise ValueError(f"expected [N, {len(self.dims)}] inputs")
        if self.nt_mode == "per_nt":
            if self._nt_idx is None:
                raise RuntimeError("fit() first")
            mu, sigma = np.full(len(x), np.nan), np.full(len(x), np.nan)
            ok = self.available(x)
            levels = np.round(x[:, self._nt_idx]).astype(int)
            for level in sorted(set(levels[ok].tolist())):
                mask = ok & (levels == level)
                mu[mask], sigma[mask] = self._sub[level].predict(np.delete(x[mask], self._nt_idx, axis=1))
            return mu, sigma
        if self._gp is None:
            raise RuntimeError("fit() first")
        m, s = self._gp.predict(self._scale(x), return_std=True)
        return (np.exp(m), np.exp(m) * s) if self.log_target else (m, s)

    def predict_bounds(self, x, k: float = 2.0) -> tuple[np.ndarray, np.ndarray]:
        """k-sigma bounds, widened by ``k_scale``."""
        mu, sigma = self.predict(x)
        return prediction_bounds(mu, sigma, log_target=self.log_target, k=k * self.k_scale)

    def _scale(self, x: np.ndarray) -> np.ndarray:
        lo = np.array([self.ranges[d][0] for d in self.dims], dtype=float)
        hi = np.array([self.ranges[d][1] for d in self.dims], dtype=float)
        if not (hi > lo).all():
            raise ValueError(f"degenerate range for dims {[d for d, ok in zip(self.dims, hi > lo) if not ok]}")
        return (x - lo) / (hi - lo)


# -- held-out evaluation and calibration ---------------------------------------------------------------------

def split(n: int, seed: int, holdout: float = HOLDOUT) -> tuple[np.ndarray, np.ndarray]:
    """(test, train) index arrays: the first ``round(holdout * n)`` of a seeded permutation are held out."""
    order = np.random.default_rng(seed).permutation(n)
    k = round(holdout * n)
    return order[:k], order[k:]


def holdout(x, y, *, seeds=SEEDS, fraction: float = HOLDOUT, **model) -> dict:
    """Seeded hold-out evaluation: relative errors and |z| of every scored held-out row, plus the rows skipped
    because their turns level had no sub-GP in that split. ``model`` goes to ``StratumGP`` (dims, ranges, ...)."""
    x, y = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    rel, z, inside, levels, skipped = [], [], [], [], 0
    for seed in seeds:
        test, train = split(len(y), seed, fraction)
        gp = StratumGP(**model).fit(x[train], y[train])
        ok = gp.available(x[test])
        skipped += int((~ok).sum())
        test = test[ok]
        mu, sigma = gp.predict(x[test])
        lo, hi = prediction_bounds(mu, sigma, log_target=gp.log_target, k=2.0)
        yt = y[test]
        rel += (np.abs(mu - yt) / np.abs(yt)).tolist()
        inside += ((yt >= lo) & (yt <= hi)).tolist()
        if gp.log_target:
            z += (np.abs(np.log(yt) - np.log(mu)) / np.maximum(sigma / np.maximum(np.abs(mu), 1e-300), 1e-300)).tolist()
        else:
            z += (np.abs(yt - mu) / np.maximum(sigma, 1e-300)).tolist()
        if gp.nt_dim is not None:
            levels += np.round(x[test, gp.dims.index(gp.nt_dim)]).astype(int).tolist()
    rel_a = np.array(rel)
    by_level = {int(v): float(np.median(rel_a[np.array(levels) == v])) for v in sorted(set(levels))} if levels else {}
    return {"n": len(y), "n_scored": len(rel), "skipped_unfitted_level": skipped,
            "median_rel": float(np.median(rel_a)) if rel else float("nan"),
            "p90_rel": float(np.quantile(rel_a, 0.9)) if rel else float("nan"),
            "max_rel": float(rel_a.max()) if rel else float("nan"),
            "coverage_2sigma": float(np.mean(inside)) if inside else float("nan"),
            "median_rel_by_level": by_level, "z": z}


def calibration_scale(report: dict, level: float = 0.95, k: float = 2.0) -> float:
    """The widening that makes the nominal k-sigma interval cover ``level`` of held-out rows (at least 1)."""
    z = np.asarray(report["z"], dtype=float)
    if not len(z):
        return 1.0
    return max(1.0, float(np.quantile(z, level)) / k)
