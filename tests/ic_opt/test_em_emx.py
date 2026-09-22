"""T9.4: the EMX stage — argv, one run per device through the executor, the engine's cache, V3 argv parity with recorded em-opt runs."""

from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pytest

from ic_opt.em import emx, touchstone
from ic_opt.em.pcell.base import EmxPort
from ic_opt.eval import engine
from ic_opt.eval.stage import Resources, StageFailure
from ic_opt.observation import ChildResult
from ic_opt.space import Point
from ic_opt.spec import EmSettings, Spec
from ic_opt.stages.em_chain import Geometry, Pcell, emx_stages
from ic_opt.store import RunStore
from tests.ic_opt.fakes import FakeSpectreExecutor, synthetic_snp
from tests.ic_opt.test_em_pcell import demo_spec

pytest.importorskip("klayout.db")

EM = {"process_file": "/site/n28.proc", "frequencies": {"start_hz": 0, "stop_hz": 200e9, "step_hz": 1e9}, "three_d_metals": ["M6", "M5"],
      "via_separation_um": 0.5, "memory_gb": 64}


class Passthrough:
    """Device child that reads the sNp back: proves the point output carries the S-parameters."""

    name, level, unit, resources = "peek", "child", "device", Resources()

    def fingerprint(self, inp, ctx):
        return None

    def run(self, geometry: Geometry, ctx):
        sp = geometry.sparams[ctx.unit]
        ts = touchstone.read(sp.path)
        return ChildResult(unit=ctx.unit, status="ok", metrics={"s11": float(abs(ts.s[0, 0, 0])), "n": float(ts.n_ports)})


def em_only_spec(**em_overrides) -> Spec:
    d = demo_spec().model_dump(mode="json")
    d["em"] = {**EM, **em_overrides}
    d["devices"][0]["variables"] = {"outer_diameter_um": "outer_diameter_um", "width_um": "width_um"}
    d["variables"].append({"name": "F", "kind": "integer", "lower": "1", "upper": "9", "step": "1"})   # not a device field: same geometry across F
    d["metrics"] = [{"name": "s11", "unit": "1", "device": "ind", "quantity": "s11"}, {"name": "n", "unit": "1", "device": "ind", "quantity": "n"}]
    d["objective"] = {"direction": "minimize", "expression": "s11"}
    return Spec.model_validate(d)


def test_argv_follows_the_legacy_flag_order():
    em = EmSettings(**EM, accuracy="standard", simultaneous_frequencies=0, extra_args=["--foo=1"], modes=["a"])
    ports = emx.numbered_ports(["P1", "N1"], {"P1": "G01", "N1": "G02"})
    argv = emx.argv(em, gds_file="ind.gds", top_cell="ind", s_file="ind.s2p", log_file="emx.log", ports=ports)
    assert argv == ["emx", "--quasistatic", "--format=touchstone", "--s-impedance=50", "--s-file=ind.s2p", "--sweep", "--sweep-stepsize=1000000000",
                    "--via-separation=0.5", "--include-command-line", "--verbose=2", "--log-file=emx.log", "--parallel=4",
                    "--simultaneous-frequencies=0", "--max-memory=64G", "--accuracy=standard", "--3d=M6,M5", "--mode=a", "--foo=1",
                    "-p", "p01=P1:G01", "-p", "p02=N1:G02", "ind.gds", "ind", "/site/n28.proc", "200000000000"]
    grid = EmSettings(**{**EM, "accuracy": {"edge_width_um": 0.5, "max_splits": 3, "thickness_um": 0.5}, "frequencies": [4e10, 6e10]}, verbose=None)
    argv = emx.argv(grid, gds_file="x.gds", top_cell="x", s_file="x.s4p", log_file="emx.log", ports=[])
    assert "--thickness=0.5" in argv and "--max-splits=3" in argv and "--edge-width=0.5" in argv and "--accuracy" not in " ".join(argv)
    assert argv[-2:] == ["40000000000", "60000000000"] and "--sweep" not in argv and "--verbose" not in " ".join(argv)


def test_fingerprint_ignores_scheduling_fields_but_sees_physics_and_the_process_file():
    base = EmSettings(**EM)
    ports = emx.numbered_ports(["P1", "N1"], {})
    key = emx.fingerprint(base, gds_sha256="g", ports=ports, proc_sha256="p")
    assert key == emx.fingerprint(EmSettings(**{**EM, "threads": 16, "memory_gb": 8, "timeout_s": 10, "verbose": None}), gds_sha256="g", ports=ports, proc_sha256="p")
    assert key != emx.fingerprint(EmSettings(**{**EM, "three_d_metals": ["M6"]}), gds_sha256="g", ports=ports, proc_sha256="p")
    assert key != emx.fingerprint(base, gds_sha256="g", ports=ports, proc_sha256="q")
    assert key != emx.fingerprint(base, gds_sha256="g", ports=emx.numbered_ports(["N1", "P1"], {}), proc_sha256="p")


