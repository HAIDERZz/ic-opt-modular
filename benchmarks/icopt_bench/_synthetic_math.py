"""Vectorized formulas for the synthetic benchmark problems (``synthetic.py``).

Every function takes the last axis as the variable axis (``x.shape[-1] == d``) and works on a single point
(``x.shape == (d,)``) or a batch (``x.shape == (n, d)``) alike, so the same code serves ``Problem.evaluate`` (one
point at a time, from ``space.to_raw``) and ``benchmarks/tools/calibrate_synthetic.py`` (large batches, for the
threshold and reference search). Nothing here touches ``ic_opt.spec`` or ``ic_opt.space``: nothing here needs the
grid contract, only plain floats.
"""

from __future__ import annotations

import numpy as np

# -- syn_small_tight ----------------------------------------------------------------------------


def small_tight_metrics(u: np.ndarray) -> tuple[np.ndarray, ...]:
    """``u`` unit-cube coordinates, last axis (a, b, c, d). Returns (m1, m2, m3, m4, m5)."""
    ua, ub, uc, ud = u[..., 0], u[..., 1], u[..., 2], u[..., 3]
    m1 = 2 + 3 * ua - 2 * ub + 1.5 * ua * ud - uc**2
    m2 = 9 - 2.5 * ua + 1.2 * ub**2 + 0.8 * ud - 0.6 * ua * uc
    m3 = 1 + 4 * ub * (1 - ub) + ud - 1.5 * ua * ub
    m4 = -6 + 3 * uc + 4 * ud * (1 - ud) - ua
    m5 = 0.5 + 2 * ud - uc * ud + 0.7 * ub
    return m1, m2, m3, m4, m5


# -- syn_multimodal_small ------------------------------------------------------------------------

MULTIMODAL_C1 = np.array([0.15, 0.2, 0.25])
MULTIMODAL_C2 = np.array([0.85, 0.8, 0.75])
MULTIMODAL_D1 = 1.0
MULTIMODAL_D2 = 1.15 * MULTIMODAL_D1               # the second bowl 15% deeper
MULTIMODAL_WALL_CENTER = (MULTIMODAL_C1 + MULTIMODAL_C2) / 2
MULTIMODAL_WALL_R = 0.3                             # < half the distance between the two centres (0.524): a lens, not a full block


def multimodal_f(u: np.ndarray) -> np.ndarray:
    bowl1 = np.sum((u - MULTIMODAL_C1) ** 2, axis=-1) - MULTIMODAL_D1
    bowl2 = np.sum((u - MULTIMODAL_C2) ** 2, axis=-1) - MULTIMODAL_D2
    return np.minimum(bowl1, bowl2)


def multimodal_g(u: np.ndarray) -> np.ndarray:
    """< 0 away from the midpoint of the two bowls' centres, >= 0 in a ball around it: cuts the straight line between
    the bowls without blocking either bowl itself."""
    return MULTIMODAL_WALL_R**2 - np.sum((u - MULTIMODAL_WALL_CENTER) ** 2, axis=-1)


# -- syn_ackley10_c2 ------------------------------------------------------------------------------


def ackley10(x: np.ndarray) -> np.ndarray:
    d = x.shape[-1]
    sum_sq = np.sum(x**2, axis=-1)
    sum_cos = np.sum(np.cos(2 * np.pi * x), axis=-1)
    return -20 * np.exp(-0.2 * np.sqrt(sum_sq / d)) - np.exp(sum_cos / d) + 20 + np.e


