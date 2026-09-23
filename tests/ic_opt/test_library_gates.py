"""T13 gates on the real library (set IC_OPT_LIBRARY to a library root with library.yaml): the ported kernels
reproduce the T13.0 verification (docs/refactor/reports/library_query/ind_query_verify.json) on the same rows,
seeds and splits. G1: forward accuracy and 2-sigma coverage per quantity, SRF by GP, the domain-guard table."""
from __future__ import annotations

import itertools
import json
import os
from pathlib import Path

import numpy as np
import pytest

from ic_opt.library import dataset, domain, gp

LIB = Path(os.environ.get("IC_OPT_LIBRARY", "/nonexistent"))
VERIFY = Path(__file__).resolve().parents[2] / "docs" / "refactor" / "reports" / "library_query" / "ind_query_verify.json"
STRATA = {"ind_sym_ap": "AP", "ind_sym_m10": "M10"}
NAMES = {"Lp_lf": "L_lf", "Lp_res": "L_res", "Qp_peak": "Q_peak150", "Lp@28": "L@28", "Qp@28": "Q@28", "Lp@60": "L@60", "Qp@60": "Q@60"}
pytestmark = pytest.mark.skipif(not (LIB / "library.yaml").is_file() or not VERIFY.is_file(), reason="set IC_OPT_LIBRARY to the N28 library")


def ranges(ds: dataset.Dataset) -> dict:
    x = ds.matrix()
    return {d: (float(x[:, i].min()), float(x[:, i].max())) for i, d in enumerate(ds.dims)}


@pytest.mark.parametrize("stratum", sorted(STRATA))
def test_g1_forward_accuracy_and_coverage_reproduce_the_verification(stratum):
    ref = json.loads(VERIFY.read_text())
    ds = dataset.build(LIB, stratum)
    body = STRATA[stratum]
    for q, old in NAMES.items():
        rows = ds.usable(q)
        r = gp.holdout(ds.matrix(rows), ds.values(q, rows), dims=ds.dims, ranges=ranges(ds), log_target=True,
                       nt_mode="per_nt", kernel="matern52", nt_dim=ds.nt_dim)
        v = ref[f"forward_{body}"][f"{old}|per_nt-matern52"]
        print(f"{stratum} {q:<8} median {r['median_rel']:.5f} (ref {v['median_rel']:.5f})  cov {r['coverage_2sigma']:.3f} (ref {v['coverage_2sigma']:.3f})")
        assert r["median_rel"] == pytest.approx(v["median_rel"], rel=0.10), q
        assert abs(r["coverage_2sigma"] - v["coverage_2sigma"]) <= 0.02, q
    rows = ds.usable("SRF_p")
    r = gp.holdout(ds.matrix(rows), ds.values("SRF_p", rows) / 1e9, dims=ds.dims, ranges=ranges(ds), log_target=True,
                   nt_mode="per_nt", kernel="matern52", nt_dim=ds.nt_dim)
    s = ref[f"srf_{body}"]
    print(f"{stratum} SRF_p    median {r['median_rel']:.5f} (ref {s['gp_median_rel']:.5f})  cov {r['coverage_2sigma']:.3f} (ref {s['gp_coverage_2sigma']:.3f})")
    assert r["median_rel"] == pytest.approx(s["gp_median_rel"], rel=0.10)
    assert abs(r["coverage_2sigma"] - s["gp_coverage_2sigma"]) <= 0.02


@pytest.mark.parametrize("stratum", sorted(STRATA))
def test_g1_guard_accepts_the_library_and_rejects_what_it_must(stratum):
    ds = dataset.build(LIB, stratum)
    guard = domain.DomainGuard(ds.matrix(), ds.dims, ranges(ds), nt_dim=ds.nt_dim)
    od, w, s, nt = ds.dims
    for row in ds.rows:
        assert guard.check(row.coords).ok
    have = {tuple(r.coords[d] for d in ds.dims) for r in ds.rows}
    ods, ws = sorted({r.coords[od] for r in ds.rows}), sorted({r.coords[w] for r in ds.rows})
    mids = 0
    for (a, b), (c, e), sp, turns in itertools.product(itertools.pairwise(ods), itertools.pairwise(ws), (2.0, 3.0, 4.0), (1, 2, 3, 4, 5)):
        if turns == 1 and sp != 2.0:
            continue
        if all((o, x, sp, turns) in have for o in (a, b) for x in (c, e)):
            assert guard.check({od: (a + b) / 2, w: (c + e) / 2, s: sp, nt: turns}).ok
            mids += 1
    assert mids > 300
    for params, criterion in (({od: 50, w: 6, s: 2, nt: 2}, 1), ({od: 150, w: 10.5, s: 2, nt: 2}, 1), ({od: 150, w: 6, s: 2, nt: 2.5}, 2)):
        with pytest.raises(domain.OutOfDomainError) as exc:
            guard.check(params)
        assert exc.value.criterion == criterion
    for turns, o, x in itertools.product((2, 3, 4, 5), (80.0, 100.0, 120.0, 150.0), (6.0, 8.0, 10.0)):
        if o - 2 * turns * x - 2 * (turns - 1) * 3.0 < 25:                 # unbuildable: inner diameter below 25 um
            with pytest.raises(domain.OutOfDomainError):
                guard.check({od: o, w: x, s: 3.0, nt: turns})
    assert np.isfinite(guard.nearest(ds.rows[0].coords)[0][1])
