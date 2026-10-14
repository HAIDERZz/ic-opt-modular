"""The calibration folds of a composed curve on stable rows: each part fitted once per fold, shared by every curve (N-125).

A composed curve (``model: ratio`` / ``model: resonance``, ``ic_opt.library.composed``) is calibrated by five seeded
hold-outs of 20 %, and every fold has to predict its held-out rows with models that never saw them: the curve's own part
(the ratio R or the residual Res) and the parts it is built on -- the base scalar's model (``Lp_lf``, ``Ls_lf``, ``k_lf``,
``Qp_peak``, ``Qs_peak``) and, for ``resonance``, the system SRF's. ``composed.holdout_fold`` draws a fold's held-out rows
from the curve's own usable rows, which differ from frequency to frequency, so every curve refits every part on every fold:
a table's Lp_lf model of fold 3 was fitted again for Lp@12, Lp@17, Lp@24, ... and the SRF model for each of them and for
every Ls as well -- on a stacked-transformer table, 30 of the 60 fits a new frequency took (N-122).

Here fold ``s`` holds out the same rows for every column of the stratum: S_s, the first round(0.2 x N) entries of
``gp.split``'s seeded permutation of all N rows of the dataset (``held``; s in ``gp.SEEDS``, 0.2 = ``gp.HOLDOUT``). For a
composed column c:

- part p's model of fold s is fitted on the rows usable for p (a finite value) less S_s (``fit_part``). It depends on the
  dataset, p's settings and s only -- not on c --, so the library fits it once and keeps it as a file of its own
  (``Library._part_fold``) for every curve built on p, at any frequency;
- c's own model (R or Res) is fitted on the rows with a finite value of c less S_s, on those part models (``fold``);
- the scored rows are S_s's rows with a positive value of c, scored exactly as ``composed.holdout_fold`` scores its
  held-out rows: relative error, |z| against the unfloored sigma, inside the 2-sigma interval, turns levels, the rows
  skipped because a model has no sub-model for their level, ``n`` (c's positive rows) and ``nonpositive`` (c's finite
  rows <= 0, which a log-space model can neither fit nor score). ``composed.merge`` merges the five folds as it merges
  ``holdout_fold``'s.

No held-out row is seen by any model its fold predicts with: every model of fold s is fitted without S_s, and the scored
rows lie in S_s.

What differs from ``composed.holdout_fold`` besides the rows: a fold of S_s may hold out none of a sparse column's rows
(about 0.8**n of the time for a column of n rows); it then scores nothing instead of handing scikit-learn an empty set,
and the other folds calibrate the column. A fold that holds out rows of the column but whose models answer none of them
raises as ``composed.holdout_fold`` does (a joint model refuses an empty set), and so does a calibration with a joint
model among the three whose five folds held out none of the column's rows (``report``): it would have nothing to be
calibrated on. ``query.stable_composed_fits`` is where these raise, decided from the rows.

The library calibrates a curve on these folds where they can calibrate it and the folds of before could too
(``Library._stable``): every curve but one with a handful of rows the shared folds cannot score or fit on. Such a curve
-- and one that can have no model (N-120) -- is calibrated on ``composed.holdout_fold``'s folds as before, so which
curves have a model, and where a fit raises, are what they were. ``composed.holdout_fold`` also stays the definition of
every calibration cached before: the library keeps using it under its earlier name (``Library._composed_calibration``).
"""

from __future__ import annotations

import numpy as np

from ic_opt.library import composed, gp

STABLE = "stable-rows"                               # the calibration's "folds", hashed into its file name
SOURCE = ("holdout 5x20% of the stratum's rows, the same rows for every column; each part fitted once per fold on the other "
          "rows and shared by the stratum's composed curves")


def held(n: int, seed: int) -> np.ndarray:
    """Fold ``seed``'s held-out rows S_s of a stratum of ``n`` rows, in ``gp.split``'s order: the first round(``gp.HOLDOUT`` x n)
    entries of its seeded permutation of all the rows -- the same rows whichever column is calibrated."""
    return gp.split(n, seed, gp.HOLDOUT)[0]


def mask(n: int, seed: int) -> np.ndarray:
    """``held`` as a boolean mask over the ``n`` rows."""
    out = np.zeros(n, dtype=bool)
    out[held(n, seed)] = True
    return out


