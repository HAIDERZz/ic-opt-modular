"""N-82: an AnalogGym simulation that did not finish is a failed simulation, and the files it left are not read.
A transient run that hit its timeout, crashed or exited nonzero never has its waveform used for metrics, and an ac/dc
run that hit its timeout is no different (a nonzero exit of an ac/dc run keeps its own rules: not tested here).

A real ngspice is not needed (and not used): ``ICOPT_BENCH_NGSPICE`` points at a small shell script that drops the files
a scenario asks for into the working directory and then either exits or keeps running until it is killed, and
``ICOPT_BENCH_DATA`` points at a stand-in clone holding only the parameter-file templates ``simulate`` reads."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import numpy as np
import pytest
from icopt_bench import analoggym as ag

_POSIX = pytest.mark.skipif(os.name != "posix", reason="the fake ngspice is a shell script; the runner kills a process group")

TIMEOUT_S = 1.5          # the hanging fake is killed at this; the files it drops arrive within milliseconds

_FAKE_NGSPICE = """#!/bin/sh
# Stands in for `ngspice -b -o <log> <cir>` (run in the cir's directory). What it does is read from the scenario
# directory it sits in: files under <run>/ are dropped into the working directory, <run>.hang keeps it running (the
# caller has to kill it), <run>.crash kills it with a signal, <run>.exit is the exit code otherwise; <run> is the
# cir's stem.
here=$(dirname "$0")
run=$(basename "$4" .cir)
: > "$3"
if [ -d "$here/$run" ]; then cp "$here/$run"/* .; fi
if [ -e "$here/$run.hang" ]; then exec sleep 60; fi
if [ -e "$here/$run.crash" ]; then kill -KILL $$; fi
if [ -e "$here/$run.exit" ]; then exit "$(cat "$here/$run.exit")"; fi
exit 0
"""


class FakeNgspice:
    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir()
        self.script = root / "ngspice"
        self.script.write_text(_FAKE_NGSPICE)
        self.script.chmod(self.script.stat().st_mode | stat.S_IXUSR)

    def writes(self, run: str, **files: str) -> None:
        (self.root / run).mkdir(exist_ok=True)
        for name, text in files.items():
            (self.root / run / name).write_text(text)

    def hangs(self, run: str) -> None:
        (self.root / f"{run}.hang").touch()

    def exits(self, run: str, code: int) -> None:
        (self.root / f"{run}.exit").write_text(str(code))

    def crashes(self, run: str) -> None:
        (self.root / f"{run}.crash").touch()


@pytest.fixture
def fake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeNgspice:
    """The fake ngspice, plus a clone with just the parameter-file template of each circuit these tests use."""
    ngspice = FakeNgspice(tmp_path / "fake_ngspice")
    monkeypatch.setenv("ICOPT_BENCH_NGSPICE", str(ngspice.script))
    monkeypatch.setenv("ICOPT_BENCH_DATA", str(tmp_path / "clone"))
    for name in ("leung_nmcf_pin_3", "ldo_simple"):
        template = tmp_path / "clone" / "AnalogGym" / ag.circuits()[name].vars_template
        template.parent.mkdir(parents=True, exist_ok=True)
        template.write_text("* parameters\n")
    return ngspice


def _scalar_file(metrics: list[tuple[str, str]]) -> str:
    """A ``wrdata`` file of scalar ``.let``s: one (x, value) pair per metric on a row."""
    row = "  ".join(f"0.0  {1.0 + i}" for i, _ in enumerate(metrics))
    return f"{row}\n{row}\n"


def _scalar_file_cut_in_a_number(metrics: list[tuple[str, str]]) -> str:
    """The same file, killed while writing its first row: every pair whole but the last, whose value is cut after its
    first digit (``6`` of ``61.7``) -- still a number, so it reads as a result."""
    pairs = ["0.0  1.0"] * (len(metrics) - 1) + ["0.0  6"]
    return "  ".join(pairs)


def _step_response_rows() -> list[str]:
    """The ``wrdata <file> v(vout3) v(visr)`` rows of a unity-gain buffer's step response: time, vout, time, vin -- a
    0.3 V -> 0.5 V step at once, back at 200 us, the output a first-order lag behind it (settled well before the
    next edge), 401 rows in all."""
    time = np.arange(401) * 1e-6
    vin = np.where((time >= 1e-6) & (time < 201e-6), 0.5, 0.3)
    vout, level = np.empty_like(vin), 0.3
    for i, target in enumerate(vin):
        level += (target - level) * (1 - np.exp(-1e-6 / 5e-6))
        vout[i] = level
    return [f"{t:.15e}  {o:.15e}  {t:.15e}  {v:.15e}" for t, o, v in zip(time, vout, vin, strict=True)]


def _whole_wave() -> str:
    return "\n".join(_step_response_rows()) + "\n"


def _wave_cut_at_a_line_boundary() -> str:
    """300 of the 401 rows, every one whole: the falling edge (row 201) and its settling are in, so it analyses like
    a result."""
    return "\n".join(_step_response_rows()[:300]) + "\n"


def _wave_cut_in_a_line() -> str:
    """The same 300 rows and then half of the next one (time and vout, no second pair), no trailing newline."""
    rows = _step_response_rows()
    return "\n".join(rows[:300]) + "\n" + "  ".join(rows[300].split()[:2])


def _amplifier_acdc_files() -> dict[str, str]:
    return {"acdc_dc": _scalar_file(ag._ACDC_DC), "acdc_ac": _scalar_file(ag._ACDC_AC),
            "acdc_gbwpm": _scalar_file(ag._ACDC_GBWPM)}


def _ldo_acdc_files() -> dict[str, str]:
    return {"ldo_dc": _scalar_file(ag._LDO_DC), "ldo_lnr_max": _scalar_file(ag._LDO_LNR_MAX),
            "ldo_lnr_min": _scalar_file(ag._LDO_LNR_MIN), "ldo_ac": _scalar_file(ag._LDO_AC)}


_ACDC_FILE_SETS = {
    "leung_nmcf_pin_3": [("acdc_dc", ag._ACDC_DC), ("acdc_ac", ag._ACDC_AC), ("acdc_gbwpm", ag._ACDC_GBWPM)],
    "ldo_simple": [("ldo_dc", ag._LDO_DC), ("ldo_lnr_max", ag._LDO_LNR_MAX), ("ldo_lnr_min", ag._LDO_LNR_MIN),
                   ("ldo_ac", ag._LDO_AC)],
}


def _simulate(name: str, workdir: Path):
    workdir.mkdir()
    return ag.simulate(ag.circuits()[name], {}, workdir=workdir, timeout_s=TIMEOUT_S)


def _never_read_wave(path: Path):
    raise AssertionError(f"the waveform of a run that did not finish was read: {path}")


def _never_extract(*args, **kwargs):
    raise AssertionError("the output files of a run that hit its timeout were read")


# --- the premises: the files these tests cut are the dangerous kind --------------------------------------------------

def test_the_cut_waveforms_are_the_dangerous_kinds() -> None:
    """What the timeout tests rely on: cut at a line boundary the waveform reads as a result, cut in a line its rows
    are ragged, and uncut it reads as a result too."""
    for text in (_whole_wave(), _wave_cut_at_a_line_boundary()):
        rows = [[float(tok) for tok in line.split()] for line in text.splitlines()]
        assert {len(row) for row in rows} == {4}
        arr = np.array(rows)
        sr_p, settle_p, sr_n, settle_n = ag._step_response(arr[:, 0], arr[:, 3], arr[:, 1])
        assert not any(np.isnan([sr_p, settle_p, sr_n, settle_n]))
    assert {len(line.split()) for line in _wave_cut_in_a_line().splitlines()} == {4, 2}


def test_a_scalar_file_cut_in_a_number_reads_as_a_result(tmp_path: Path) -> None:
    """What the ac/dc timeout test relies on: killed inside its row, a scalar file reads with a wrong last value."""
    path = tmp_path / "acdc_gbwpm"
    path.write_text(_scalar_file_cut_in_a_number(ag._ACDC_GBWPM))
    assert ag._read_row(path, len(ag._ACDC_GBWPM)) == [1.0, 6.0]


def test_read_wave_returns_none_on_ragged_rows(tmp_path: Path) -> None:
    """A line cut inside a number leaves rows of different lengths: a malformed file, not an exception. Whole rows, cut
    or not, still read."""
    path = tmp_path / "tran_wave"
    path.write_text(_wave_cut_in_a_line())
    assert ag._read_wave(path) is None
    for text, rows in ((_whole_wave(), 401), (_wave_cut_at_a_line_boundary(), 300)):
        path.write_text(text)
        wave = ag._read_wave(path)
        assert wave is not None and len(wave[0]) == rows


# --- _run_ngspice tells the three endings apart ----------------------------------------------------------------------

@_POSIX
def test_run_ngspice_tells_a_timeout_from_a_failure_from_a_clean_exit(fake: FakeNgspice, tmp_path: Path) -> None:
    fake.exits("bad", 3)
    fake.hangs("slow")
    for stem in ("ok", "bad", "slow"):
        (tmp_path / f"{stem}.cir").write_text("* netlist\n")

    def run(stem: str, timeout_s: float = 30) -> ag.NgspiceOutcome:
        return ag._run_ngspice(tmp_path / f"{stem}.cir", tmp_path / f"{stem}.log", timeout_s)

    assert run("ok") is ag.NgspiceOutcome.FINISHED
    assert run("bad") is ag.NgspiceOutcome.FAILED
    assert run("slow", timeout_s=0.5) is ag.NgspiceOutcome.TIMED_OUT
    # anything that is not a clean exit is falsy, as the bool this function used to return was
    assert bool(ag.NgspiceOutcome.FINISHED)
    assert not ag.NgspiceOutcome.FAILED
    assert not ag.NgspiceOutcome.TIMED_OUT


# --- a transient simulation that hit its timeout is a failed simulation ----------------------------------------------

@_POSIX
@pytest.mark.parametrize("cut", [_wave_cut_at_a_line_boundary, _wave_cut_in_a_line],
                         ids=["cut_at_a_line_boundary", "cut_in_a_line"])
def test_amplifier_transient_timeout_is_a_failure_and_the_waveform_is_not_read(
        fake: FakeNgspice, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cut) -> None:
    fake.writes("acdc", **_amplifier_acdc_files())
    fake.writes("tran", tran_wave=cut())
    fake.hangs("tran")
    monkeypatch.setattr(ag, "_read_wave", _never_read_wave)

    children = _simulate("leung_nmcf_pin_3", tmp_path / "work")

    assert (tmp_path / "work" / "tran_wave").exists()            # the cut file is there to be read, and is not
    tran = children["tran/nominal"]
    assert tran.status == "failed:ngspice"
    assert tran.issues == [f"ngspice timed out after {TIMEOUT_S:g} seconds"]
    assert tran.metrics == {}
    acdc = children["acdc/nominal"]                              # the other testbench is its own run: untouched
    assert acdc.status == "ok" and set(acdc.metrics) == {m for _, m in ag._ACDC_DC + ag._ACDC_AC + ag._ACDC_GBWPM}


@_POSIX
def test_ldo_transient_timeout_is_a_failure_and_its_file_is_not_read(fake: FakeNgspice, tmp_path: Path) -> None:
    fake.writes("acdc", **_ldo_acdc_files())
    fake.writes("tran", ldo_tran=_scalar_file(ag._LDO_TRAN))     # whole, so that reading it would give metrics
    fake.hangs("tran")

    children = _simulate("ldo_simple", tmp_path / "work")

    tran = children["tran/nominal"]
    assert tran.status == "failed:ngspice"
    assert tran.issues == [f"ngspice timed out after {TIMEOUT_S:g} seconds"]
    assert tran.metrics == {}
    assert children["acdc/nominal"].status == "ok"


# --- a transient run that crashed or exited nonzero is a failed simulation too --------------------------------------

def _end_badly(fake: FakeNgspice, run: str, ending: str) -> None:
    if ending == "exit_1":
        fake.exits(run, 1)
    else:
        fake.crashes(run)


@_POSIX
@pytest.mark.parametrize("ending", ["exit_1", "killed_by_a_signal"])
@pytest.mark.parametrize("wave", [_wave_cut_in_a_line, _wave_cut_at_a_line_boundary, _whole_wave],
                         ids=["ragged", "short", "whole"])
def test_amplifier_transient_that_did_not_finish_is_a_failure_whatever_it_left(
        fake: FakeNgspice, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ending: str, wave) -> None:
    fake.writes("acdc", **_amplifier_acdc_files())
    fake.writes("tran", tran_wave=wave())
    _end_badly(fake, "tran", ending)
    monkeypatch.setattr(ag, "_read_wave", _never_read_wave)

    children = _simulate("leung_nmcf_pin_3", tmp_path / "work")

    assert (tmp_path / "work" / "tran_wave").exists()
    tran = children["tran/nominal"]
    assert tran.status == "failed:ngspice"
    assert tran.issues == ["ngspice did not finish"]             # it did not time out
    assert tran.metrics == {}
    assert children["acdc/nominal"].status == "ok"


@_POSIX
@pytest.mark.parametrize("ending", ["exit_1", "killed_by_a_signal"])
def test_ldo_transient_that_did_not_finish_is_a_failure_whatever_it_left(
        fake: FakeNgspice, tmp_path: Path, ending: str) -> None:
    fake.writes("acdc", **_ldo_acdc_files())
    fake.writes("tran", ldo_tran=_scalar_file(ag._LDO_TRAN))
    _end_badly(fake, "tran", ending)

    children = _simulate("ldo_simple", tmp_path / "work")

    tran = children["tran/nominal"]
    assert tran.status == "failed:ngspice"
    assert tran.issues == ["ngspice did not finish"]
    assert tran.metrics == {}
    assert children["acdc/nominal"].status == "ok"


# --- an ac/dc run that hit its timeout is a failed simulation ----------------------------------------------------

def _normal_tran_files(circuit: str) -> dict[str, str]:
    return {"tran_wave": _whole_wave()} if circuit == "leung_nmcf_pin_3" else \
        {"ldo_tran": _scalar_file(ag._LDO_TRAN)}


@_POSIX
@pytest.mark.parametrize("circuit", ["leung_nmcf_pin_3", "ldo_simple"])
def test_acdc_timeout_is_a_failure_and_its_files_are_not_read(
        fake: FakeNgspice, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, circuit: str) -> None:
    *whole, (last_name, last_metrics) = _ACDC_FILE_SETS[circuit]
    fake.writes("acdc", **{name: _scalar_file(metrics) for name, metrics in whole},
                **{last_name: _scalar_file_cut_in_a_number(last_metrics)})
    fake.hangs("acdc")
    fake.writes("tran", **_normal_tran_files(circuit))
    extract_calls: list[set[str]] = []
    real_extract = ag._extract

    def spy(workdir, log_text, files_and_names):
        extract_calls.append({name for name, _ in files_and_names})
        return real_extract(workdir, log_text, files_and_names)

    monkeypatch.setattr(ag, "_extract", spy)

    children = _simulate(circuit, tmp_path / "work")

    assert (tmp_path / "work" / last_name).exists()              # the cut file is there to be read, and is not
    acdc = children["acdc/nominal"]
    assert acdc.status == "failed:ngspice"
    assert acdc.issues == [f"ngspice timed out after {TIMEOUT_S:g} seconds"]
    assert acdc.metrics == {}
    assert not any(names & {name for name, _ in _ACDC_FILE_SETS[circuit]} for names in extract_calls)
    assert children["tran/nominal"].status == "ok"               # the transient run is its own, and finished


# --- a normal run, and an ordinary failure, are as they were ---------------------------------------------------------

@_POSIX
def test_amplifier_normal_run_is_unchanged(fake: FakeNgspice, tmp_path: Path) -> None:
    fake.writes("acdc", **_amplifier_acdc_files())
    fake.writes("tran", tran_wave=_whole_wave())

    children = _simulate("leung_nmcf_pin_3", tmp_path / "work")

    assert children["acdc/nominal"].status == "ok"
    tran = children["tran/nominal"]
    assert tran.status == "ok", tran.issues
    assert set(tran.metrics) == {"SR", "TS"}
    assert tran.metrics["SR"] > 0 and tran.metrics["TS"] > 0


@_POSIX
def test_ldo_normal_run_is_unchanged(fake: FakeNgspice, tmp_path: Path) -> None:
    fake.writes("acdc", **_ldo_acdc_files())
    fake.writes("tran", ldo_tran=_scalar_file(ag._LDO_TRAN))

    children = _simulate("ldo_simple", tmp_path / "work")

    assert children["acdc/nominal"].status == "ok"
    assert children["tran/nominal"].status == "ok"
    assert set(children["tran/nominal"].metrics) == {"UNDERSHOOT", "OVERSHOOT"}


@_POSIX
@pytest.mark.parametrize("circuit", ["leung_nmcf_pin_3", "ldo_simple"])
def test_a_nonzero_exit_with_no_output_keeps_its_own_issue_line(
        fake: FakeNgspice, tmp_path: Path, circuit: str) -> None:
    fake.exits("acdc", 1)
    fake.exits("tran", 1)

    children = _simulate(circuit, tmp_path / "work")

    for child in children.values():
        assert child.status == "failed:ngspice"
        assert child.issues == ["ngspice did not finish"]        # not the timeout line: it did not time out
        assert child.metrics == {}
