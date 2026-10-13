"""N-107 part C: the neural regressors behind the device library's model interface -- ``mlp``, scikit-learn's multilayer
perceptron tuned by the inner cross-validation, and ``mlp_paper``, PACOSYT's ANN as its notebook builds it, untuned.
Specification: ``docs/refactor/N107_REGRESSOR_STUDY_SPEC.md`` (section 2, family C). Point errors only (sigma NaN).

Both subclass ``regressor_base.StratumMean``, so they see what the library's GP sees: the feature map or the min-max
scaling on the ranges, one sub-model per turns level for ``per_nt``, the log target for a positive column, the inputs
constant on a (sub-)model's training rows dropped. ``_fit_joint(z, t)`` gets the kept, scaled inputs and the fitted-space
target (log y for a log target):

  mlp        ``MLPRegressor(solver="lbfgs", max_iter=2000, random_state=0)`` on ``z``; the target standardized inside the
             model -- fitted on ``(t - mean) / std`` of the rows it is fitted on, ``* std + mean`` at the prediction (both
             in the inner folds and in the final fit). ``tune`` (3 folds, seed 0, RMSE in the fitted space) picks one of
             ``MLP_GRID`` -- the specification's 12 configurations less the four with hidden_layer_sizes (256, 256),
             cut for cost (``GRID_CUT``, measured below the grid) -- and the chosen one is then fitted once on all the
             training rows.
  mlp_paper  ``StandardScaler`` (on the training rows) -> ``PolynomialFeatures(degree=5)`` -> ``MLPRegressor(
             hidden_layer_sizes=(256, 512), activation="relu", solver="adam", alpha=1e-6, max_iter=2000, random_state=0)``
             on ``t`` as it is: PACOSYT's ``PreProcess(standardize=True)``, polynomial features and two dense layers
             [256, 512] relu with L2 1e-6 under adam, re-implemented in scikit-learn (no TensorFlow). Nothing tuned.

Scikit-learn's ``ConvergenceWarning`` is silenced (a solver stopping at ``max_iter`` is the configuration, not news);
each final fit's iteration count (adam: epochs) is kept in ``fit_info``. A fit whose weights come out non-finite
(scikit-learn's ``ValueError`` "Solver produced non-finite parameter weights") raises ``regressor_base.Unfit``: recorded,
never substituted; in the inner cross-validation such a configuration scores inf (``tune``). BLAS is capped at
``n_threads`` while a model fits.

What a fitted model says (all JSON-serialisable; a ``per_nt`` model answers per turns level, keyed by the level as a
string, from the sub-models it has -- a level below ``MIN_NT_SAMPLES`` rows has none):
  chosen        the configuration fitted: one of ``MLP_GRID`` for ``mlp``, ``PAPER`` for ``mlp_paper``
                (joint: a dict; per_nt: ``{"1": dict, "2": dict, ...}``)
  inner_scores  ``mlp`` only: ``tune``'s ``{json(config): RMSE}`` (None for ``mlp_paper``)
  fit_info      rows, kept inputs, iterations of the final fit (``n_iter``), seconds; ``mlp`` adds ``inner_s`` (per
                configuration, its three inner folds together) and ``refit_s``; ``mlp_paper`` ``poly_features``

Registry, in N-104's shape with the class itself under ``"model"``: ``VARIANTS = {name: {"model": cls, "family": "C",
"inputs": "library", "params": {...}, "grid": [...] | None, "grid_cut": [...]}}`` -- ``params`` are the constructor
keywords the variant adds to the library's settings (none: the defaults are the ones described here), ``grid`` the inner
cross-validation's configurations (None: untuned), ``grid_cut`` the specification's configurations not run. A harness
builds a model as ``cls(**settings, **params, n_threads=threads)``.

Smoke (one direct column, an 80/20 ``gp.split``):
  regressors_neural.py LIBRARY_ROOT TABLE COLUMN --cache-dir DIR [--variants mlp,mlp_paper] [--threads 2] [--seed 0]
Run with OMP_NUM_THREADS=2, IC_OPT_PROFILE_DIRS set and PYTHONPATH at the source tree; the library is only read
(``query.Library(root, calibrate=False, cache_dir=DIR)``).
"""
from __future__ import annotations

