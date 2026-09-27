"""AnalogGym circuits (T17.0a-B2): ngspice + SKY130 benchmark problems built from ``analoggym_circuits.yaml``.

AnalogGym (BSD-3-Clause, https://github.com/CODA-Team/AnalogGym) is not vendored here; ``fetch_analoggym.py`` fetches
it to ``$ICOPT_BENCH_DATA`` (or ``benchmarks/.data`` next to that script).

Sign conventions, one per metric (every ``le``/``ge`` target direction is meant to read correctly on the signed
value returned, not on some hidden magnitude):

- CMRR, PSRP, PSRN, PSRR: AnalogGym reports these as negative dB (more negative is better rejection); we negate
  them (``-1*...``) so larger is better, matching every other metric here. A badly-behaving point can still come
  out negative after negation (rejection failed outright), which correctly fails a ``ge`` target.
- GAIN: AnalogGym's own RL code does **not** take the absolute value (``self.dcgain = self.ac_results[4][1]`` in
  AMP_NMCF.py's ``_get_info``; a negative value scores -1 outright rather than being folded positive). We follow
  it: ``gain_out`` is the raw ``vdb(...)`` reading, unmodified. Taking ``abs()`` here would have been actively
  wrong -- it would turn a broken, negative-gain point into what looks like a large, healthy positive gain.
- PM: the raw ``vp(...) when vdb(...)=0`` reading, unmodified (see the comment in templates/amp_acdc.cir.tmpl for
  why no 180-degree conversion is needed). A badly-behaving point can read outside 0..90 degrees (we have seen
  -167 and +149 on wide-range Sobol samples) -- that is a real instability/multiple-crossing artifact, not a sign
  bug, and must not be forced into range.
- VOS, VOS_MAXLOAD, VOS_MINLOAD: ``abs()``, matching AnalogGym's own ``self.vos = abs(self.vos_1)`` in
  AMP_NMCF.py's ``_get_info`` (offset has no natural sign to preserve; a "le" target on a signed offset would let
  a large negative offset through).
- TC, LDR, LNR_MAXLOAD, LNR_MINLOAD: each is a peak-to-peak deviation divided by a positive average and a positive
  span, so it is non-negative by construction for any point where the circuit holds a sane operating point at
  all; we wrap it in ``abs()`` regardless, defensively, since "le <threshold>" only means what AnalogGym means
  (small in magnitude) if the value can never present as a deceptively-small negative number.
- POWER, POWER_MAXLOAD, POWER_MINLOAD: ``-1 * I(vdd) * V(vdd)`` in mW; positive by construction for a circuit
  actually drawing current from the supply as wired (a negative reading would mean current flowing the wrong way
  through that source, itself a sign of a badly broken point -- we do not mask that with ``abs()``).
- UNDERSHOOT, OVERSHOOT: signed, deliberately, matching AnalogGym's own unmodified
  ``let v_undershoot = 4*0.4 - v_min`` / ``let v_overshoot = v_max - 4*0.4`` (ldo_1_tran.cir). A negative value
  means the output never dipped below (or rose above) the reference during that window at all, which correctly
  satisfies a "le <threshold>" target without needing ``abs()``.
- SR, TS: computed in Python from the transient waveform, porting AnalogGym's own
  ``RGNN_RL/utils.py:analyze_amplifier_performance`` (BSD-3-Clause) -- see ``_step_response``. Both are
  magnitudes (``abs()`` of the rise/fall slew rate and settling time) by construction there.
"""

from __future__ import annotations

import functools
import json
import math
import os
import re
import shutil
import signal
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import yaml

from ic_opt.observation import ChildResult
from ic_opt.space import parse_scalar
from icopt_bench.problem import Problem, child, make_spec

_HERE = Path(__file__).resolve().parent
_REGISTRY = _HERE / "analoggym_circuits.yaml"
_TEMPLATES = _HERE / "templates"
_CALIBRATED = _HERE.parent / "analoggym_problems.json"    # written by tools/survey_analoggym.py (plan section 4.3)


