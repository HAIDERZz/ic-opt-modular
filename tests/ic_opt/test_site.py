"""T15.1: resources come from the user -- site.yaml v2 per host, required spec resource fields, refusal before start, doctor."""

from __future__ import annotations

import inspect
import logging
import re
from pathlib import Path, PurePosixPath

import pytest
import yaml
from typer.testing import CliRunner

from ic_opt import site
from ic_opt.blocks.doctor import doctor, plan_line
from ic_opt.blocks.evaluate import evaluate, plan_shape
from ic_opt.blocks.optimize import optimize
from ic_opt.cli import app
from ic_opt.deck import Deck
from ic_opt.eval import engine
from ic_opt.recipe import PLAN_MODE, Run, load_run
from ic_opt.site import EnvelopeError, HostLimits, Site, SiteError
from ic_opt.space import Point
from ic_opt.spec import Spec
from ic_opt.store import RunStore
from tests.ic_opt.fakes import FakeSpectreExecutor, make_spec, minimal_spec
from tests.ic_opt.test_blocks import maestro_export
from tests.ic_opt.test_cli_recipes import project

runner = CliRunner()
LOCAL_AND_LAB = """\
hosts:
  local: {max_threads: 8, max_memory_gb: 16}
  lab:
    max_threads: 64
    max_memory_gb: 256
    cshrc: /cad/lab.csh
    scratch_root: /scratch/me/ic-opt/
    license_probe: lmutil lmstat -a
    transfer_timeout_s: 600
"""


def write(tmp_path: Path, text: str, name: str = "site.yaml") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


# -- site.yaml ------------------------------------------------------------------------------


def test_a_missing_site_file_refuses_with_the_v2_example(tmp_path):
    with pytest.raises(SiteError) as err:
        site.load(tmp_path / "nope.yaml")
    text = str(err.value)
    assert "nope.yaml not found" in text and "has no defaults" in text and site.EXAMPLE in text
    example = site.load(write(tmp_path, site.EXAMPLE))                # the example itself is a valid v2 file
    assert set(example.hosts) == {"local", "lab"} and example.host("lab").scratch_root == "/scratch/me/ic-opt"


def test_every_number_in_the_example_says_it_is_a_placeholder():
    numbered = [line for line in site.EXAMPLE.splitlines() if re.search(r":\s*\d", line.split("#")[0])]
    assert numbered and all("placeholder" in line for line in numbered)


def test_the_default_file_is_resolved_when_loading(tmp_path, monkeypatch):
    monkeypatch.setattr(site, "SITE_FILE", write(tmp_path, LOCAL_AND_LAB))
    assert site.load().host("local") == HostLimits(max_threads=8, max_memory_gb=16.0)


def test_a_missing_host_names_it_lists_the_known_ones_and_shows_the_entry(tmp_path):
    known = site.load(write(tmp_path, LOCAL_AND_LAB))
    with pytest.raises(SiteError, match=r"no entry for host 'farm' \(known: lab, local\); ic-opt does not guess") as err:
        known.host("farm")
    assert "hosts:\n  farm:\n    max_threads: " in str(err.value) and "placeholder" in str(err.value)


def test_every_field_of_an_entry_is_read_and_typed(tmp_path):
    lab = site.load(write(tmp_path, LOCAL_AND_LAB)).host("lab")
    assert lab == HostLimits(max_threads=64, max_memory_gb=256.0, cshrc="/cad/lab.csh", scratch_root="/scratch/me/ic-opt/",
                             license_probe="lmutil lmstat -a", transfer_timeout_s=600)
    assert isinstance(lab.max_memory_gb, float)
    with pytest.raises(TypeError):
        HostLimits()                                                   # no default limits, not even in code