def test_emx_stage_runs_per_device_and_the_engine_caches_it(tmp_path):
    spec = em_only_spec()
    store = RunStore(tmp_path)
    ex = FakeSpectreExecutor(store.root / "sims")
    pipeline = [Pcell(spec), *emx_stages(spec), Passthrough()]
    assert [s.name for s in pipeline] == ["pcell", "emx:ind", "peek"] and pipeline[1].resources == Resources(threads=4, memory_gb=64.0)
    points = [Point({"outer_diameter_um": "100", "width_um": "5", "F": "1"}, "user"), Point({"outer_diameter_um": "100", "width_um": "5.5", "F": "1"}, "user")]
    obs = engine.run(spec, pipeline, points, ex, store, parallel_jobs=1)
    assert [o.status for o in obs] == ["ok", "ok"] and ex.emx_runs == 2 and obs[0].metrics["n"] == 2.0
    assert obs[0].cache == {"emx:ind": "miss"}
    cmd = (store.root / "sims" / "obs_0001" / "em" / "ind" / "emx.cmd").read_text()
    assert cmd.startswith("emx --quasistatic --format=touchstone --s-impedance=50 --s-file=ind.s2p --sweep") and " ind.gds ind /site/n28.proc 200000000000" in cmd
    assert "-p p01=P1:G01 -p p02=N1:G02" in cmd
    assert (store.root / "sims" / "obs_0001" / "em" / "ind" / "ind.s2p").exists() and (store.root / "sims" / "obs_0001" / "em" / "ind" / "emx.log").exists()

    again = engine.run(spec, pipeline, [Point({"outer_diameter_um": "100", "width_um": "5", "F": "2"}, "user")], ex, store, step="again")
    assert again[0].cache == {"emx:ind": "hit"} and ex.emx_runs == 2 and again[0].metrics == obs[0].metrics
    assert (store.root / "sims" / "obs_0003" / "em" / "ind" / "ind.s2p").read_bytes() == (store.root / "sims" / "obs_0001" / "em" / "ind" / "ind.s2p").read_bytes()


def test_emx_failure_and_bad_output_are_stage_failures(tmp_path):
    spec = em_only_spec()
    store = RunStore(tmp_path)
    failing = FakeSpectreExecutor(store.root / "sims", fail_emx=lambda device: True)
    obs = engine.run(spec, [Pcell(spec), *emx_stages(spec), Passthrough()], [Point({"outer_diameter_um": "100", "width_um": "5", "F": "1"}, "user")], failing, store)
    assert obs[0].status == "failed:emx:ind" and "emx exited 3" in obs[0].issues[0] and (store.root / "sims" / "obs_0001" / "em" / "ind" / "emx.stderr").read_text().startswith("emx: license")

    bad = FakeSpectreExecutor(store.root / "sims", snp_fn=lambda argv, n, z0: "# Hz S RI R 50\n1e9 0 0 0 0 0 0 0 0\n")   # no EMX header line
    obs = engine.run(spec, [Pcell(spec), *emx_stages(spec), Passthrough()], [Point({"outer_diameter_um": "110", "width_um": "5", "F": "1"}, "user")], bad, store)
    assert obs[0].status == "failed:emx:ind" and any("EMX command line" in i for i in obs[0].issues)


def test_touchstone_reader_round_trips_the_synthetic_file(tmp_path):
    path = tmp_path / "x.s2p"
    path.write_text(synthetic_snp(["emx"], 2, 50.0))
    ts = touchstone.read(path)
    assert ts.n_ports == 2 and ts.z0 == 50.0 and list(ts.freqs) == [1e9, 5e9, 10e9]
    z = 50.0 * np.linalg.solve(np.eye(2) - ts.s[0], np.eye(2) + ts.s[0])
    assert z[0, 0] == pytest.approx(1.0 + 2j * np.pi * 1e9 * 1e-9, rel=1e-6) and z[0, 1] == pytest.approx(0.5j * 2 * np.pi * 1e9 * 1e-9, rel=1e-6)
    assert touchstone.header_issues(path, expected_ports=2, z0=50.0) == []
    assert touchstone.header_issues(path, expected_ports=4, z0=75.0) == ["touchstone extension declares 2 ports, expected 4", "touchstone option line is not '# Hz S RI R 75'"]
    with pytest.raises(StageFailure):
        emx.process_file_digest(EmSettings(**EM), _ctx_with(tmp_path, ok=False))


