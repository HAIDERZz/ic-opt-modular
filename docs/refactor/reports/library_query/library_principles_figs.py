"""Figures for LIBRARY_PRINCIPLES_CN.html: toy illustrations of the ideas the query library rests on (no library data).

fig 1  a 1-D Gaussian process: measured points, mean, 2-sigma band; then the same with one point "pretended measured"
       at the widest place -- the band collapses there without any new value (why lib.densify can plan without EMX)
fig 2  calibration: a band that covers too few held-out points, and the same band widened by k_scale
fig 3  the resonance rise 1 / (1 - (f/SRF)^2): why an anchored column is steep where the resonance comes close
"""
from pathlib import Path

import matplotlib
import numpy as np
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import ConstantKernel, Matern, WhiteKernel

matplotlib.use("Agg")
import matplotlib.pyplot as plt

plt.rcParams["font.family"] = ["Noto Sans CJK JP", "DejaVu Sans"]
FIGS = Path(__file__).resolve().parent / "figs"
STEM = "LIBRARY_PRINCIPLES_CN"


def truth(x):
    return 1.0 + 0.35 * np.sin(2.2 * x) + 0.08 * x


def fig1():
    rng = np.random.default_rng(3)
    xs = np.array([0.15, 0.4, 0.55, 1.6, 1.85, 2.1, 2.7, 2.95])
    ys = truth(xs) + rng.normal(0, 0.01, len(xs))
    grid = np.linspace(0, 3.1, 300)
    kern = ConstantKernel(1.0, (1e-3, 1e3)) * Matern(nu=2.5, length_scale=0.5, length_scale_bounds=(1e-2, 1e2)) + WhiteKernel(1e-4, (1e-8, 1e-1))
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.2), sharey=True)
    for ax, extra in zip(axes, [None, 1.1]):
        x = xs if extra is None else np.append(xs, extra)
        y = ys if extra is None else np.append(ys, truth(extra))
        gp = GaussianProcessRegressor(kern, normalize_y=True, n_restarts_optimizer=2, random_state=0).fit(x[:, None], y)
        mu, sd = gp.predict(grid[:, None], return_std=True)
        ax.plot(grid, truth(grid), color="#9a9c90", lw=1, ls="--", label="真实曲线（模型不知道）")
        ax.fill_between(grid, mu - 2 * sd, mu + 2 * sd, color="#2f7d5f", alpha=0.18, label="预测 ± 2σ")
        ax.plot(grid, mu, color="#2f7d5f", lw=2, label="预测均值")
        ax.scatter(xs, ys, color="#1f2320", zorder=5, s=28, label="已仿真的行")
        if extra is not None:
            ax.scatter([extra], [truth(extra)], color="#b8611f", zorder=6, s=70, marker="*", label="假定已测的新点")
            ax.set_title("加一个“假定已测”的点后：那里的带子立刻收窄")
        else:
            ax.set_title("高斯过程：点之间的带子越宽，模型越没把握")
        ax.set_xlabel("几何参数（示意，一维）")
        ax.grid(alpha=0.25)
    axes[0].set_ylabel("结果列（示意）")
    axes[0].legend(loc="upper left", fontsize=8)
    axes[1].legend(loc="upper left", fontsize=8)
    fig.tight_layout()
    fig.savefig(FIGS / f"{STEM}_gp.png", dpi=130)
    plt.close(fig)


def fig2():
    rng = np.random.default_rng(7)
    n = 60
    z = rng.normal(0, 1.6, n)                   # the model's sigma is too small by 1.6x: residuals in sigma units
    fig, axes = plt.subplots(1, 2, figsize=(12, 3.8))
    for ax, k in zip(axes, [1.0, 1.6]):
        inside = np.abs(z) <= 2 * k
        ax.axhspan(-2 * k, 2 * k, color="#2f7d5f", alpha=0.15, label=f"±2σ × k_scale（k_scale = {k}）")
        ax.scatter(np.arange(n), z, c=np.where(inside, "#2f7d5f", "#b8611f"), s=20)
        ax.axhline(0, color="#1f2320", lw=0.8)
        ax.set_title(f"留出行的实际误差 / 模型 σ：区间盖住 {inside.mean():.0%}")
        ax.set_xlabel("留出的行")
        ax.set_ylabel("误差（σ 为单位）")
        ax.legend(loc="upper right", fontsize=8)
        ax.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(FIGS / f"{STEM}_calibration.png", dpi=130)
    plt.close(fig)


def fig3():
    f = np.linspace(1, 100, 500)
    fig, ax = plt.subplots(figsize=(7.5, 4))
    for srf, c in [(90.0, "#2f7d5f"), (70.0, "#5b6fc9"), (55.0, "#b8611f")]:
        rise = 1 / (1 - (f / srf) ** 2)
        rise[f >= srf] = np.nan
        ax.plot(f, rise, color=c, lw=2, label=f"SRF = {srf:.0f} GHz")
        ax.scatter([40], [1 / (1 - (40 / srf) ** 2)], color=c, zorder=5)
    ax.axvline(40, color="#1f2320", lw=0.8, ls="--")
    ax.text(41, 3.6, "锚定频率 40 GHz", fontsize=9)
    ax.set_ylim(0.8, 4.2)
    ax.set_xlim(1, 100)
    ax.set_xlabel("频率 (GHz)")
    ax.set_ylabel("L(f) / L_lf（理想并联谐振的抬升因子）")
    ax.set_title("同一低频电感，SRF 不同：40 GHz 处的电感差很多")
    ax.legend(fontsize=9)
    ax.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(FIGS / f"{STEM}_resonance.png", dpi=130)
    plt.close(fig)


if __name__ == "__main__":
    FIGS.mkdir(exist_ok=True)
    fig1()
    fig2()
    fig3()
    print("figures written to", FIGS)