@pytest.mark.parametrize(("text", "message"), [
    ("hosts:\n  local: {max_threads: 8}\n", "hosts.local lacks max_memory_gb: required, no default"),
    ("hosts:\n  local: {max_memory_gb: 8}\n", "hosts.local lacks max_threads: required, no default"),
    ("hosts:\n  local: {max_threads: 0, max_memory_gb: 8}\n", "max_threads must be a positive integer, got 0"),
    ("hosts:\n  local: {max_threads: 8.5, max_memory_gb: 8}\n", "max_threads must be a positive integer, got 8.5"),
    ("hosts:\n  local: {max_threads: true, max_memory_gb: 8}\n", "max_threads must be a positive integer, got True"),
    ("hosts:\n  local: {max_threads: 8, max_memory_gb: -1}\n", "max_memory_gb must be a positive number, got -1"),
    ("hosts:\n  local: {max_threads: 8, max_memory_gb: .inf}\n", "max_memory_gb must be a positive number, got inf"),
    ("hosts:\n  local: {max_threads: 8, max_memory_gb: 32G}\n", "max_memory_gb must be a positive number, got '32G'"),
    ("hosts:\n  local: {max_threads: 8, max_memory_gb: 8, transfer_timeout_s: 0}\n", "transfer_timeout_s must be a positive integer"),
    ("hosts:\n  local: {max_threads: 8, max_memory_gb: 8, cshrc: ''}\n", "cshrc must be a non-empty string"),
    ("hosts:\n  local: {max_threads: 8, max_memory_gb: 8, max_thread: 8}\n", "hosts.local: unknown key(s) max_thread; an entry takes"),
    ("hosts:\n  local: {max_threads: 8, max_memory_gb: 8}\nlocal: {max_threads: 8}\n", "unknown top-level key(s) local"),
    ("hosts: []\n", "hosts must map host names to their limits"),
    ("hosts:\n  local: 8\n", "hosts.local must be a mapping of limits"),
    ("", "lists no hosts"),
    ("hosts: [unclosed\n", "is not valid YAML"),
])
def test_a_missing_or_unusable_value_refuses(tmp_path, text, message):
    with pytest.raises(SiteError, match=re.escape(message)):
        site.load(write(tmp_path, text))


def test_the_flat_format_is_read_as_hosts_local_and_asks_to_move(tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger="ic_opt.site"):
        flat = site.load(write(tmp_path, "max_threads: 8\nmax_memory_gb: 16\ncshrc: /cad/env.csh\n"))
    assert "uses the flat 0.2.0 format; it is read as hosts.local. Move its keys under 'hosts: local:'" in caplog.text
    assert flat.legacy and flat.hosts == {"local": HostLimits(max_threads=8, max_memory_gb=16.0, cshrc="/cad/env.csh")}
    with pytest.raises(SiteError, match="flat 0.2.0 format, read as hosts.local: move its keys under 'hosts: local:'"):
        flat.host("lab")
    with pytest.raises(SiteError, match="lacks max_threads and max_memory_gb"):
        site.load(write(tmp_path, "cshrc: /cad/env.csh\n", "old.yaml"))   # the old 128 / 128 fallback is gone


def test_slots_has_no_floor():
    host = HostLimits(max_threads=8, max_memory_gb=16)
    assert host.slots(4) == 2 and host.slots(2, 8) == 2 and host.slots(1, 5) == 3
    assert host.slots(10) == 0 and host.slots(1, 32) == 0                          # a job bigger than the host: zero, not one


# -- the spec states its resources ------------------------------------------------------------


@pytest.mark.parametrize("path", ["simulator.parallel_jobs", "simulator.threads_per_run", "simulator.timeout_s",
                                  "em.threads", "em.memory_gb", "em.timeout_s"])
def test_every_resource_field_is_required(path):
    d = minimal_spec()
    d["em"] = {"process_file": "/p/x.proc", "frequencies": [1e9], "threads": 4, "memory_gb": 8, "timeout_s": 60}
    Spec.model_validate(d)
    section, field = path.split(".")
    del d[section][field]
    with pytest.raises(ValueError, match=rf"{re.escape(path)}\n  Field required"):
        Spec.model_validate(d)


