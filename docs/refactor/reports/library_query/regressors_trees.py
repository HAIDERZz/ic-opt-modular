"""N-107, part B: the tree ensembles of the regressor study, behind the device library's model interface.

Three ``regressor_base.StratumMean`` subclasses (specification ``docs/refactor/N107_REGRESSOR_STUDY_SPEC.md``, section 2).
Each (sub-)model runs ``regressor_base.tune`` over its grid on its own training rows -- 3 seeded folds (seed 0), RMSE in
the fitted space (log y for a log target), the first best configuration wins -- and is then refitted on all of its
training rows with the configuration chosen. Nothing of the held-out rows enters the choice.

  rf    sklearn RandomForestRegressor, n_estimators 500, random_state 0, n_jobs n_threads (the fit; a prediction adds
        the trees in one thread, in their order, so that it is bitwise reproducible);
        grid min_samples_leaf {1, 2, 4} x max_features {1.0, 0.5, "sqrt"}  (9 configurations)
  gbm   sklearn HistGradientBoostingRegressor, early_stopping False, random_state 0 (it has no n_jobs: its OpenMP
        threads are capped at n_threads around every fit and prediction);
        grid learning_rate {0.03, 0.1} x max_leaf_nodes {7, 15, 31} x max_iter {300, 1000} x l2_regularization {0, 1} (24)
  lgbm  LightGBM LGBMRegressor, random_state 0, n_jobs n_threads, verbose -1;
        grid learning_rate {0.03, 0.1} x num_leaves {7, 15, 31} x n_estimators {300, 1000} x min_child_samples {5, 20} (24)
        -- not registered when lightgbm does not import; ``SKIPPED`` then says why.
The grids run in the order the specification lists the values (the first axis slowest), which decides a tie. Every other
estimator parameter is the library default of sklearn 1.3.2 / LightGBM 4.6.0.

Each class sees what the library's GP sees, through ``StratumMean``: the settings of ``query.Library._fit_inputs``
(feature map, per_nt where the table has an nt_dim and no feature map, min-max scaling on the ranges, log target) plus
``n_threads``; the inputs constant on a (sub-)model's training rows are dropped. A per_nt model tunes each turns level's
sub-model on that level's training rows alone. On a fitted model:
  ``chosen``     the configuration chosen (a dict) for a joint model; ``{turns level: configuration}`` for a per_nt one;
  ``cv_rmse``    the chosen configuration's inner cross-validation RMSE, in the same shape;
  ``cv_scores``  every configuration's RMSE (``{json(configuration): rmse}``), in the same shape;
  ``timing``     ``{"tune_s", "refit_s"}``: the inner cross-validation and the refit, summed over a per_nt model's levels.

``VARIANTS`` registers the variants in N-104's shape, ``{name: {"model": class, ...}}``, for the study's harness, which
imports this module (``regressor_base`` is imported from this file's directory, the module the harness imports as well).

Smoke (``__main__``): one direct column of one table, its x, y and settings from ``query.Library(root, calibrate=False,
cache_dir=SCRATCH)._fit_inputs``, the 80 / 20 split ``gp.split(n, seed)``; each variant fitted on the 80 % and scored on
the 20 %: the configuration chosen, the held-out median / p90 / max relative error, the fit time (inner cross-validation
included) and the prediction time. ``--gp`` adds the library's own GP on the same split, for reference.
  regressors_trees.py LIBRARY_ROOT SCRATCH TABLE COLUMN [--variants rf,gbm,lgbm] [--threads 2] [--seed 0] [--gp]
Run with OMP_NUM_THREADS=2, IC_OPT_PROFILE_DIRS set and PYTHONPATH at the source tree.
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import platform
import sys
import time
from pathlib import Path
from typing import ClassVar

import numpy as np
import sklearn
from sklearn.ensemble import HistGradientBoostingRegressor, RandomForestRegressor
from threadpoolctl import threadpool_limits

sys.path.insert(0, str(Path(__file__).parent))
# _GP: the library's StratumGP, captured by regressor_base at its import, before any job rebinds gp.StratumGP
from regressor_base import _GP, StratumMean, tune

from ic_opt.library import gp, query

try:
    import lightgbm
    from lightgbm import LGBMRegressor
except (ImportError, OSError) as exc:                 # not installed, or its shared library does not load
    lightgbm, LGBMRegressor = None, None
    LGBM_IMPORT_ERROR: str | None = f"{type(exc).__name__}: {exc}"
else:
    LGBM_IMPORT_ERROR = None


def grid(**axes: list) -> list[dict]:
    """Every combination of the axes' values, the first axis slowest."""
    return [dict(zip(axes, values)) for values in itertools.product(*axes.values())]


