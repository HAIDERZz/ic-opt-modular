"""A curve column predicted from the stratum's other models: ``model: ratio`` and ``model: resonance`` (T16.2b).

A column sampled at an anchor frequency, such as ``Lp@40``, holds two things at once: the winding's low-frequency
inductance, which changes smoothly with the geometry, and the rise of the apparent inductance towards the
self-resonance, which is steep wherever the resonance comes close to the anchor. One GP of its own on that column
(``model: direct``) has to learn both, with the short length scales of the steep part; between the sampled levels of
a dim it can be tens of per cent off. The T16.2a study (``docs/refactor/reports/library_query/
XFM_ANCHOR_MODEL_STUDY_CN.html``) measured that on the single-turn transformer tables and found the fix in two steps:
split off the parts that are known or smooth, and give what is left the inputs in which it varies slowly (the
curve's ``feature_map``). A composed model predicts, in log space,

- ``ratio``:      log L(f0) = log L_lf(x) + log R(x)
- ``resonance``:  log L(f0) = log L_lf(x) + log (1 / (1 - (f0 / SRF(x))**2)) + log Res(x)

L_lf is the stratum's model of the curve's base scalar -- the low-frequency value (``Lp_lf`` for ``Lp``, ``Ls_lf`` for
``Ls``, ``k_lf`` for ``k``) or, for a Q curve, its peak (``Qp_peak`` for ``Qp``, ``Qs_peak`` for ``Qs``; ``ratio`` only,
N-19: log Q(f0) = log Q_peak(x) + log R(x)) -- and SRF its system-SRF model: the very model objects the library answers
those columns with, fitted once and shared (``attach``). R is a GP of its own on the measured ratio L(f0) / L_lf, and
Res one on that ratio divided by the ideal rise of a parallel resonance at the SRF the SRF model predicts; each is
fitted on the rows that have the curve and the base value, both positive (the fit is in log space), and, for
``resonance``, a predicted SRF above f0 (below it the rise does not exist), with the curve's own settings,
``feature_map`` included. f0 is given in the SRF model's own unit (the library fits SRF in GHz; ``query.fit_unit``), so
no conversion happens here.

Where the SRF model puts the resonance at or below sqrt(1 / U_MAX) f0 = 1.118 f0, (f0 / SRF)**2 is held at U_MAX: the
rise stays finite (5) and its derivative 2u / (1 - u) = 8 carries the SRF's uncertainty into a wide interval, which the
confidence gate then flags. Anchored rows exist only where the measured SRF lies above srf_margin x f0 (1.25 f0 by
default), so no training row is near that hold.

Sigma treats the parts as independent: var_log = var_lf + g**2 var_log_SRF + var_part, with g = 2u / (1 - u) and
u = (f0 / SRF)**2 the derivative of the log rise with respect to log SRF. ``posterior_cov`` adds the parts'
posterior covariances under the same assumption: cov_lf + (g g^T) * cov_SRF + cov_part, whose diagonal is the variance
``predict(floor=False)`` reports. ``predict`` floors sigma at ``sigma_floor_rel`` of the mean and ``predict_bounds``
widens by ``k_scale``, exactly as for ``gp.StratumGP``; both come from ``holdout``, the calibration: the 5 x 20 %
seeded hold-out of the curve's usable rows (the direct model's protocol), except that every fold refits every part --
L_lf, SRF and R / Res -- on that fold's training rows, so the held-out rows are predicted by models that never saw
them. ``holdout_fold`` is one seed of it, so that the library can run the folds in parallel processes. A row whose
curve value is <= 0 is left out of the hold-out, as it is of the fit (log space can neither fit nor score it), and
counted: ``dropped_nonpositive``.

A pickled composed model holds only its own part; the shared L_lf and SRF models are attached again when it is loaded
(``Library`` keys its cache file by theirs).
"""

from __future__ import annotations

import numpy as np

from ic_opt.library import gp

KINDS = ("ratio", "resonance")
U_MAX = 0.8                                          # (f0 / SRF)**2 at most: the rise is held at 1 / (1 - U_MAX) = 5 below 1.118 f0


