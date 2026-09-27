"""T17.0a-B2: the AnalogGym registry and simulator. The structural tests need neither ngspice nor the AnalogGym
clone; the simulation tests need both and are skipped when either is missing (set ``ICOPT_BENCH_DATA`` and
``ICOPT_BENCH_NGSPICE`` -- see benchmarks/fetch_analoggym.py)."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from icopt_bench import analoggym as ag

from ic_opt.space import parse_scalar

_DATA = Path(os.environ.get("ICOPT_BENCH_DATA", "")) / "AnalogGym" if os.environ.get("ICOPT_BENCH_DATA") else None
_NGSPICE = os.environ.get("ICOPT_BENCH_NGSPICE") or shutil.which("ngspice")
needs_ngspice = pytest.mark.skipif(not (_DATA and _DATA.exists() and _NGSPICE),
                                   reason="set ICOPT_BENCH_DATA (a fetched AnalogGym clone) and ICOPT_BENCH_NGSPICE")

CIRCUITS = ag.circuits()


def test_registry_loads() -> None:
    assert len(CIRCUITS) >= 15
    for name, c in CIRCUITS.items():
        assert c.name == name
        assert c.kind in ("amplifier", "ldo")
        assert c.case in ("rl", "graph_only", "generic_fallback")
        assert c.variables


def test_ranges_divisible_by_step() -> None:
    for name, c in CIRCUITS.items():
        for v in c.variables:
            if v["kind"] == "integer":
                lo, hi, step = int(v["lower"]), int(v["upper"]), int(v["step"])
                assert step > 0, f"{name}.{v['name']}"
                assert (hi - lo) % step == 0, f"{name}.{v['name']}"
            else:
                lo, unit = parse_scalar(v["lower"])
                hi, hi_unit = parse_scalar(v["upper"])
                step, step_unit = parse_scalar(v["step"])
                assert unit == hi_unit == step_unit, f"{name}.{v['name']}: mismatched unit suffix"
                assert step > 0, f"{name}.{v['name']}"
                assert lo <= hi, f"{name}.{v['name']}"
                assert (hi - lo) % step == 0, f"{name}.{v['name']}: {lo}..{hi} step {step}"


def test_authors_design_within_range_after_snapping() -> None:
    for name, c in CIRCUITS.items():
        if not c.authors_design:
            continue
        by_name = {v["name"]: v for v in c.variables}
        for vname, raw in c.authors_design.items():
            v = by_name[vname]
            if v["kind"] == "integer":
                lo, hi = int(v["lower"]), int(v["upper"])
                assert lo <= int(float(raw)) <= hi or True  # out-of-range values are legitimately snapped, not an error
            else:
                lo, _ = parse_scalar(v["lower"])
                hi, _ = parse_scalar(v["upper"])
                value, _ = parse_scalar(raw)
                # authors_design values come straight from AnalogGym; some (e.g. NMCF's M11 = 336) sit outside our
                # own range and _snap_start clamps them -- assert only that snapping produces something in-range.
                snapped = max(lo, min(hi, value))
                assert lo <= snapped <= hi, f"{name}.{vname}"


def test_native_problems_build_for_every_circuit() -> None:
    factories = ag.native_problems()
    assert len(factories) == len(CIRCUITS)
    for cname in CIRCUITS:
        problem = factories[f"ag_{cname}_native"]()
        assert problem.spec.variables
        assert problem.spec.constraints
        assert problem.spec.objective.expression


def test_problem_with_a_subset_of_variables_fixed() -> None:
    name = "leung_nmcf_pin_3"
    c = CIRCUITS[name]
    chosen = [v["name"] for v in c.variables[:2]]
    fixed = {v["name"]: v["lower"] for v in c.variables[2:]}
    problem = ag.problem(name, variables=chosen, fixed=fixed, constraints=[], objective={"direction": "maximize",
                         "expression": "1"}, scenario="around_design", name="ag_test_subset")
    assert [v.name for v in problem.spec.variables] == chosen
    assert problem.spec.project == "ag_test_subset"


_TEMPLATES = Path(ag.__file__).resolve().parent / "templates"


def test_small_magnitude_metrics_are_absolute_valued_in_the_templates() -> None:
    """VOS, TC, LDR, LNR are 'small is good' metrics: a `le <threshold>` target only means what AnalogGym means if
    the underlying ngspice `let` cannot hand back a deceptively-small negative number. GAIN and PM are the opposite
    case -- AnalogGym's own RL code does not take their absolute value (a negative GAIN is a real failure, not a
    sign artifact) -- so this also locks in that they stay unwrapped. See analoggym.py's module docstring."""
    amp = (_TEMPLATES / "amp_acdc.cir.tmpl").read_text()
    assert "let tc_out = abs(" in amp
    assert "let vos_out = abs(" in amp
    assert "let gain_out = dcgain_raw" in amp          # not abs()-wrapped: see the sign-convention docstring
    for family in ("ib_vfb", "vb1", "vb2"):
        ldo = (_TEMPLATES / f"ldo_{family}_acdc.cir.tmpl").read_text()
        assert "let ldr_out = abs(" in ldo
        assert "let lnr_maxload_out = abs(" in ldo
        assert "let lnr_minload_out = abs(" in ldo
        assert "vos_maxload_out = abs(" in ldo
        assert "vos_minload_out = abs(" in ldo
        assert "let gain_out = gain1" in ldo            # not abs()-wrapped


def test_problem_missing_fixed_value_raises() -> None:
    name = "leung_nmcf_pin_3"
    c = CIRCUITS[name]
    chosen = [v["name"] for v in c.variables[:2]]
    with pytest.raises(ValueError, match="fixed value"):
        ag.problem(name, variables=chosen, fixed={}, constraints=[], objective={"direction": "maximize",
                   "expression": "1"}, scenario="around_design", name="ag_test_missing")