@pytest.mark.parametrize("fn", [evaluate, optimize, engine.run, engine.workers_for, plan_shape, doctor, plan_line])
def test_limits_have_no_default_wherever_work_is_sized(fn):
    assert inspect.signature(fn).parameters["limits"].default is inspect.Parameter.empty


# -- refusal before start -----------------------------------------------------------------------


def test_run_jobs_refuses_a_spectre_job_bigger_than_the_host(tmp_path):
    spec = make_spec(simulator={"parallel_jobs": 4, "threads_per_run": 10, "timeout_s": 60})
    hosts = Site({"local": HostLimits(max_threads=8, max_memory_gb=16), "big": HostLimits(max_threads=24, max_memory_gb=16)})
    too_small = Run(tmp_path, spec, RunStore(tmp_path), FakeSpectreExecutor(tmp_path / "sims"), None, hosts, hosts.host("local"))
    with pytest.raises(EnvelopeError, match=r"threads_per_run 10 exceeds max_threads 8 of host 'local'"):
        _ = too_small.jobs
    fits = Run(tmp_path, spec, RunStore(tmp_path), FakeSpectreExecutor(tmp_path / "sims"), None, hosts, hosts.host("big"))
    assert fits.jobs == 2                                                              # min(parallel_jobs 4, 24 // 10)


def test_plan_mode_refuses_a_stage_bigger_than_the_host(tmp_path):
    spec = make_spec(simulator={"parallel_jobs": 2, "threads_per_run": 16, "timeout_s": 60})
    store = RunStore(tmp_path)
    ex = FakeSpectreExecutor(store.root / "sims")
    deck = Deck(templates={("tb", None): "parameters F={{F}} W={{W}}\n"})
    small = HostLimits(max_threads=8, max_memory_gb=16)
    token = PLAN_MODE.set(True)
    try:
        with pytest.raises(EnvelopeError, match="stage spectre needs 16 threads / 0 GB but the executor host allows max_threads 8"):
            evaluate(spec, [Point({"F": "20", "W": "0.6u"}, "user")], ex, store, deck=deck, limits=small)
        with pytest.raises(EnvelopeError, match="stage spectre"):
            optimize(spec, ex, store, deck=deck, budget=2, limits=small)
    finally:
        PLAN_MODE.reset(token)
    assert ex.commands == [] and store.observations() == []


def test_load_run_takes_the_executor_hosts_entry(tmp_path, monkeypatch):
    monkeypatch.delenv("IC_OPT_CADENCE_CSHRC", raising=False)
    root = project(tmp_path)
    hosts = site.load(write(tmp_path, LOCAL_AND_LAB + "  bare: {max_threads: 4, max_memory_gb: 8}\n"))
    local = load_run(root, site=hosts)
    assert local.limits == hosts.host("local") and local.executor.host == "local" and local.site is hosts and local.cshrc is None
    lab = load_run(root, ssh_profile="lab", site=hosts)
    assert lab.limits == hosts.host("lab") and lab.executor.host == "lab" and lab.cshrc == "/cad/lab.csh"
    assert lab.executor.scratch_root == PurePosixPath("/scratch/me/ic-opt/demo") and lab.executor.transfer_timeout_s == 600
    bare = load_run(root, ssh_profile="bare", site=hosts)
    assert bare.executor._scratch_root == PurePosixPath("~/.ic-opt/scratch/demo") and bare.limits.transfer_timeout_s is None
    monkeypatch.setenv("IC_OPT_CADENCE_CSHRC", "/env.csh")
    assert load_run(root, ssh_profile="lab", site=hosts).cshrc == "/env.csh"            # explicit > environment > the host's entry
    assert load_run(root, ssh_profile="lab", cshrc="/given.csh", site=hosts).cshrc == "/given.csh"
    with pytest.raises(SiteError, match="no entry for host 'farm'"):
        load_run(root, ssh_profile="farm", site=hosts)
    fresh = project(tmp_path / "fresh")
    monkeypatch.setattr(site, "SITE_FILE", tmp_path / "absent.yaml")
    with pytest.raises(SiteError, match="absent.yaml not found"):
        load_run(fresh)
    assert not (fresh / ".icopt").exists()                                              # refused before the project is touched