class TunedMean(StratumMean):
    """A ``StratumMean`` whose joint model is an estimator chosen by ``tune`` over ``GRID`` on the (sub-)model's training
    rows and refitted on all of them. A subclass gives ``GRID`` and ``_estimator(config)``."""

    GRID: ClassVar[list[dict]] = []

    def __init__(self, **settings):
        super().__init__(**settings)
        self._model = None
        self._chosen: dict | None = None
        self._cv_scores: dict[str, float] | None = None
        self._tune_s = self._refit_s = 0.0

    def _estimator(self, config: dict):
        raise NotImplementedError

    def _fit_model(self, config: dict, z: np.ndarray, t: np.ndarray):
        return self._estimator(config).fit(z, t)

    def _predict_model(self, model, z: np.ndarray) -> np.ndarray:
        return model.predict(z)

    def _fit_joint(self, z: np.ndarray, t: np.ndarray) -> None:
        def fit_predict(config, z_train, t_train, z_test):
            return self._predict_model(self._fit_model(config, z_train, t_train), z_test)

        t0 = time.perf_counter()
        best, self._cv_scores = tune(z, t, self.GRID, fit_predict)
        t1 = time.perf_counter()
        self._chosen = dict(best)                      # a copy: the grid's dicts are shared by every instance
        self._model = self._fit_model(self._chosen, z, t)
        self._tune_s, self._refit_s = t1 - t0, time.perf_counter() - t1

    def _predict_joint(self, z: np.ndarray) -> np.ndarray:
        if self._model is None:
            raise RuntimeError("fit() first")
        return self._predict_model(self._model, z)

    # -- what the harness records -----------------------------------------------------------------------------------------

    def _per_level(self, joint):
        return {level: joint(sub) for level, sub in self._sub.items()} if self.nt_mode == "per_nt" else joint(self)

    @property
    def chosen(self) -> dict | None:
        return self._per_level(lambda m: m._chosen)

    @property
    def cv_rmse(self) -> float | dict | None:
        return self._per_level(lambda m: None if m._chosen is None else m._cv_scores[json.dumps(m._chosen, sort_keys=True)])

    @property
    def cv_scores(self) -> dict | None:
        return self._per_level(lambda m: m._cv_scores)

    @property
    def timing(self) -> dict[str, float]:
        models = list(self._sub.values()) if self.nt_mode == "per_nt" else [self]
        return {"tune_s": sum(m._tune_s for m in models), "refit_s": sum(m._refit_s for m in models)}


class StratumRF(TunedMean):
    MODEL = "rf"
    GRID = grid(min_samples_leaf=[1, 2, 4], max_features=[1.0, 0.5, "sqrt"])

    def _estimator(self, config: dict) -> RandomForestRegressor:
        return RandomForestRegressor(n_estimators=500, random_state=0, n_jobs=self.n_threads, **config)

    def _predict_model(self, model: RandomForestRegressor, z: np.ndarray) -> np.ndarray:
        # sklearn's threads add the trees' predictions into one array in the order they finish: two runs differ in the last
        # bit, enough to flip tune's first-best tie between two configurations that grow the same trees (max_features 0.5
        # and "sqrt" resolve to the same number of inputs for up to 5 inputs). One thread adds them in the trees' order.
        model.n_jobs = 1
        return model.predict(z)


class StratumGBM(TunedMean):
    MODEL = "gbm"
    GRID = grid(learning_rate=[0.03, 0.1], max_leaf_nodes=[7, 15, 31], max_iter=[300, 1000], l2_regularization=[0, 1])

    def _estimator(self, config: dict) -> HistGradientBoostingRegressor:
        return HistGradientBoostingRegressor(early_stopping=False, random_state=0, **config)

    def _fit_model(self, config: dict, z: np.ndarray, t: np.ndarray):
        with threadpool_limits(limits=self.n_threads, user_api="openmp"):
            return super()._fit_model(config, z, t)

    def _predict_model(self, model, z: np.ndarray) -> np.ndarray:
        with threadpool_limits(limits=self.n_threads, user_api="openmp"):
            return model.predict(z)


class StratumLGBM(TunedMean):
    MODEL = "lgbm"
    GRID = grid(learning_rate=[0.03, 0.1], num_leaves=[7, 15, 31], n_estimators=[300, 1000], min_child_samples=[5, 20])

    def _estimator(self, config: dict):
        if LGBMRegressor is None:
            raise RuntimeError(f"lightgbm does not import ({LGBM_IMPORT_ERROR})")
        return LGBMRegressor(random_state=0, n_jobs=self.n_threads, verbose=-1, **config)


