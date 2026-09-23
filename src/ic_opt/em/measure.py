"""Device quantities from S-parameters under an ideal-balun topology (kernel moved from em-opt ``device_db/measure.py``).

The math is the Python side of the OCEAN parity proven on 2026-07-10 (max rel
err <= 4.7e-6 on Lp/Ls/Qp/Qs/k): S->Z via Z = z0 (I+S)(I-S)^-1; each ideal
balun contributes a common-mode clamp (V_plus + V_minus = 0) and a differential
drive (I_plus - I_minus = 2 Ia); grounded ports contribute V = 0.

Curves per drive N (names p, s): L<N>(f), Q<N>(f), plus k(f) for two drives.
Scalars: L<N>_lf, L<N>_res, Q<N>_peak, SRF_<N>, k_lf, and SRF -- the system SRF, the lowest finite SRF over
all drives (a one-drive device: SRF_p). A drive's first Im(Z) zero can be the other winding's resonance
reflected through the coupling or its own, depending on how deep the reflected dip goes, so SRF_<N> of a
coupled pair may jump between the two between neighbouring geometries; SRF does not.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

DRIVE_NAMES = ("p", "s")
LOW_FREQ_MAX_HZ = 3e9


class MeasureError(ValueError):
    """Raised when a quantity cannot be produced (fail-closed)."""


@dataclass(frozen=True)
class Topology:
    drives: list[tuple[int, int]]          # (plus, minus) 0-based sNp column indices
    grounded: list[int] = field(default_factory=list)
    low_freq_max_hz: float = LOW_FREQ_MAX_HZ

    @classmethod
    def from_labels(cls, drives: list[tuple[str, str]], grounded: list[str], columns: list[str]) -> Topology:
        index = {label: i for i, label in enumerate(columns)}
        return cls([(index[p], index[m]) for p, m in drives], [index[g] for g in grounded])


@dataclass
class Quantities:
    freqs: np.ndarray
    curves: dict[str, np.ndarray]          # Lp, Qp, [Ls, Qs, k] over freqs
    scalars: dict[str, float | None]       # L*_lf, L*_res, Q*_peak, SRF_* (None: no resonance in the sweep), k_lf

    def at(self, name: str, frequency_hz: float) -> float:
        """A curve value at the grid point nearest ``frequency_hz`` (no interpolation, like em-opt's --anchored)."""
        if name not in self.curves:
            raise MeasureError(f"unknown curve {name}; have {sorted(self.curves)}")
        i = int(np.argmin(np.abs(self.freqs - frequency_hz)))
        if abs(self.freqs[i] - frequency_hz) > 0.5 * _grid_step(self.freqs):
            raise MeasureError(f"{frequency_hz:g} Hz is outside the swept grid [{self.freqs[0]:g}, {self.freqs[-1]:g}]")
        return float(self.curves[name][i])


def s_to_z(s: np.ndarray, z0: float = 50.0) -> np.ndarray:
    """Z = z0 (I+S)(I-S)^-1 per frequency; a singular slot gets NaN without poisoning the batch."""
    n = s.shape[-1]
    eye = np.eye(n)
    z = np.empty_like(s)
    for i in range(s.shape[0]):
        try:
            z[i] = z0 * np.linalg.solve((eye - s[i]).T, (eye + s[i]).T).T
        except np.linalg.LinAlgError:
            z[i] = np.nan
    return z


def _mixed_mode_z(z: np.ndarray, topo: Topology) -> np.ndarray:
    """[F, D, D] differential impedance matrix from the full Z under the balun constraints."""
    n = z.shape[-1]
    d = len(topo.drives)
    if 2 * d + len(topo.grounded) != n:
        raise MeasureError(f"topology needs {2 * d + len(topo.grounded)} ports, the S-parameters have {n}")
    zm = np.empty((z.shape[0], d, d), dtype=complex)
    for fi in range(z.shape[0]):
        a = np.zeros((n, n), dtype=complex)
        b = np.zeros((n, d), dtype=complex)
        row = 0
        for p, m in topo.drives:             # V_plus + V_minus = 0
            a[row] = z[fi, p] + z[fi, m]
            row += 1
        for g in topo.grounded:              # V_g = 0
            a[row] = z[fi, g]
            row += 1
        for j, (p, m) in enumerate(topo.drives):   # I_plus - I_minus = 2 Ia
            a[row, p], a[row, m] = 1.0, -1.0
            b[row, j] = 2.0
            row += 1
        try:
            currents = np.linalg.solve(a, b)
        except np.linalg.LinAlgError:
            zm[fi] = np.nan
            continue
        v = z[fi] @ currents
        for i, (p, m) in enumerate(topo.drives):
            zm[fi, i] = v[p] - v[m]
    return zm


def _finite_lf_mean(freqs, values, low_freq_max_hz, what) -> float:
    mask = (freqs > 0) & (freqs <= low_freq_max_hz) & np.isfinite(values)
    if not mask.any():
        raise MeasureError(f"no finite low-frequency samples for {what}")
    return float(np.mean(values[mask]))


def _l_res(freqs, l_curve, srf, what) -> float:
    """L at the largest finite-L grid sample with 0 < f <= srf/5 (resonance-aware alternative to the low-frequency mean)."""
    mask = (freqs > 0) & (freqs <= srf / 5.0) & np.isfinite(l_curve)
    if not mask.any():
        raise MeasureError(f"no finite L sample at or below SRF/5 for drive {what}")
    idx = np.nonzero(mask)[0]
    return float(l_curve[idx[np.argmax(freqs[idx])]])


def _srf_first_sign_flip(freqs, imag_z) -> float | None:
    """First positive-to-non-positive sign flip of imag(Z), linearly interpolated; None when there is none (or already past it)."""
    mask = (freqs > 0) & np.isfinite(imag_z)
    f, v = freqs[mask], imag_z[mask]
    if len(f) < 2 or v[0] <= 0:
        return None
    flip = np.nonzero((v[:-1] > 0) & (v[1:] <= 0))[0]
    if len(flip) == 0:
        return None
    i = flip[0]
    return float(f[i] + (f[i + 1] - f[i]) * v[i] / (v[i] - v[i + 1]))


def _grid_step(freqs: np.ndarray) -> float:
    return float(np.min(np.diff(freqs))) if len(freqs) > 1 else float("inf")


def quantities(freqs: np.ndarray, s: np.ndarray, topo: Topology, *, z0: float = 50.0) -> Quantities:
    """Curves and scalars for every drive; ``L*_res`` is capped by the system SRF (the lowest finite SRF over all drives)."""
    names = DRIVE_NAMES[: len(topo.drives)]
    zm = _mixed_mode_z(s_to_z(s, z0=z0), topo)
    with np.errstate(divide="ignore", invalid="ignore"):
        w = 2 * math.pi * freqs
        curves: dict[str, np.ndarray] = {}
        scalars: dict[str, float | None] = {}
        for i, nm in enumerate(names):
            zii = zm[:, i, i]
            l_curve = np.where(w > 0, np.imag(zii) / np.where(w > 0, w, np.nan), np.nan)
            q_curve = np.where(freqs > 0, np.imag(zii) / np.real(zii), np.nan)
            curves[f"L{nm}"], curves[f"Q{nm}"] = l_curve, q_curve
            scalars[f"L{nm}_lf"] = _finite_lf_mean(freqs, l_curve, topo.low_freq_max_hz, f"L{nm}")
            qmask = np.isfinite(q_curve) & (freqs > 0)
            if not qmask.any():
                raise MeasureError(f"no finite Q samples for drive {nm}")
            scalars[f"Q{nm}_peak"] = float(np.max(q_curve[qmask]))
            scalars[f"SRF_{nm}"] = _srf_first_sign_flip(freqs, np.imag(zii))
        finite = [scalars[f"SRF_{nm}"] for nm in names if scalars[f"SRF_{nm}"] is not None]
        srf_cap = min(finite) if finite else None
        scalars["SRF"] = srf_cap
        for nm in names:
            scalars[f"L{nm}_res"] = scalars[f"L{nm}_lf"] if srf_cap is None else _l_res(freqs, curves[f"L{nm}"], srf_cap, nm)
        if len(topo.drives) == 2:
            im00, im11, im01 = np.imag(zm[:, 0, 0]), np.imag(zm[:, 1, 1]), np.imag(zm[:, 0, 1])
            with np.errstate(invalid="ignore"):
                k_curve = im01 / np.sqrt(np.where(im00 * im11 > 0, im00 * im11, np.nan))
            curves["k"] = k_curve
            scalars["k_lf"] = _finite_lf_mean(freqs, k_curve, topo.low_freq_max_hz, "k")
    return Quantities(freqs, curves, scalars)