import argparse
import itertools
import json
import sys
import time
import warnings
from pathlib import Path

import numpy as np
import sklearn
from sklearn.exceptions import ConvergenceWarning
from sklearn.neural_network import MLPRegressor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import PolynomialFeatures, StandardScaler
from threadpoolctl import threadpool_limits

from ic_opt.library import gp, query

_HERE = str(Path(__file__).resolve().parent)       # regressor_base sits beside this file, not in a package
if _HERE not in sys.path:
    sys.path.append(_HERE)
from regressor_base import StratumMean, Unfit, tune

#: the specification's grid for ``mlp``, in its order (a tie goes to the first)
SPEC_GRID = [{"hidden_layer_sizes": h, "alpha": a, "activation": act}
             for h, a, act in itertools.product(((64, 64), (128, 128), (256, 256)), (1e-5, 1e-3), ("relu", "tanh"))]
#: the grid run: SPEC_GRID without hidden_layer_sizes (256, 256), cut for cost (the specification's rule: under a minute
#: per configuration, about 10 minutes per fold job). Measured on xfm_bs_m10 k_lf, 1769 training rows, 2 threads: the
#: four (256, 256) configurations took 57-71 s each for their three inner fits (248 of the 354 s of the inner
#: cross-validation), the 64 / 128 ones 7-19 s; a composed fold job (Lp@28: three tuned parts) took 651 s on the full grid
GRID_CUT = [c for c in SPEC_GRID if c["hidden_layer_sizes"] == (256, 256)]
MLP_GRID = [c for c in SPEC_GRID if c not in GRID_CUT]
MLP_FIXED = {"solver": "lbfgs", "max_iter": 2000, "random_state": 0}
INNER_FOLDS, INNER_SEED = 3, 0
#: PACOSYT's ANN as the specification re-implements it
PAPER = {"degree": 5, "hidden_layer_sizes": (256, 512), "activation": "relu", "solver": "adam", "alpha": 1e-6,
         "max_iter": 2000, "random_state": 0}
NONFINITE = "non-finite parameter weights"            # scikit-learn's refusal of a diverged fit


def _key(cfg: dict) -> str:
    return json.dumps(cfg, sort_keys=True)            # tune's key of a configuration


def _quiet_fit(model, z: np.ndarray, t: np.ndarray):
    """``model.fit(z, t)`` with lbfgs / adam stopping at ``max_iter`` not reported; a diverged fit is ``Unfit``."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", ConvergenceWarning)
            return model.fit(z, t)
    except ValueError as exc:
        if NONFINITE in str(exc):
            raise Unfit(f"{len(t)} rows, {z.shape[1]} inputs: {exc}") from exc
        raise


class StandardizedMLP:
    """``MLPRegressor(**cfg, **MLP_FIXED)`` on the target standardized over the rows it is fitted on, undone at the
    prediction (a constant target keeps std 1)."""

    def __init__(self, cfg: dict):
        self.cfg = dict(cfg)
        self.net = MLPRegressor(**self.cfg, **MLP_FIXED)
        self.mean, self.std = 0.0, 1.0

    def fit(self, z: np.ndarray, t: np.ndarray) -> StandardizedMLP:
        self.mean, std = float(np.mean(t)), float(np.std(t))
        self.std = std if std > 0 else 1.0
        _quiet_fit(self.net, z, (t - self.mean) / self.std)
        return self

    def predict(self, z: np.ndarray) -> np.ndarray:
        return self.net.predict(z) * self.std + self.mean


class _Neural(StratumMean):
    """What both variants report about a fit; a per_nt model gathers it from its turns levels' sub-models."""

    def __init__(self, **settings):
        super().__init__(**settings)
        self._model = None
        self._chosen: dict | None = None
        self._scores: dict[str, float] | None = None
        self._info: dict | None = None

    def _gather(self, attr: str):
        if self.nt_mode == "per_nt":
            return {str(level): getattr(sub, attr) for level, sub in sorted(self._sub.items())}
        return getattr(self, attr)

    @property
    def chosen(self):
        return self._gather("_chosen")

    @property
    def inner_scores(self):
        return self._gather("_scores")

    @property
    def fit_info(self):
        return self._gather("_info")

    def _predict_joint(self, z: np.ndarray) -> np.ndarray:
        if self._model is None:
            raise RuntimeError("fit() first")
        return self._model.predict(z)