def _data_dir() -> Path:
    """The AnalogGym clone's root (its ``.git``, ``PDK/``, ``RGNN_RL/`` live directly under it)."""
    env = os.environ.get("ICOPT_BENCH_DATA")
    base = Path(env) if env else _HERE.parent / ".data"
    return base / "AnalogGym"


def _ngspice() -> str:
    return os.environ.get("ICOPT_BENCH_NGSPICE") or shutil.which("ngspice") or "ngspice"


@dataclass(frozen=True)
class Circuit:
    name: str
    kind: str                          # "amplifier" | "ldo"
    case: str                          # "rl" | "graph_only" | "generic_fallback"
    subckt: str
    netlist: str                       # path under the AnalogGym clone: the .subckt this circuit simulates
    vars_template: str                 # path under the clone: AnalogGym's own placeholder-valued .param file
    family: str | None                 # LDO wiring family ("ib_vfb" | "vb1" | "vb2"); None for amplifiers
    supply: str | None                 # LDO supply voltage (amplifiers hard-code 1.8V in their template)
    vref: str | None                   # LDO reference voltage
    source: tuple[str, ...]
    load: dict
    variables: tuple[dict, ...]
    targets: tuple[dict, ...]
    authors_design: dict[str, str] | None
    notes: tuple[str, ...]


@functools.cache                     # the registry is read once per process: every problem is built from it
def circuits() -> dict[str, Circuit]:
    doc = yaml.safe_load(_REGISTRY.read_text())
    out: dict[str, Circuit] = {}
    for c in doc["circuits"]:
        authors = {a["name"]: a["value"] for a in c["authors_design"]} if c.get("authors_design") else None
        out[c["name"]] = Circuit(
            name=c["name"], kind=c["kind"], case=c["case"], subckt=c["subckt"], netlist=c["netlist"],
            vars_template=c["vars_template"], family=c.get("family"), supply=c.get("supply"), vref=c.get("vref"),
            source=tuple(c["source"]), load=c["load"], variables=tuple(c["variables"]), targets=tuple(c["targets"]),
            authors_design=authors, notes=tuple(c.get("notes") or ()),
        )
    return out


# --- ngspice ---------------------------------------------------------------------------------------------------

def _single_threaded_env() -> dict[str, str]:
    """ngspice can be built with OpenMP (device-evaluation loops); left alone it reads ``OMP_NUM_THREADS`` from the
    environment, which is unset here, so OpenMP's own default -- the machine's core count -- applies. At up to 8
    ngspice processes at once that is 8x the machine's core count in threads, not 8, so every one of them gets a
    forced ``OMP_NUM_THREADS=1`` (OPENBLAS/MKL/NUMEXPR too, in case a build links one of those instead): one ngspice,
    one thread, so ``simulate``'s own 8-at-once budget is the real thread budget. ``templates/*.tmpl`` additionally
    set ``num_threads=1`` inside ``.control`` for the same reason, belt and suspenders."""
    env = dict(os.environ)
    for var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        env[var] = "1"
    return env


def _run_ngspice(cir: Path, log: Path, timeout_s: float) -> bool:
    """Runs ``ngspice -b -o <log> <cir>`` in its own session, forced single-threaded; on timeout, kills the whole
    process group. Returns whether the run finished with exit code 0 (a nonzero exit, a timeout or a crash are all
    a failed run: only a clean exit means the log and any wrdata files are trustworthy)."""
    argv = [_ngspice(), "-b", "-o", str(log), str(cir)]
    process = subprocess.Popen(argv, cwd=cir.parent, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                start_new_session=True, env=_single_threaded_env())
    try:
        return process.wait(timeout=timeout_s) == 0
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            pass
        process.wait()
        return False


_FAILED_RE = re.compile(r"^\s*(\w+)\s*=\s*failed", re.IGNORECASE | re.MULTILINE)