# -- the CLI refuses in one paragraph, exit code 2 ---------------------------------------------


def _refused(result, *fragments: str) -> None:
    assert result.exit_code == 2, result.output
    assert isinstance(result.exception, SystemExit) and "Traceback" not in result.output
    for fragment in fragments:
        assert fragment in result.output, result.output


def test_cli_refuses_without_a_site_file_or_host_entry(tmp_path, monkeypatch):
    root = project(tmp_path)
    monkeypatch.setattr(site, "SITE_FILE", tmp_path / "absent.yaml")
    _refused(runner.invoke(app, ["doctor", str(root)]), "error: ", "absent.yaml not found", "hosts:\n  local:")
    _refused(runner.invoke(app, ["run", "optimize", str(root), "--plan"]), "absent.yaml not found")
    monkeypatch.setattr(site, "SITE_FILE", write(tmp_path, "hosts:\n  local: {max_threads: 16, max_memory_gb: 64}\n"))
    _refused(runner.invoke(app, ["run", "optimize", str(root), "--plan", "--ssh-profile", "lab"]),
             "has no entry for host 'lab' (known: local)", "hosts:\n  lab:")


def test_cli_refuses_a_spec_without_its_resource_fields(tmp_path, monkeypatch):
    root = project(tmp_path)
    monkeypatch.setattr(site, "SITE_FILE", write(tmp_path, "hosts:\n  local: {max_threads: 16, max_memory_gb: 64}\n"))
    d = yaml.safe_load((root / "spec.yaml").read_text())
    del d["simulator"]["threads_per_run"]
    (root / "spec.yaml").write_text(yaml.safe_dump(d))
    _refused(runner.invoke(app, ["doctor", str(root)]), "spec.yaml is not a valid spec: simulator.threads_per_run: Field required.",
             "Resource fields have no defaults")


def test_cli_refuses_a_job_bigger_than_the_host_under_plan(tmp_path, monkeypatch):
    root = project(tmp_path)                                                    # threads_per_run 4
    monkeypatch.setattr(site, "SITE_FILE", write(tmp_path, "hosts:\n  local: {max_threads: 2, max_memory_gb: 64}\n"))
    _refused(runner.invoke(app, ["run", "optimize", str(root), "--plan"]), "threads_per_run 4 exceeds max_threads 2 of host 'local'")
    assert not (root / ".icopt" / "observations.jsonl").exists()


def test_cli_call_hands_the_host_entry_to_the_block(tmp_path, monkeypatch):
    root = project(tmp_path)
    monkeypatch.setattr(site, "SITE_FILE", write(tmp_path, "hosts:\n  local: {max_threads: 16, max_memory_gb: 64}\n"))
    result = runner.invoke(app, ["call", "env.doctor", str(root)])
    assert "[ok] envelope: 2 jobs × 4 threads / 0 GB per job → 8 threads / 0 GB of 16 / 64" in result.output, result.output


# -- env.doctor: the envelope line and the machine probe (D2) ---------------------------------


def _doctor(tmp_path: Path, spec: Spec, limits: HostLimits, **fake):
    return doctor(spec, FakeSpectreExecutor(tmp_path / "sims", **fake), limits=limits)


def spectre_spec(tmp_path: Path, **simulator) -> Spec:
    export = maestro_export(tmp_path / "maestro", "tb")
    return make_spec(testbenches=[{"id": "tb", "maestro_point_root": str(export), "virtuoso_library": "l", "cell": "c", "test_name": "t"}],
                     simulator={"parallel_jobs": 2, "threads_per_run": 4, "timeout_s": 60, "license_check": False, **simulator})