class StratumMLP(_Neural):
    """``mlp``: the lbfgs MLP on the standardized target, tuned over ``grid`` (default ``MLP_GRID``)."""

    MODEL = "mlp"

    def __init__(self, *, grid: list[dict] | None = None, **settings):
        super().__init__(**settings)
        self.grid = [dict(c) for c in (MLP_GRID if grid is None else grid)]

    def own_params(self) -> dict:
        return {"grid": self.grid}

    def _fit_joint(self, z: np.ndarray, t: np.ndarray) -> None:
        seconds: dict[str, float] = {}

        def fit_predict(cfg: dict, z_tr: np.ndarray, t_tr: np.ndarray, z_te: np.ndarray) -> np.ndarray:
            t0 = time.perf_counter()
            try:
                return StandardizedMLP(cfg).fit(z_tr, t_tr).predict(z_te)
            finally:
                seconds[_key(cfg)] = seconds.get(_key(cfg), 0.0) + time.perf_counter() - t0

        with threadpool_limits(limits=self.n_threads):
            best, scores = tune(z, t, self.grid, fit_predict, folds=INNER_FOLDS, seed=INNER_SEED)
            t0 = time.perf_counter()
            self._model = StandardizedMLP(best).fit(z, t)
            refit_s = time.perf_counter() - t0
        self._chosen, self._scores = best, scores
        self._info = {"rows": len(t), "inputs": int(z.shape[1]), "n_iter": int(self._model.net.n_iter_),
                      "inner_s": {k: round(v, 3) for k, v in seconds.items()}, "refit_s": round(refit_s, 3)}


class StratumPaperANN(_Neural):
    """``mlp_paper``: PACOSYT's ANN -- standardized inputs, polynomial features of degree 5, MLP (256, 512) relu under
    adam with L2 1e-6 -- on the fitted-space target as it is; untuned."""

    MODEL = "mlp_paper"

    def _fit_joint(self, z: np.ndarray, t: np.ndarray) -> None:
        net = {k: v for k, v in PAPER.items() if k != "degree"}
        pipe = Pipeline([("standardize", StandardScaler()), ("poly", PolynomialFeatures(degree=PAPER["degree"])),
                         ("ann", MLPRegressor(**net))])
        t0 = time.perf_counter()
        with threadpool_limits(limits=self.n_threads):
            _quiet_fit(pipe, z, t)
        self._model, self._chosen = pipe, dict(PAPER)
        self._info = {"rows": len(t), "inputs": int(z.shape[1]), "poly_features": int(pipe["poly"].n_output_features_),
                      "n_iter": int(pipe["ann"].n_iter_), "fit_s": round(time.perf_counter() - t0, 3)}


VARIANTS = {"mlp": {"model": StratumMLP, "family": "C", "inputs": "library", "params": {}, "grid": MLP_GRID,
                    "grid_cut": GRID_CUT},
            "mlp_paper": {"model": StratumPaperANN, "family": "C", "inputs": "library", "params": {}, "grid": None,
                          "grid_cut": []}}


# -- smoke ----------------------------------------------------------------------------------------------------------------

def _cfg_label(cfg: dict) -> str:
    return f"{tuple(cfg['hidden_layer_sizes'])} alpha {cfg['alpha']:g} {cfg['activation']}"


def _per_level(value, per_nt: bool) -> dict:
    return value if per_nt else {"joint": value}