def _failed_names(log_text: str) -> set[str]:
    return {m.group(1).lower() for m in _FAILED_RE.finditer(log_text)}


def _read_row(path: Path, n: int) -> list[float] | None:
    """The first data row of an ngspice ``wrdata`` file, keeping only the value column of each (x, value) pair --
    ``wrdata`` at a scalar `.let` writes it broadcast over the sweep, so every row carries the same value."""
    if not path.exists():
        return None
    lines = [ln for ln in path.read_text(errors="ignore").splitlines() if ln.strip()]
    if not lines:
        return None
    try:
        values = [float(tok) for tok in lines[0].split()]
    except ValueError:
        return None
    if len(values) < 2 * n:
        return None
    return values[1::2][:n]


def _extract(workdir: Path, log_text: str, files_and_names: Sequence[tuple[str, Sequence[tuple[str, str]]]],
             ) -> tuple[dict[str, float], list[str], bool]:
    """``files_and_names``: ``[(wrdata_filename, [(raw_name, metric_name), ...]), ...]``. Returns (metrics, missing,
    any_file_present) -- ``any_file_present`` false means the unit produced no measurement file at all."""
    failed = _failed_names(log_text)
    metrics: dict[str, float] = {}
    missing: list[str] = []
    any_present = False
    for filename, names in files_and_names:
        raw_names = [r for r, _ in names]
        values = _read_row(workdir / filename, len(raw_names))
        any_present = any_present or (workdir / filename).exists()
        for i, (raw_name, metric_name) in enumerate(names):
            if raw_name.lower() in failed or values is None:
                missing.append(metric_name)
                continue
            v = values[i]
            if not math.isfinite(v):
                missing.append(metric_name)
                continue
            metrics[metric_name] = v
    return metrics, missing, any_present


# --- amplifier metric layout -------------------------------------------------------------------------------------

_ACDC_DC = [("tc_out", "TC"), ("power_out", "POWER"), ("vos_out", "VOS")]
_ACDC_AC = [("cmrr_out", "CMRR"), ("psrp_out", "PSRP"), ("psrn_out", "PSRN"), ("gain_out", "GAIN")]
_ACDC_GBWPM = [("gbw_out", "GBW"), ("pm_out", "PM")]

_LDO_DC = [("ldr_out", "LDR"), ("power_maxload_out", "POWER_MAXLOAD"), ("power_minload_out", "POWER_MINLOAD"),
           ("vos_maxload_out", "VOS_MAXLOAD"), ("vos_minload_out", "VOS_MINLOAD")]
_LDO_LNR_MAX = [("lnr_maxload_out", "LNR_MAXLOAD")]
_LDO_LNR_MIN = [("lnr_minload_out", "LNR_MINLOAD")]
_LDO_AC = [("gain_out", "GAIN"), ("gbw_out", "GBW"), ("pm_out", "PM"), ("psrr_out", "PSRR")]
_LDO_TRAN = [("undershoot_out", "UNDERSHOOT"), ("overshoot_out", "OVERSHOOT")]