def test_doctor_warns_when_the_entry_exceeds_the_machine_and_never_blocks(tmp_path, capsys):
    report = _doctor(tmp_path, spectre_spec(tmp_path), HostLimits(max_threads=128, max_memory_gb=256), machine=(8, 16.0))
    machine = next(c for c in report.checks if c.name == "machine")
    assert machine.tag == "WARN" and not machine.blocking
    assert machine.detail == ("local: max_threads 128 > 8 cores (nproc); max_memory_gb 256 > 16.0 GB (MemTotal) -- "
                              "the entry allows more than the machine has")
    assert report.ok and "[WARN] machine: local:" in str(report)
    report.require_pass()                                                        # advisory only: nothing raises
    assert "[doctor] [WARN] machine" in capsys.readouterr().out


def test_doctor_notes_a_failed_probe_and_passes_a_fitting_entry(tmp_path):
    spec, limits = spectre_spec(tmp_path), HostLimits(max_threads=8, max_memory_gb=16)
    unprobed = next(c for c in _doctor(tmp_path, spec, limits, machine=None).checks if c.name == "machine")
    assert unprobed.tag == "note" and "limits taken as written" in unprobed.detail and not unprobed.blocking
    fits = next(c for c in _doctor(tmp_path, spec, limits, machine=(8, 31.3)).checks if c.name == "machine")
    assert fits.ok and fits.detail == "local: 8 cores / 31.3 GB; the entry fits"


def test_doctor_keeps_the_spectre_checks_for_a_spec_with_testbenches(tmp_path):
    """R-18: testbenches run Spectre and OCEAN, so the doctor still asks the host for both and, with license_check, for
    the license; a host without Spectre fails there. Without devices it asks for no EMX."""
    spec, limits = spectre_spec(tmp_path, license_check=True), HostLimits(max_threads=16, max_memory_gb=16)
    checks = {c.name: c for c in _doctor(tmp_path, spec, limits, tools={"ocean", "lmstat"}).checks}
    assert not checks["tools"].ok and checks["tools"].detail == "spectre/ocean not on PATH (found: /cad/bin/ocean)"
    assert not checks["license"].ok and checks["license"].detail == "spectre: Command not found." and "emx" not in checks
    full = {c.name: c for c in _doctor(tmp_path, spec, limits).checks}
    assert full["tools"].ok and full["license"].ok and full["export:tb"].ok


def test_doctor_envelope_counts_threads_and_memory_of_the_heaviest_job(tmp_path):
    spectre = next(c for c in _doctor(tmp_path, spectre_spec(tmp_path, parallel_jobs=5), HostLimits(max_threads=16, max_memory_gb=8)).checks
                   if c.name == "envelope")
    assert not spectre.ok and spectre.detail == ("5 jobs × 4 threads / 0 GB per job → 20 threads / 0 GB of 16 / 8 "
                                                 "(max_threads / max_memory_gb of local)")
    d = minimal_spec(testbenches=[], metrics=[], constraints=[], objective=None)
    d["devices"] = [{"id": "ind", "generator": "clean_port_ind_sym", "profile": "demo_6m", "ports": ["P1", "N1"]}]
    d["em"] = {"process_file": "/site/demo.proc", "frequencies": [1e10], "threads": 4, "memory_gb": 64, "timeout_s": 600}
    checks = {c.name: c for c in _doctor(tmp_path, Spec.model_validate(d), HostLimits(max_threads=64, max_memory_gb=100)).checks}
    assert not checks["envelope"].ok and checks["envelope"].detail.startswith("2 jobs × 4 threads / 64 GB per job → 8 threads / 128 GB of 64 / 100")
    assert checks["em:envelope"].ok and "→ 1 concurrent" in checks["em:envelope"].detail     # one EMX run fits; two at once do not