def fit_part(x, y, seed: int, settings: dict) -> gp.StratumGP:
    """Part p's model of fold ``seed``: a ``gp.StratumGP`` with p's ``settings`` fitted on the rows of ``x`` (every row of the
    stratum) where p's values ``y`` (in its model's unit, NaN where a row has none) are finite, less the fold's held-out rows."""
    x, y = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    rows = np.isfinite(y) & ~mask(len(y), seed)
    return gp.StratumGP(**settings).fit(x[rows], y[rows])


def fold(x, y, lf_y, seed: int, *, lf: gp.StratumGP, srf: gp.StratumGP | None, kind: str, f0: float, dims: list[str], ranges: dict,
         nt_dim: str | None, part: dict, names: dict) -> dict:
    """One fold of a composed curve's calibration on stable rows. ``x`` holds every row of the stratum, ``y`` and ``lf_y`` the
    curve and its base scalar per row (NaN where a row has no value); ``lf`` and ``srf`` are the parts' models of this fold
    (``fit_part``; ``srf`` None for ``ratio``), ``part`` the curve's own StratumGP settings. The curve's own model is fitted
    on the rows with a finite curve value outside the fold's held-out rows (``composed.ComposedGP.fit`` keeps those with a
    positive curve and base value and, for ``resonance``, a predicted SRF above f0); the held-out rows with a positive
    curve value are scored as ``composed.holdout_fold`` scores its own, with the same keys."""
    x, y, lf_y = np.asarray(x, dtype=float), np.asarray(y, dtype=float), np.asarray(lf_y, dtype=float)
    order = held(len(y), seed)
    out = np.zeros(len(y), dtype=bool)
    out[order] = True
    positive = np.isfinite(y) & (y > 0)              # the curve's rows a log-space model can fit and score
    model = composed.ComposedGP(kind=kind, f0=f0, dims=dims, ranges=ranges, nt_dim=nt_dim, part=part, names=names).attach(lf, srf)
    train = np.isfinite(y) & ~out
    model.fit(x[train], y[train], lf_y=lf_y[train])
    report = {"seed": seed, "rel": [], "z": [], "inside": [], "levels": [], "skipped": 0, "n": int(positive.sum()),
              "nonpositive": int((np.isfinite(y) & ~positive).sum())}
    test = order[positive[order]]                    # S_s's rows of this curve, in gp.split's order
    if not len(test):                                # the fold holds out none of the curve's rows: nothing to score
        return report
    ok = model.available(x[test])
    test = test[ok]
    mu, sigma = model.predict(x[test], floor=False)  # as composed.holdout_fold: a joint model refuses an empty set
    lo, hi = gp.prediction_bounds(mu, sigma, log_target=True, k=2.0)
    yt = y[test]
    z = np.abs(np.log(yt) - np.log(mu)) / np.maximum(sigma / np.maximum(np.abs(mu), 1e-300), 1e-300)
    report.update(rel=(np.abs(mu - yt) / np.abs(yt)).tolist(), z=z.tolist(), inside=((yt >= lo) & (yt <= hi)).tolist(),
                  levels=np.round(x[test, dims.index(nt_dim)]).astype(int).tolist() if nt_dim is not None else [],
                  skipped=int((~ok).sum()))
    return report


def joint(settings: dict) -> bool:
    """Whether any of a composed curve's three models (its own part, the base scalar's, the SRF's) is one joint GP rather than
    one per turns level: ``settings`` as ``Library._composed_settings`` gives them."""
    return any(s is not None and s["nt_mode"] != "per_nt" for s in (settings["part"], settings["lf"], settings["srf"]))


def report(results: list[dict], settings: dict, column: str) -> dict:
    """The five folds merged (``composed.merge``). Refused (ValueError) when a model among the three is joint and no fold held
    out a row of the column: such a calibration would score nothing, where ``composed.holdout_fold``'s refuses a joint
    column's fold without a held-out row."""
    merged = composed.merge(results)
    if joint(settings) and merged["n_scored"] + merged["skipped_unfitted_level"] == 0:
        raise ValueError(f"no fold holds out a row of {column} ({merged['n']} rows with a positive value): nothing to calibrate "
                         "its model on")
    return merged