def _step_response(time: np.ndarray, vin: np.ndarray, vout: np.ndarray, d0: float = 0.01,
                    ) -> tuple[float, float, float, float]:
    """Port of AnalogGym's ``RGNN_RL/utils.py:analyze_amplifier_performance`` (BSD-3-Clause): from a step-response
    waveform, the rising and falling slew rate (measured at the half-amplitude point) and the settling time (first
    moment the output stays within ``d0`` of its final value) on each edge. Returns
    ``(sr_p, settling_p, sr_n, settling_n)``, each ``nan`` where the edge or the settling band was never found."""
    dv = np.diff(vin)
    rises, falls = np.where(dv > 0)[0], np.where(dv < 0)[0]
    if len(rises) == 0 or len(falls) == 0:
        return math.nan, math.nan, math.nan, math.nan
    t0, t1 = time[rises[0]], time[falls[0]]
    v0 = float(np.median(vin[time < t0])) if np.any(time < t0) else float(vin[0])
    v1 = float(np.median(vin[(time > t0) & (time < t1)]))

    def settle_index(delta: np.ndarray) -> int | None:
        for i in range(len(delta)):
            if np.all(np.abs(delta[i:]) < d0):
                return i
        return None

    def edge(start_t: float, end_t: float, mode: str) -> tuple[float, float]:
        mask = (time >= start_t) & (time <= end_t)
        t_seg, v_seg = time[mask], vout[mask]
        target = v0 + (v1 - v0) / 2
        hit = np.where(v_seg >= target)[0] if v1 >= v0 else np.where(v_seg <= target)[0]
        with np.errstate(divide="ignore", invalid="ignore"):    # a 1-2 point segment (a degenerate edge) is fine: nan
            sr = float(np.gradient(v_seg, t_seg)[hit[0]]) if len(hit) else math.nan
        delta = (v_seg - v1) / v1 if mode == "positive" else (v_seg - v0) / v0
        idx = settle_index(delta)
        settling = float(t_seg[idx] - start_t) if idx is not None else math.nan
        return sr, settling

    sr_p, settle_p = edge(t0, t1, "positive")
    sr_n, settle_n = edge(t1, float(time[-1]), "negative")
    return sr_p, settle_p, sr_n, settle_n