def rise(srf, f0: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(u = (f0 / SRF)**2 held at U_MAX, the log of the ideal resonance rise 1 / (1 - u), and g = 2u / (1 - u), the
    magnitude of that log's derivative with respect to log SRF) for SRF values in f0's unit; NaN where SRF is NaN."""
    srf = np.asarray(srf, dtype=float)
    with np.errstate(divide="ignore", over="ignore", invalid="ignore"):
        u = np.minimum((f0 / srf) ** 2, U_MAX)
    return u, -np.log1p(-u), 2.0 * u / (1.0 - u)


class ComposedGP:
    """A curve column's model built on the stratum's base-scalar (and SRF) models; the ``gp.StratumGP`` interface the
    library uses (see the module docstring)."""

    log_target = True                               # always: the composition is a sum in log space
    nt_mode = "composed"

    def __init__(self, *, kind: str, f0: float, dims: list[str], ranges: dict[str, tuple[float, float]], nt_dim: str | None,
                 part: dict, names: dict[str, str | None], k_scale: float = 1.0, sigma_floor_rel: float = 0.0):
        if kind not in KINDS:
            raise ValueError(f"unknown composed model {kind!r}; expected one of {KINDS}")
        if not f0 > 0:
            raise ValueError(f"f0 must be positive, got {f0!r}")
        if kind == "resonance" and not names.get("srf"):
            raise ValueError("a resonance model needs the name of its SRF model")
        self.kind, self.f0 = kind, float(f0)
        self.dims, self.ranges, self.nt_dim = list(dims), dict(ranges), nt_dim
        self.part_settings = dict(part)             # StratumGP settings of R / Res (the curve's own, feature_map included)
        self.feature_map = self.part_settings.get("feature_map")
        self.names = {"lf": names["lf"], "srf": names.get("srf") if kind == "resonance" else None}
        self.k_scale, self.sigma_floor_rel = k_scale, float(sigma_floor_rel)
        self.part: gp.StratumGP | None = None
        self.lf: gp.StratumGP | None = None
        self.srf: gp.StratumGP | None = None
        self.n_train = 0                            # the most training rows of any part: what a prediction call's memory scales with

    # -- the parts ------------------------------------------------------------------------------------------------

    def attach(self, lf: gp.StratumGP, srf: gp.StratumGP | None = None, *, n_train: int = 0) -> ComposedGP:
        """Use these fitted models as L_lf (and SRF): the library's own, shared; ``n_train`` is the most rows any part was
        fitted on."""
        for label, model in (("lf", lf), ("srf", srf)):
            if model is not None and list(model.dims) != self.dims:
                raise ValueError(f"the {label} model is over {list(model.dims)}, this one over {self.dims}")
        if self.kind == "resonance" and srf is None:
            raise ValueError("a resonance model needs its SRF model")
        self.lf, self.srf = lf, srf if self.kind == "resonance" else None
        self.n_train = max(self.n_train, int(n_train))
        return self

    def _ready(self) -> None:
        if self.lf is None or (self.kind == "resonance" and self.srf is None):
            raise RuntimeError("attach() the base (and SRF) models first")

    def fit(self, x, y, *, lf_y) -> ComposedGP:
        """Fit R (``ratio``) or Res (``resonance``) on the rows of ``x`` that have the curve ``y`` and the base value ``lf_y``
        (NaN: not measured), both positive, and for ``resonance`` a predicted SRF above f0; the other rows are left out.
        L_lf and SRF must be attached; they are not refitted here."""
        self._ready()
        x, y, lf_y = np.asarray(x, dtype=float), np.asarray(y, dtype=float), np.asarray(lf_y, dtype=float)
        with np.errstate(divide="ignore", invalid="ignore"):
            ok = np.isfinite(y) & np.isfinite(lf_y) & (y > 0) & (lf_y > 0)
            target = y / lf_y
            if self.kind == "resonance":
                srf, _ = self.srf.predict(x, floor=False)
                ok &= np.isfinite(srf) & (srf > self.f0)                         # where the rise exists
                u, _, _ = rise(srf, self.f0)
                target = target * (1 - u)                                         # the ratio over the ideal rise 1 / (1 - u)
        if not ok.any():
            raise ValueError("no row has a positive curve value and base value (and a predicted SRF above f0) to fit on")
        self.part = gp.StratumGP(**self.part_settings).fit(x[ok], target[ok])
        self.n_train = max(self.n_train, int(ok.sum()))
        return self

    # -- the gp.StratumGP interface ---------------------------------------------------------------------------------

    @property
    def unavailable_nt(self) -> dict[int, int]:
        """Turns levels some part has no sub-model for (level -> that part's rows there)."""
        out: dict[int, int] = {}
        for model in (self.lf, self.srf, self.part):
            if model is not None:
                for level, rows in model.unavailable_nt.items():
                    out[level] = max(out.get(level, 0), rows)
        return out

    def available(self, x) -> np.ndarray:
        """Rows every part can answer."""
        self._ready()
        x = np.asarray(x, dtype=float)
        ok = self.lf.available(x) & self.part.available(x)
        return ok & self.srf.available(x) if self.srf is not None else ok

    def terms(self, x) -> dict[str, np.ndarray]:
        """The composition per row, in log space: ``lf`` / ``lf_sigma``, ``part`` / ``part_sigma`` and, for resonance,
        ``srf`` / ``srf_sigma`` (log SRF in the SRF model's unit), ``rise`` (log of the ideal rise) and ``g``. The sigmas
        are the parts' own, unfloored. NaN where a part is not available."""
        self._ready()
        if self.part is None:
            raise RuntimeError("fit() first")
        x = np.asarray(x, dtype=float)
        parts = (("lf", self.lf), ("part", self.part)) + ((("srf", self.srf),) if self.kind == "resonance" else ())
        out, srf = {}, None
        with np.errstate(divide="ignore", invalid="ignore"):
            for label, model in parts:
                mu, sigma = model.predict(x, floor=False)
                out[label], out[f"{label}_sigma"] = np.log(mu), sigma / mu
                srf = mu if label == "srf" else srf
        if srf is not None:
            _, out["rise"], out["g"] = rise(srf, self.f0)
        return out

    def _log(self, t: dict[str, np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
        """(log mean, log sigma) from ``terms``: the logs add, and so do the variances of independent parts."""
        if self.kind == "ratio":
            return t["lf"] + t["part"], np.sqrt(t["lf_sigma"] ** 2 + t["part_sigma"] ** 2)
        return t["lf"] + t["rise"] + t["part"], np.sqrt(t["lf_sigma"] ** 2 + t["part_sigma"] ** 2 + (t["g"] * t["srf_sigma"]) ** 2)

    def predict(self, x, *, floor: bool = True) -> tuple[np.ndarray, np.ndarray]:
        """(mu, sigma) in the curve's units; NaN where ``available`` is False; sigma = mu x the log-space sigma (the delta
        method, as for a log-target StratumGP), never below ``sigma_floor_rel`` x mu unless ``floor`` is False."""
        m, s = self._log(self.terms(x))
        mu = np.exp(m)
        sigma = mu * s
        if floor and self.sigma_floor_rel > 0:
            with np.errstate(invalid="ignore"):
                sigma = np.maximum(sigma, self.sigma_floor_rel * np.abs(mu))
        return mu, sigma

    def predict_bounds(self, x, k: float = 2.0) -> tuple[np.ndarray, np.ndarray]:
        """k-sigma bounds, widened by ``k_scale`` (exact in log space)."""
        mu, sigma = self.predict(x)
        return gp.prediction_bounds(mu, sigma, log_target=True, k=k * self.k_scale)

    def posterior_cov(self, x) -> tuple[np.ndarray, np.ndarray]:
        """(log mean, covariance in log space) at the rows of ``x``: the parts' posterior covariances added as independent,
        the SRF's scaled by the rise's derivative on both sides (g_i g_j cov_SRF_ij). Its diagonal is the variance
        ``predict(floor=False)`` reports; NaN where a part is not available."""
        self._ready()
        x = np.asarray(x, dtype=float)
        m_lf, c_lf = self.lf.posterior_cov(x)
        m_part, c_part = self.part.posterior_cov(x)
        if self.kind == "ratio":
            return m_lf + m_part, c_lf + c_part
        m_srf, c_srf = self.srf.posterior_cov(x)
        _, log_rise, g = rise(np.exp(m_srf), self.f0)
        return m_lf + log_rise + m_part, c_lf + c_part + np.outer(g, g) * c_srf

    def explain(self, x, srf_unit: float = 1.0) -> list[dict]:
        """Per row, where the value comes from, in the curve's units: the base value under its column's name (the
        low-frequency value, or a Q curve's peak), the ratio (``ratio``) or the SRF (times ``srf_unit``: in its column's
        unit), the ideal rise and the residual (``resonance``); their product is the predicted value."""
        t = self.terms(x)
        rows = []
        for i in range(len(t["lf"])):
            entry = {"model": self.kind, self.names["lf"]: float(np.exp(t["lf"][i]))}
            if self.kind == "ratio":
                entry.update({"formula": f"{self.names['lf']} x ratio", "ratio": float(np.exp(t["part"][i]))})
            else:
                entry.update({"formula": f"{self.names['lf']} x resonance_factor x residual", self.names["srf"]: float(np.exp(t["srf"][i])) * srf_unit,
                              "resonance_factor": float(np.exp(t["rise"][i])), "residual": float(np.exp(t["part"][i]))})
            rows.append(entry)
        return rows

    # -- pickling: only the part; L_lf and SRF are the library's and are attached again on load ----------------------

    def __getstate__(self) -> dict:
        state = dict(self.__dict__)
        state["lf"] = state["srf"] = None
        return state


# -- calibration ------------------------------------------------------------------------------------------------------

def holdout_fold(x, y, lf_y, srf_y, seed: int, *, kind: str, f0: float, dims: list[str], ranges: dict, nt_dim: str | None,
                 part: dict, lf: dict, srf: dict | None, names: dict, fraction: float = gp.HOLDOUT) -> dict:
    """One seed of ``holdout``. ``x`` holds every row of the stratum; ``y``, ``lf_y`` and ``srf_y`` the curve, its base
    scalar and the system SRF (in the SRF model's unit) per row, NaN where a row has no usable value; ``part``, ``lf``
    and ``srf`` are the three models' StratumGP settings. The seed's held-out rows are ``gp.split``'s of the curve's
    usable rows, as a direct model's calibration holds them out -- usable meaning a positive value: log space can
    neither fit nor score a value <= 0, so such a row is left out like a row without the curve, and counted
    (``nonpositive``); every part is refitted on its own usable rows minus the held-out ones. Returns the held-out rows'
    relative errors, |z| (against the unfloored sigma), whether each lies inside the 2-sigma interval, their turns
    levels, the rows skipped because a part had no model for their level and the curve's rows left out as <= 0."""
    x, y, lf_y = np.asarray(x, dtype=float), np.asarray(y, dtype=float), np.asarray(lf_y, dtype=float)
    positive = np.isfinite(y) & (y > 0)                  # the curve's rows a log-space model can fit and score
    curve = np.flatnonzero(positive)
    test_i, _ = gp.split(len(curve), seed, fraction)
    held = np.zeros(len(y), dtype=bool)
    held[curve[test_i]] = True
    lf_rows = np.isfinite(lf_y) & ~held
    lf_model = gp.StratumGP(**lf).fit(x[lf_rows], lf_y[lf_rows])
    srf_model = None
    if kind == "resonance":
        srf_y = np.asarray(srf_y, dtype=float)
        srf_rows = np.isfinite(srf_y) & ~held
        srf_model = gp.StratumGP(**srf).fit(x[srf_rows], srf_y[srf_rows])
    model = ComposedGP(kind=kind, f0=f0, dims=dims, ranges=ranges, nt_dim=nt_dim, part=part, names=names).attach(lf_model, srf_model)
    train = np.isfinite(y) & ~held
    model.fit(x[train], y[train], lf_y=lf_y[train])
    test = curve[test_i]
    ok = model.available(x[test])
    test = test[ok]
    mu, sigma = model.predict(x[test], floor=False)
    lo, hi = gp.prediction_bounds(mu, sigma, log_target=True, k=2.0)
    yt = y[test]
    z = np.abs(np.log(yt) - np.log(mu)) / np.maximum(sigma / np.maximum(np.abs(mu), 1e-300), 1e-300)
    levels = np.round(x[test, dims.index(nt_dim)]).astype(int).tolist() if nt_dim is not None else []
    return {"seed": seed, "rel": (np.abs(mu - yt) / np.abs(yt)).tolist(), "z": z.tolist(), "inside": ((yt >= lo) & (yt <= hi)).tolist(),
            "levels": levels, "skipped": int((~ok).sum()), "n": len(curve), "nonpositive": int((np.isfinite(y) & ~positive).sum())}


def merge(folds: list[dict]) -> dict:
    """``holdout_fold`` results, in seed order, as the report ``gp.holdout`` gives (``calibration_scale`` reads its z),
    plus ``dropped_nonpositive``: the curve's rows left out as <= 0 (the same rows in every fold)."""
    rel = [v for f in folds for v in f["rel"]]
    z = [v for f in folds for v in f["z"]]
    inside = [v for f in folds for v in f["inside"]]
    levels = [v for f in folds for v in f["levels"]]
    rel_a = np.array(rel)
    by_level = {int(v): float(np.median(rel_a[np.array(levels) == v])) for v in sorted(set(levels))} if levels else {}
    return {"n": folds[0]["n"] if folds else 0, "n_scored": len(rel), "skipped_unfitted_level": sum(f["skipped"] for f in folds),
            "dropped_nonpositive": folds[0]["nonpositive"] if folds else 0,
            "median_rel": float(np.median(rel_a)) if rel else float("nan"),
            "p90_rel": float(np.quantile(rel_a, 0.9)) if rel else float("nan"),
            "max_rel": float(rel_a.max()) if rel else float("nan"),
            "coverage_2sigma": float(np.mean(inside)) if inside else float("nan"),
            "median_rel_by_level": by_level, "z": z, "rel": rel}


def holdout(x, y, lf_y, srf_y, *, seeds=gp.SEEDS, **settings) -> dict:
    """The seeded hold-out calibration of a composed model: ``holdout_fold`` for every seed, merged."""
    return merge([holdout_fold(x, y, lf_y, srf_y, seed, **settings) for seed in seeds])