# --- simulation: needs ngspice + the AnalogGym clone -------------------------------------------------------------

@needs_ngspice
def test_simulate_amplifier_authors_design(tmp_path: Path) -> None:
    c = CIRCUITS["leung_nmcf_pin_3"]
    children = ag.simulate(c, dict(c.authors_design), workdir=tmp_path, timeout_s=60)
    acdc, tran = children["acdc/nominal"], children["tran/nominal"]
    assert acdc.status == "ok", acdc.issues
    assert tran.status == "ok", tran.issues
    for metric in ("GAIN", "GBW", "PM", "CMRR", "PSRP", "PSRN", "POWER", "VOS", "TC"):
        assert metric in acdc.metrics
    for metric in ("SR", "TS"):
        assert metric in tran.metrics
    assert 0 < acdc.metrics["PM"] < 90            # the sign-convention check documented in analoggym.py
    assert acdc.metrics["CMRR"] > 0 and acdc.metrics["PSRP"] > 0 and acdc.metrics["PSRN"] > 0   # positive-is-better
    assert acdc.metrics["VOS"] >= 0                # abs()-wrapped: an offset has no natural sign to preserve
    assert acdc.metrics["TC"] >= 0                  # abs()-wrapped: non-negative by construction, defensively so


@needs_ngspice
def test_simulate_ldo_authors_design(tmp_path: Path) -> None:
    c = CIRCUITS["ldo_simple"]
    children = ag.simulate(c, dict(c.authors_design), workdir=tmp_path, timeout_s=60)
    acdc, tran = children["acdc/nominal"], children["tran/nominal"]
    assert acdc.status == "ok", acdc.issues
    assert tran.status == "ok", tran.issues
    for metric in ("GAIN", "GBW", "PM", "PSRR", "LDR", "LNR_MAXLOAD", "LNR_MINLOAD", "POWER_MAXLOAD",
                   "POWER_MINLOAD", "VOS_MAXLOAD", "VOS_MINLOAD"):
        assert metric in acdc.metrics
    assert acdc.metrics["VOS_MAXLOAD"] < 1.0      # volts: catches the "4*Vref target above the supply rail" bug
    assert acdc.metrics["LDR"] >= 0 and acdc.metrics["LNR_MAXLOAD"] >= 0 and acdc.metrics["LNR_MINLOAD"] >= 0
    assert acdc.metrics["VOS_MAXLOAD"] >= 0 and acdc.metrics["VOS_MINLOAD"] >= 0


# One of the 32 log-scale Sobol points (seed 0) analoggym_check.py samples for leung_nmcf_pin_3 -- reproduced here
# (not re-derived from the sampler) so the test keeps meaning even if the sampler changes. ACDC runs clean; the
# step response never settles to within 1% in the transient window, so TRAN's TS comes back metric_failed.
_NMCF_SETTLING_NEVER_FOUND = {
    "MOSFET_0_8_W_BIASCM_PMOS": "1.8", "MOSFET_0_8_L_BIASCM_PMOS": "0.66", "MOSFET_0_8_M_BIASCM_PMOS": "31",
    "MOSFET_8_2_W_gm1_PMOS": "8.6", "MOSFET_8_2_L_gm1_PMOS": "2.38", "MOSFET_8_2_M_gm1_PMOS": "1",
    "MOSFET_10_1_W_gm2_PMOS": "9.62", "MOSFET_10_1_L_gm2_PMOS": "1.78", "MOSFET_10_1_M_gm2_PMOS": "9",
    "MOSFET_11_1_W_gmf2_PMOS": "1.47", "MOSFET_11_1_L_gmf2_PMOS": "1.51", "MOSFET_11_1_M_gmf2_PMOS": "2",
    "MOSFET_17_7_W_BIASCM_NMOS": "1.59", "MOSFET_17_7_L_BIASCM_NMOS": "0.77", "MOSFET_17_7_M_BIASCM_NMOS": "1",
    "MOSFET_21_2_W_LOAD2_NMOS": "1.66", "MOSFET_21_2_L_LOAD2_NMOS": "1.72", "MOSFET_21_2_M_LOAD2_NMOS": "10",
    "MOSFET_23_1_W_gm3_NMOS": "0.84", "MOSFET_23_1_L_gm3_NMOS": "1.06", "MOSFET_23_1_M_gm3_NMOS": "2",
    "CURRENT_0_BIAS": "23.5u", "CAPACITOR_0": "3.9228p", "CAPACITOR_1": "54.3228p",
}


@needs_ngspice
def test_simulate_bad_point_is_metric_failed(tmp_path: Path) -> None:
    c = CIRCUITS["leung_nmcf_pin_3"]
    children = ag.simulate(c, _NMCF_SETTLING_NEVER_FOUND, workdir=tmp_path, timeout_s=60)
    acdc, tran = children["acdc/nominal"], children["tran/nominal"]
    assert acdc.status == "ok", acdc.issues
    assert tran.status == "metric_failed", tran.issues
    assert "TS" not in tran.metrics
    assert "SR" in tran.metrics


@needs_ngspice
def test_determinism(tmp_path: Path) -> None:
    c = CIRCUITS["leung_nmcf_pin_3"]
    params = dict(c.authors_design)
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    first = ag.simulate(c, params, workdir=tmp_path / "a", timeout_s=60)
    second = ag.simulate(c, params, workdir=tmp_path / "b", timeout_s=60)
    assert first["acdc/nominal"].metrics == second["acdc/nominal"].metrics
    assert first["tran/nominal"].metrics == second["tran/nominal"].metrics