def _read_wave(path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
    """``wrdata <file> v(vout) v(vin)`` -> (time, vout, vin) or None if the file is missing or malformed."""
    if not path.exists():
        return None
    rows = []
    for line in path.read_text(errors="ignore").splitlines():
        if not line.strip():
            continue
        try:
            rows.append([float(tok) for tok in line.split()])
        except ValueError:
            return None
    if not rows or len(rows[0]) < 4:
        return None
    arr = np.array(rows)
    return arr[:, 0], arr[:, 1], arr[:, 3]


# --- templating --------------------------------------------------------------------------------------------------

def _fmt_amp(x: float) -> str:
    return f"{x:.9g}"


def _parse_amp(raw: str) -> float:
    value, unit = parse_scalar(raw)
    scale = {"": 1, "f": 1e-15, "p": 1e-12, "n": 1e-9, "u": 1e-6, "m": 1e-3, "k": 1e3, "meg": 1e6}[unit.lower()]
    return float(value) * scale


def _fill(template_name: str, **kw: str) -> str:
    return (_TEMPLATES / template_name).read_text().format(**kw)


def _write_vars_file(circuit: Circuit, params: dict[str, str], workdir: Path) -> Path:
    """AnalogGym's own placeholder-valued .param file, with our chosen values substituted for the variables we
    control; any parameter this circuit does not expose as a variable (loads, ties such as ``W_M1=W_M0``) is left
    exactly as AnalogGym shipped it."""
    text = (_data_dir() / circuit.vars_template).read_text()
    for name, value in params.items():
        text = re.sub(rf"(?<![\w.]){re.escape(name)}=\S+", f"{name}={value}", text)
    path = workdir / "vars.spice"
    path.write_text(text)
    return path


def _amp_paths(circuit: Circuit, workdir: Path, params: dict[str, str]) -> tuple[Path, Path]:
    data = _data_dir()
    vars_path = _write_vars_file(circuit, params, workdir)
    acdc = workdir / "acdc.cir"
    acdc.write_text(_fill("amp_acdc.cir.tmpl", subckt=circuit.subckt, netlist=str(data / circuit.netlist),
                          pdk=str(data / "PDK" / "sky130_pdk"), vars=str(vars_path), out_dc="acdc_dc",
                          out_ac="acdc_ac", out_gbwpm="acdc_gbwpm"))
    tran = workdir / "tran.cir"
    tran.write_text(_fill("amp_tran.cir.tmpl", subckt=circuit.subckt, netlist=str(data / circuit.netlist),
                          pdk=str(data / "PDK" / "sky130_pdk"), vars=str(vars_path), out_wave="tran_wave"))
    return acdc, tran


def _ldo_template_kwargs(circuit: Circuit) -> dict[str, str]:
    """``FIND ... AT=`` needs its point strictly inside the swept interval (ngspice: "out of interval" at the exact
    edge), so the ``dc Iload3`` sweep itself runs 2% past ``iload_max`` while every ``AT=``/``to=`` stays at the
    true max: ``iload_max_val`` sits safely in the sweep's interior, not on its last, possibly-off-by-rounding
    sample.

    ``vref_target`` is the regulated output AnalogGym's own testbench compares against. Only ldo_1's own subckt
    has a verified external R0/R1 feedback divider (``r0 vout vfb 300e3`` / ``r1 vfb Vss 100e3`` inside
    design_variables/ldo_1.txt, ratio (300k+100k)/100k = 4), matching the ``4*Vref`` AnalogGym itself writes in
    ldo_1_acdc.cir. ldo_2's subckt has no such divider (its ``vfb`` pin is an internal tap with no resistors at
    all), and ldo_simple/ldo_folded_cascode feed Vreg straight back into the non-inverting input (`x1 vdd Vreg1
    vref_in net1 vss Vreg1 ldo_simple`) -- a unity-gain loop. AnalogGym's own ldo_2_acdc.cir/ldo_simple_acdc.cir/
    ldo_folded_cascode_acdc.cir nonetheless all write the same ``4*Vref`` (copied from ldo_1's testbench without
    adjusting for the different, or absent, feedback ratio): at ldo_simple's vref=1.8V/supply=2V that puts the
    "target" at 7.2V, above the supply rail, and the vos/undershoot/overshoot metrics computed against it come out
    several volts off, which is what first exposed this. We use the verified ratio (4) for ldo_1 only and 1 for
    the other three."""
    supply = float(circuit.supply)
    iload_max, iload_min = _parse_amp(circuit.load["current_max"]), _parse_amp(circuit.load["current_min"])
    sweep_stop = iload_max * 1.02
    vref_ratio = 4.0 if circuit.name == "ldo_1" else 1.0
    return {
        "supply": circuit.supply, "vref": circuit.vref,
        "iload_max_val": _fmt_amp(iload_max), "iload_min_val": _fmt_amp(iload_min),
        "iload_sweep_stop": _fmt_amp(sweep_stop), "iload_step_val": _fmt_amp((sweep_stop - iload_min) / 100),
        "iload_span": _fmt_amp(iload_max - iload_min),
        "lnr_lo": _fmt_amp(0.9 * supply), "lnr_hi": _fmt_amp(1.1 * supply), "lnr_span": _fmt_amp(0.2 * supply),
        "vref_target": _fmt_amp(vref_ratio * float(circuit.vref)),
    }


def _ldo_paths(circuit: Circuit, workdir: Path, params: dict[str, str]) -> tuple[Path, Path]:
    data = _data_dir()
    vars_path = _write_vars_file(circuit, params, workdir)
    kw = dict(subckt=circuit.subckt, netlist=str(data / circuit.netlist), pdk=str(data / "PDK" / "sky130_pdk"),
              vars=str(vars_path), **_ldo_template_kwargs(circuit))
    acdc = workdir / "acdc.cir"
    acdc.write_text(_fill(f"ldo_{circuit.family}_acdc.cir.tmpl", out_lnr_max="ldo_lnr_max", out_lnr_min="ldo_lnr_min",
                          out_dc="ldo_dc", out_ac="ldo_ac", **kw))
    tran = workdir / "tran.cir"
    tran.write_text(_fill(f"ldo_{circuit.family}_tran.cir.tmpl", out_tran="ldo_tran", **kw))
    return acdc, tran


# --- simulate ----------------------------------------------------------------------------------------------------

def simulate(circuit: Circuit, params: dict[str, str], *, workdir: Path | None = None,
             timeout_s: float = 120) -> dict[str, ChildResult]:
    """Writes the parameter file and the two testbenches, runs ngspice on each in a fresh working directory, and
    parses the measurements into ``acdc/nominal`` and ``tran/nominal`` children. ``workdir`` is removed afterwards
    unless the caller gave one (so a failing point's files can be inspected)."""
    own_dir = workdir is None
    workdir = Path(workdir) if workdir is not None else Path(tempfile.mkdtemp(prefix="analoggym_"))
    try:
        if circuit.kind == "amplifier":
            acdc, tran = _amp_paths(circuit, workdir, params)
            acdc_files = [("acdc_dc", _ACDC_DC), ("acdc_ac", _ACDC_AC), ("acdc_gbwpm", _ACDC_GBWPM)]
        else:
            acdc, tran = _ldo_paths(circuit, workdir, params)
            acdc_files = [("ldo_dc", _LDO_DC), ("ldo_lnr_max", _LDO_LNR_MAX), ("ldo_lnr_min", _LDO_LNR_MIN),
                          ("ldo_ac", _LDO_AC)]

        acdc_log = workdir / "acdc.log"
        acdc_ok = _run_ngspice(acdc, acdc_log, timeout_s)
        log_text = acdc_log.read_text(errors="ignore") if acdc_log.exists() else ""
        metrics, missing, any_present = _extract(workdir, log_text, acdc_files)
        if not acdc_ok and not any_present:
            acdc_child = child("acdc", {}, failed="ngspice")
        elif missing:
            acdc_child = child("acdc", metrics, missing=missing)
        else:
            acdc_child = child("acdc", metrics)

        tran_log = workdir / "tran.log"
        tran_ok = _run_ngspice(tran, tran_log, timeout_s)
        if circuit.kind == "amplifier":
            wave = _read_wave(workdir / "tran_wave")
            if not tran_ok and wave is None:
                tran_child = child("tran", {}, failed="ngspice")
            else:
                tran_metrics: dict[str, float] = {}
                tran_missing: list[str] = []
                if wave is None:
                    tran_missing = ["SR", "TS"]
                else:
                    time, vout, vin = wave
                    sr_p, settle_p, sr_n, settle_n = _step_response(time, vin, vout)
                    if math.isnan(sr_p) or math.isnan(sr_n):
                        tran_missing.append("SR")
                    else:
                        tran_metrics["SR"] = min(abs(sr_p), abs(sr_n)) * 1e-6   # V/s -> V/us
                    if math.isnan(settle_p) or math.isnan(settle_n):
                        tran_missing.append("TS")
                    else:
                        tran_metrics["TS"] = max(abs(settle_p), abs(settle_n))
                tran_child = child("tran", tran_metrics, missing=tran_missing) if tran_missing else \
                    child("tran", tran_metrics)
        else:
            tran_log_text = tran_log.read_text(errors="ignore") if tran_log.exists() else ""
            tran_metrics, tran_missing, tran_present = _extract(workdir, tran_log_text, [("ldo_tran", _LDO_TRAN)])
            if not tran_ok and not tran_present:
                tran_child = child("tran", {}, failed="ngspice")
            elif tran_missing:
                tran_child = child("tran", tran_metrics, missing=tran_missing)
            else:
                tran_child = child("tran", tran_metrics)

        return {"acdc/nominal": acdc_child, "tran/nominal": tran_child}
    finally:
        if own_dir:
            shutil.rmtree(workdir, ignore_errors=True)


# --- problems ------------------------------------------------------------------------------------------------------

_UNIT_OF = {  # metric name -> (testbench, ic-opt unit string)
    **{m: ("acdc", u) for m, u in [("GAIN", "dB"), ("GBW", "Hz"), ("PM", "deg"), ("CMRR", "dB"), ("PSRP", "dB"),
                                   ("PSRN", "dB"), ("POWER", "mW"), ("VOS", "V"), ("TC", "1")]},
    "SR": ("tran", "V/us"), "TS": ("tran", "s"),
    **{m: ("acdc", u) for m, u in [("PSRR", "dB"), ("LDR", "1"), ("LNR_MAXLOAD", "1"), ("LNR_MINLOAD", "1"),
                                   ("POWER_MAXLOAD", "mW"), ("POWER_MINLOAD", "mW"), ("VOS_MAXLOAD", "V"),
                                   ("VOS_MINLOAD", "V")]},
    "UNDERSHOOT": ("tran", "V"), "OVERSHOOT": ("tran", "V"),
}


def metric_defs(circuit: Circuit) -> list[dict]:
    names = sorted({m for c in circuit.targets for m in [c["metric"]]} |
                   ({"GAIN", "GBW", "PM", "CMRR", "PSRP", "PSRN", "POWER", "VOS", "TC", "SR", "TS"}
                    if circuit.kind == "amplifier" else
                    {"GAIN", "GBW", "PM", "PSRR", "POWER_MAXLOAD", "POWER_MINLOAD", "VOS_MAXLOAD", "VOS_MINLOAD",
                     "LDR", "LNR_MAXLOAD", "LNR_MINLOAD", "UNDERSHOOT", "OVERSHOOT"}))
    return [{"name": m, "unit": _UNIT_OF[m][1], "testbench": _UNIT_OF[m][0]} for m in names]


def problem(circuit_name: str, *, variables: list[str] | None = None, fixed: dict[str, str] | None = None,
            ranges: dict[str, tuple[str, str, str]] | None = None, constraints: list[dict], objective: dict,
            start: tuple[dict[str, str], ...] = (), scenario: str, name: str) -> Problem:
    """Builds a benchmark ``Problem`` for one AnalogGym circuit. ``variables`` (default: all of the circuit's own
    variables) are the ones the ``Spec`` exposes; the rest are held at ``fixed[name]`` (required for every variable
    left out). ``ranges`` overrides a chosen variable's lower/upper/step."""
    reg = circuits()[circuit_name]
    fixed = fixed or {}
    ranges = ranges or {}
    chosen = list(variables) if variables is not None else [v["name"] for v in reg.variables]
    by_name = {v["name"]: v for v in reg.variables}
    spec_vars = []
    for vname in chosen:
        v = dict(by_name[vname])
        if vname in ranges:
            lo, hi, step = ranges[vname]
            v = {**v, "lower": lo, "upper": hi, "step": step}
        spec_vars.append({"name": v["name"], "kind": v["kind"], "lower": v["lower"], "upper": v["upper"],
                          "step": v["step"]})
    left_out = [vname for vname in by_name if vname not in chosen]
    missing_fixed = [vname for vname in left_out if vname not in fixed]
    if missing_fixed:
        raise ValueError(f"{circuit_name}: variables left out of the spec need a fixed value: {missing_fixed}")
    held = {vname: fixed[vname] for vname in left_out}

    spec = make_spec(name, variables=spec_vars, metrics=metric_defs(reg), constraints=constraints,
                      objective=objective, description=f"AnalogGym {reg.kind} {circuit_name} ({reg.case})")

    def evaluate(point_params: dict[str, str]) -> dict[str, ChildResult]:
        full = {**held, **point_params}
        return simulate(reg, full)

    return Problem(name=name, family="analoggym", scenario=scenario, spec=spec, evaluate=evaluate, start=start)


def native_objective(circuit: Circuit) -> dict:
    if circuit.kind == "amplifier":
        cl_pf = float(parse_scalar(circuit.load["capacitance"])[0])
        return {"direction": "maximize", "expression": f"(GBW/1e6)*{cl_pf}/POWER"}
    return {"direction": "maximize", "expression": "GBW/POWER_MAXLOAD"}


def _native_constraints(circuit: Circuit) -> list[dict]:
    return [{"metric": c["metric"], "op": ("ge" if c["direction"] == "ge" else "le"), "value": str(c["value"])}
            for c in circuit.targets]


def _snap_start(circuit: Circuit) -> tuple[dict[str, str], ...]:
    if not circuit.authors_design:
        return ()
    by_name = {v["name"]: v for v in circuit.variables}
    point = {}
    for vname, v in by_name.items():
        raw = circuit.authors_design.get(vname)
        if raw is None:
            continue
        if v["kind"] == "integer":
            lo, hi, step = int(v["lower"]), int(v["upper"]), int(v["step"])
            n = round((int(float(raw)) - lo) / step)
            point[vname] = str(lo + max(0, min((hi - lo) // step, n)) * step)
        else:
            lo, _ = parse_scalar(v["lower"])
            hi, _ = parse_scalar(v["upper"])
            step, unit = parse_scalar(v["step"])
            value, _ = parse_scalar(raw)
            n = round(float(value - lo) / float(step))
            snapped = lo + max(0, min(int((hi - lo) / step), n)) * step
            point[vname] = f"{snapped.normalize():f}{unit}"
    if len(point) != len(by_name):
        return ()  # the authors' design does not cover every variable of this circuit; no usable start point
    return (point,)


def native_problems() -> dict[str, Callable[[], Problem]]:
    """One problem per circuit, ``ag_<circuit>_native``: every variable, the registry's own ranges, AnalogGym's own
    targets as constraints, and the objective AnalogGym itself reports (GBW*CL/POWER for amplifiers; an analogous
    GBW/POWER figure of merit for LDOs, since AnalogGym states no combined LDO figure of merit)."""
    out: dict[str, Callable[[], Problem]] = {}
    for cname, reg in circuits().items():
        def factory(cname=cname, reg=reg) -> Problem:
            return problem(cname, constraints=_native_constraints(reg), objective=native_objective(reg),
                           start=_snap_start(reg), scenario="wide_range", name=f"ag_{cname}_native")
        out[f"ag_{cname}_native"] = factory
    return out


def calibrated_problems() -> dict[str, Callable[[], Problem]]:
    """Two problems per circuit of ``analoggym_problems.json`` (thresholds, reference design and fine-tuning ranges
    derived from the circuit's survey by ``icopt_bench.calibrate``; none before that file is written):

    - ``ag_<circuit>_wide``: every variable over the registry's ranges, nothing to start from;
    - ``ag_<circuit>_fine``: the six variables that matter most, a few levels around the reference design, the others
      held at it; the reference design is the start.

    Both carry the same thresholds and the objective AnalogGym reports."""
    if not _CALIBRATED.exists():
        return {}
    entries = json.loads(_CALIBRATED.read_text(encoding="utf-8"))["problems"]
    out: dict[str, Callable[[], Problem]] = {}
    for cname, entry in entries.items():
        def wide(cname=cname, entry=entry) -> Problem:
            return problem(cname, constraints=entry["constraints"], objective=native_objective(circuits()[cname]),
                           scenario="wide_range", name=f"ag_{cname}_wide")

        def fine(cname=cname, entry=entry) -> Problem:
            chosen = list(entry["fine_variables"])
            reference = entry["reference"]
            return problem(cname, variables=chosen, fixed={k: v for k, v in reference.items() if k not in chosen},
                           ranges={k: tuple(v) for k, v in entry["fine_ranges"].items()},
                           constraints=entry["constraints"], objective=native_objective(circuits()[cname]),
                           start=({k: reference[k] for k in chosen},), scenario="around_design",
                           name=f"ag_{cname}_fine")

        out[f"ag_{cname}_wide"] = wide
        out[f"ag_{cname}_fine"] = fine
    return out


def problems() -> dict[str, Callable[[], Problem]]:
    """All AnalogGym benchmark problems: the calibrated ones the benchmark compares methods on, and the native ones
    (AnalogGym's own targets, which no point of any survey met) for reference."""
    return {**calibrated_problems(), **native_problems()}