def ackley10_constraints(x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """SCBO paper: c1 = sum(x) <= 0, c2 = ||x||_2 - 5 <= 0."""
    c1 = np.sum(x, axis=-1)
    c2 = np.linalg.norm(x, axis=-1) - 5
    return c1, c2


# -- syn_hartmann6_c1 -----------------------------------------------------------------------------

_H6_ALPHA = np.array([1.0, 1.2, 3.0, 3.2])
_H6_A = np.array([
    [10, 3, 17, 3.5, 1.7, 8],
    [0.05, 10, 17, 0.1, 8, 14],
    [3, 3.5, 1.7, 10, 17, 8],
    [17, 8, 0.05, 10, 0.1, 14],
])
_H6_P = 1e-4 * np.array([
    [1312, 1696, 5569, 124, 8283, 5886],
    [2329, 4135, 8307, 3736, 1004, 9991],
    [2348, 1451, 3522, 2883, 3047, 6650],
    [4047, 8828, 8732, 5743, 1091, 381],
])


def hartmann6(x: np.ndarray) -> np.ndarray:
    diff = x[..., None, :] - _H6_P                          # (..., 4, 6)
    inner = np.sum(_H6_A * diff**2, axis=-1)                # (..., 4)
    return -np.sum(_H6_ALPHA * np.exp(-inner), axis=-1)


# -- syn_levy20_c1 --------------------------------------------------------------------------------


def levy20(x: np.ndarray) -> np.ndarray:
    w = 1 + (x - 1) / 4
    term1 = np.sin(np.pi * w[..., 0]) ** 2
    wi = w[..., :-1]
    term2 = np.sum((wi - 1) ** 2 * (1 + 10 * np.sin(np.pi * wi + 1) ** 2), axis=-1)
    wd = w[..., -1]
    term3 = (wd - 1) ** 2 * (1 + np.sin(2 * np.pi * wd) ** 2)
    return term1 + term2 + term3


# -- syn_mostly_infeasible ------------------------------------------------------------------------

INFEASIBLE_DIM = 8
INFEASIBLE_C1 = np.full(INFEASIBLE_DIM, 0.35)
INFEASIBLE_C2 = np.full(INFEASIBLE_DIM, 0.65)
INFEASIBLE_C3 = np.full(INFEASIBLE_DIM, 0.9)          # objective centre, outside the lens


def infeasible_distances(u: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Euclidean distance to each centre, scaled by 1/sqrt(dim) so the value stays O(1) regardless of dimension."""
    scale = INFEASIBLE_DIM**0.5
    r1 = np.linalg.norm(u - INFEASIBLE_C1, axis=-1) / scale
    r2 = np.linalg.norm(u - INFEASIBLE_C2, axis=-1) / scale
    return r1, r2


def infeasible_objective(u: np.ndarray) -> np.ndarray:
    return np.sum((u - INFEASIBLE_C3) ** 2, axis=-1)


# -- syn_failure_region ---------------------------------------------------------------------------


def failure_region_f(u: np.ndarray) -> np.ndarray:
    """Bowl over (u0, u1) only, minimized at the all-ones corner -- outside the failure boundary u0 + u1 <= 1.4, so
    it pulls the constrained optimum up against that boundary from the feasible side."""
    return (u[..., 0] - 1) ** 2 + (u[..., 1] - 1) ** 2


def failure_region_q(u: np.ndarray) -> np.ndarray:
    return u[..., 2] + u[..., 3]


def failure_region_p(u: np.ndarray) -> np.ndarray:
    return u[..., 4] + u[..., 5]


def failure_region_missing(u: np.ndarray) -> np.ndarray:
    return (u[..., 0] + u[..., 1]) > 1.4


# -- syn_amplifier_like ---------------------------------------------------------------------------


def amplifier_stage(W1, W2, L1, L2, M1, M2, IB, CC):
    """The two-stage amplifier's metrics from its raw grid values (W, L in um, M dimensionless, IB in uA, CC in pF).
    Returns (GAIN_dB, GBW_Hz, PM_deg, POWER_mW, AREA_um2)."""
    i1 = 4 * IB * 1e-6
    i2 = 20 * IB * 1e-6
    k = 2e-4
    lam = 0.08
    gm1 = np.sqrt(2 * k * (W1 * M1 / L1) * i1)
    gm2 = np.sqrt(2 * k * (W2 * M2 / L2) * i2)
    ro1 = L1 / (lam * i1)
    ro2 = L2 / (lam * i2)
    cl = 10e-12
    cc = CC * 1e-12
    cp = 2e-15 * (W2 * L2 * M2)
    gain = 20 * np.log10(gm1 * ro1 * gm2 * ro2 / 4)
    gbw = gm1 / (2 * np.pi * cc)
    p2 = gm2 * cc / (2 * np.pi * (cp * cl + cc * (cp + cl)))
    z = gm2 / (2 * np.pi * cc)
    pm = 90 - np.degrees(np.arctan(gbw / p2)) - np.degrees(np.arctan(gbw / z))
    power = 1.8 * (2 * i1 + i2) * 1e3
    area = 2 * W1 * L1 * M1 + W2 * L2 * M2
    return gain, gbw, pm, power, area