def smoke(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="N-107 C smoke: each neural variant on one direct column, 80/20 gp.split")
    ap.add_argument("root", type=Path)
    ap.add_argument("table")
    ap.add_argument("column")
    ap.add_argument("--cache-dir", type=Path, required=True)
    ap.add_argument("--variants", default=",".join(VARIANTS))
    ap.add_argument("--threads", type=int, default=2)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args(argv)
    names = args.variants.split(",")
    if unknown := [v for v in names if v not in VARIANTS]:
        raise SystemExit(f"unknown variants {unknown}; known {list(VARIANTS)}")
    lib = query.Library(args.root, calibrate=False, cache_dir=args.cache_dir)
    if lib._plan(args.table, args.column) is not None:
        raise SystemExit(f"{args.table} {args.column} is a composed column; the smoke takes a direct one")
    _ds, _rows, x, y, settings = lib._fit_inputs(args.table, args.column)
    test, train = gp.split(len(y), args.seed)
    per_nt = settings["nt_mode"] == "per_nt"
    print(f"{args.table} {args.column}: {len(y)} rows, train {len(train)} / test {len(test)} (gp.split seed {args.seed}); "
          f"nt_mode {settings['nt_mode']}, feature_map {settings['feature_map']}, log_target {settings['log_target']}; "
          f"{args.threads} threads; sklearn {sklearn.__version__}, numpy {np.__version__}", flush=True)
    for name in names:
        entry = VARIANTS[name]
        t0 = time.perf_counter()
        try:
            model = entry["model"](**settings, **entry["params"], n_threads=args.threads).fit(x[train], y[train])
        except Unfit as exc:
            print(f"\n{name}: unfit after {time.perf_counter() - t0:.1f} s -- {exc}", flush=True)
            continue
        t1 = time.perf_counter()
        with threadpool_limits(limits=args.threads):
            ok = model.available(x[test])
            mu, _sigma = model.predict(x[test][ok], floor=False)
        t2 = time.perf_counter()
        yt = y[test][ok]
        rel = np.where(np.isfinite(mu), np.abs(mu - yt) / np.abs(yt), np.inf)
        print(f"\n{name}: held-out median {np.median(rel):.4%}, p90 {np.quantile(rel, 0.9):.4%}, max {rel.max():.4%} "
              f"over {len(rel)} rows ({int((~ok).sum())} skipped, {int((~np.isfinite(mu)).sum())} non-finite); "
              f"fit {t1 - t0:.1f} s, predict {t2 - t1:.3f} s", flush=True)
        chosen = _per_level(model.chosen, per_nt)
        info = _per_level(model.fit_info, per_nt)
        if entry["grid"] is None:
            for level, cfg in chosen.items():
                i = info[level]
                print(f"  {level}: {i['rows']} rows, {i['inputs']} inputs -> {i['poly_features']} polynomial features, "
                      f"{i['n_iter']} epochs, {i['fit_s']:.1f} s", flush=True)
            print(f"  configuration {json.dumps(next(iter(chosen.values())), sort_keys=True)}", flush=True)
            continue
        scores = _per_level(model.inner_scores, per_nt)
        levels = list(chosen)
        print(f"  inner cross-validation ({INNER_FOLDS} folds, seed {INNER_SEED}): RMSE in the fitted space per (sub-)model, "
              f"* chosen; seconds = the three inner fits of a configuration, summed over the (sub-)models", flush=True)
        print(f"  {'configuration':<28}" + "".join(f"{lv:>12}" for lv in levels) + f"{'seconds':>10}", flush=True)
        for cfg in entry["grid"]:
            k = _key(cfg)
            cells = "".join(f"{scores[lv][k]:>11.5f}{'*' if _key(chosen[lv]) == k else ' '}" for lv in levels)
            print(f"  {_cfg_label(cfg):<28}{cells}{sum(info[lv]['inner_s'][k] for lv in levels):>10.1f}", flush=True)
        for lv in levels:
            i = info[lv]
            print(f"  {lv}: {i['rows']} rows, {i['inputs']} inputs; chosen {_cfg_label(chosen[lv])}; final fit "
                  f"{i['n_iter']} iterations, {i['refit_s']:.1f} s; inner {sum(i['inner_s'].values()):.1f} s", flush=True)


if __name__ == "__main__":
    smoke()