def _ctx_with(tmp_path, *, ok):
    from ic_opt.eval.stage import StageContext
    from ic_opt.executor import CommandResult, LocalExecutor

    class Ex(LocalExecutor):
        def run(self, command, **kw):
            return CommandResult(0 if ok else 1, "deadbeef  x\n", "no such file", ["sha256sum"], 0.0)

    store = RunStore(tmp_path)
    return StageContext(spec=em_only_spec(), executor=Ex(store.root / "sims"), store=store, obs_id="o", workdir=tmp_path, remote_dir="r")


# -- V3: argv parity with recorded em-opt EMX runs ------------------------------------------

RECORDED = [Path(p) for p in os.environ.get("IC_OPT_EM_RECORDED_ARGV", "").split(":") if p]   # emx_manifest.json files


@pytest.mark.skipif(not RECORDED, reason="set IC_OPT_EM_RECORDED_ARGV to em-opt emx_manifest.json files")
@pytest.mark.parametrize("manifest", RECORDED, ids=[p.parent.parent.name for p in RECORDED])
def test_argv_matches_recorded_emx_manifests(manifest):
    """Rebuild the command line from the recorded EmxRunConfig; every flag must match except the candidate-specific paths."""
    m = json.loads(manifest.read_text())
    c = m["config"]
    sweep = c["sweep"]
    freqs = {"start_hz": sweep["start_hz"] or 0, "stop_hz": sweep["stop_hz"], "step_hz": sweep["step_hz"], "num_steps": sweep["num_steps"]} if sweep["enabled"] else c["frequencies_hz"]
    grid = {k: c[k] for k in ("edge_width_um", "max_splits", "thickness_um") if c.get(k) is not None}
    extra = [a for a in c["extra_args"] if not a.startswith(("--max-memory", "--simultaneous-frequencies", "--parallel"))]
    memory = c.get("max_memory_gb") or next((float(a.split("=")[1].rstrip("G")) for a in c["extra_args"] if a.startswith("--max-memory")), 32.0)
    simultaneous = c["simultaneous_frequencies"]
    if simultaneous is None and any(a.startswith("--simultaneous-frequencies") for a in c["extra_args"]):
        simultaneous = int(next(a for a in c["extra_args"] if a.startswith("--simultaneous-frequencies")).split("=")[1])
    em = EmSettings(binary=c["binary"], process_file=c["process_file"], mode=c["mode"], frequencies=freqs, accuracy=c["accuracy"] if c["accuracy"] else (grid or None),
                    three_d_metals=c["three_d_metals"], via_separation_um=c["via_separation_um"], via_inductance=c["via_inductance"], via_sidewalls=c["via_sidewalls"],
                    modes=c["modes"], s_impedance=c["s_impedance"], threads=c["parallel"], memory_gb=memory, simultaneous_frequencies=simultaneous,
                    verbose=c["verbose"], extra_args=extra)
    ports = [EmxPort(p["name"], p["signal"], p["reference"]) for p in c["ports"]]
    ours = emx.argv(em, gds_file=c["gds_file"], top_cell=c["top_cell"], s_file=c["s_file"], log_file=c["log_file"], ports=ports)
    assert sorted(ours) == sorted(m["argv"])
    assert ours[-4:] == m["argv"][-4:]                     # positional tail: gds, top cell, process file, frequency


def test_doctor_checks_the_em_sections(tmp_path):
    from ic_opt.blocks.doctor import doctor
    from ic_opt.site import Site

    spec = em_only_spec()
    store = RunStore(tmp_path)
    report = doctor(spec, FakeSpectreExecutor(store.root / "sims"), store=store, site=Site(max_threads=128, max_memory_gb=128))
    names = {c.name: c for c in report.checks}
    assert names["device:ind"].ok and names["device:ind"].detail == "clean_port_ind_sym on demo_6m"
    assert names["emx"].ok and names["emx"].detail == "/cad/bin/emx"
    assert not names["em:process_file"].ok                       # /site/n28.proc does not exist on the fake host
    assert names["em:envelope"].ok and names["em:envelope"].detail.startswith("4 threads / 64 GB per EMX → 2 concurrent")

    missing = spec.model_copy(update={"devices": [spec.devices[0].model_copy(update={"profile": "nope"})]})
    bad = {c.name: c for c in doctor(missing, FakeSpectreExecutor(store.root / "sims"), site=Site()).checks}
    assert not bad["device:ind"].ok and "unsupported process rule profile: nope" in bad["device:ind"].detail