VARIANTS: dict[str, dict] = {
    "rf": {"model": StratumRF, "family": "B", "inputs": "library", "tuned": True, "grid": StratumRF.GRID},
    "gbm": {"model": StratumGBM, "family": "B", "inputs": "library", "tuned": True, "grid": StratumGBM.GRID},
    "lgbm": {"model": StratumLGBM, "family": "B", "inputs": "library", "tuned": True, "grid": StratumLGBM.GRID},
}
SKIPPED: dict[str, str] = {}                          # variant -> why it is not registered
if LGBMRegressor is None:
    SKIPPED["lgbm"] = f"lightgbm does not import ({LGBM_IMPORT_ERROR}); the variant is skipped"
    del VARIANTS["lgbm"]


# -- smoke --------------------------------------------------------------------------------------------------------------------

def _pct(v: float) -> str:
    return f"{100 * v:.3f}"


def smoke(args) -> None:
    variants = [v for v in args.variants.split(",") if v]
    for v in variants:
        if v not in VARIANTS:
            raise SystemExit(f"unknown or skipped variant {v!r}: registered {sorted(VARIANTS)}, skipped {SKIPPED}")
    lib = query.Library(args.root, calibrate=False, cache_dir=args.scratch)
    if lib._plan(args.table, args.column) is not None:
        raise SystemExit(f"{args.table} {args.column} is a composed column; the smoke fits a direct column")
    _ds, _rows, x, y, settings = lib._fit_inputs(args.table, args.column)
    test, train = gp.split(len(y), args.seed)
    print(f"N-107 B smoke: {args.table} {args.column} -- {len(y)} rows, {len(train)} fitted / {len(test)} held out "
          f"(gp.split seed {args.seed}, 20 %)")
    print(f"settings: dims {settings['dims']}, nt_mode {settings['nt_mode']} (nt_dim {settings['nt_dim']}), "
          f"feature_map {settings['feature_map']}, log_target {settings['log_target']}")
    print(f"threads: n_threads {args.threads}, OMP_NUM_THREADS {os.environ.get('OMP_NUM_THREADS')}; python "
          f"{platform.python_version()}, numpy {np.__version__}, scikit-learn {sklearn.__version__}, lightgbm "
          f"{lightgbm.__version__ if lightgbm else 'not importable'}; host {platform.node()}")
    if SKIPPED:
        print(f"skipped: {SKIPPED}")
    print(f"{'variant':8s} {'configs':>7s} {'scored':>6s} {'skipped':>7s} {'median %':>9s} {'p90 %':>8s} {'max %':>8s} "
          f"{'fit s':>8s} {'tune s':>8s} {'refit s':>8s} {'predict s':>9s}")
    for name in (["gp"] if args.gp else []) + variants:
        with threadpool_limits(limits=args.threads):
            t0 = time.perf_counter()
            if name == "gp":
                model = _GP(**settings).fit(x[train], y[train])
            else:
                model = VARIANTS[name]["model"](**settings, n_threads=args.threads).fit(x[train], y[train])
            t1 = time.perf_counter()
            ok = model.available(x[test])
            rows = test[ok]
            mu, _sigma = model.predict(x[rows], floor=False)
            t2 = time.perf_counter()
        finite = np.isfinite(mu)
        rel = np.where(finite, np.abs(mu - y[rows]) / np.abs(y[rows]), np.inf)
        med, p90, top = float(np.median(rel)), float(np.quantile(rel, 0.9)), float(rel.max())
        timing = getattr(model, "timing", {"tune_s": float("nan"), "refit_s": float("nan")})
        configs = len(VARIANTS[name]["grid"]) if name != "gp" else 0
        print(f"{name:8s} {configs:7d} {len(rows):6d} {int((~ok).sum()):7d} {_pct(med):>9s} {_pct(p90):>8s} {_pct(top):>8s} "
              f"{t1 - t0:8.2f} {timing['tune_s']:8.2f} {timing['refit_s']:8.2f} {t2 - t1:9.3f}"
              + (f"  ({int((~finite).sum())} non-finite)" if (~finite).any() else ""))
        if name != "gp":
            print(f"  chosen: {json.dumps(model.chosen, sort_keys=True)}")
            cv = model.cv_rmse
            cv = {k: round(v, 6) for k, v in cv.items()} if isinstance(cv, dict) else round(cv, 6)
            print(f"  inner-CV rmse of the chosen: {json.dumps(cv, sort_keys=True)}")
        sys.stdout.flush()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    p.add_argument("root", help="the library root (read only)")
    p.add_argument("scratch", help="the cache directory the Library writes to instead of the root's .cache")
    p.add_argument("table")
    p.add_argument("column")
    p.add_argument("--variants", default=",".join(VARIANTS))
    p.add_argument("--threads", type=int, default=2)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--gp", action="store_true", help="also the library's own GP on the same split")
    smoke(p.parse_args())


if __name__ == "__main__":
    main()
