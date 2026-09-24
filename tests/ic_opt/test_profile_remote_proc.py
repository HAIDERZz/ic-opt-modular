"""N-3: em.validate_profile reads ``proc=`` on the simulation host through ``--ssh-profile``'s executor -- downloaded into
a temporary directory, checked, deleted -- because a site's EMX .proc lives on that host (and may not leave it for
long); without ``--ssh-profile`` it reads the controller's path, as before (ADR-0001: no same-named local fallback)."""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

import ic_opt.executor.ssh as ssh_module
from ic_opt import site
from ic_opt.blocks.profile import validate_profile
from ic_opt.cli import app
from ic_opt.executor import SshExecutor
from tests.ic_opt.pcell.test_profile_validation import MINIMAL_PROFILE, PROC_TEXT, write_profile

runner = CliRunner()
REMOTE_PROC = "/site/pdk/emx/demo.proc"                  # a path on the host only: nothing on the controller answers to it


class ProcHost:
    """A fake OpenSSH transport to one host holding ``files`` (path -> text): ``test -d`` answers no, ``scp`` copies a
    file it holds to the local target and fails as scp does for any other path. Every call is recorded."""

    def __init__(self, files: dict[str, str]):
        self.files, self.calls, self.targets = dict(files), [], []

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        if argv[0] != "scp":                                          # the directory probe before a download
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="")
        path, target = shlex.split(argv[-2].split(":", 1)[1])[0], Path(argv[-1])
        self.targets.append(target)
        if path not in self.files:
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr=f"scp: {path}: No such file or directory")
        target.write_text(self.files[path], encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")


@pytest.fixture
def profile_dir(tmp_path: Path) -> Path:
    return write_profile(tmp_path / "profiles", MINIMAL_PROFILE) / "min2m"


def test_the_proc_is_read_through_the_executor_then_deleted(profile_dir):
    assert not Path(REMOTE_PROC).exists()
    host = ProcHost({REMOTE_PROC: PROC_TEXT})
    report = validate_profile(profile_dir, proc=REMOTE_PROC, executor=SshExecutor("lab", "/tmp/icopt", execute=host))
    assert report.passed, report.format()
    text = report.format()
    assert "[emx-names-vs-proc] PASS" in text and "[gds-layers-vs-proc] PASS" in text and "found in demo.proc" in text
    assert [call[0] for call in host.calls] == ["ssh", "scp"] and host.calls[1][-2] == f"lab:{REMOTE_PROC}"
    held = host.targets[0].parent                                      # the temporary directory the download went to
    assert held.name.startswith("ic-opt-proc-") and not held.exists()


def test_a_proc_the_host_cannot_serve_fails_the_proc_stages_only(profile_dir):
    host = ProcHost({})
    report = validate_profile(profile_dir, proc="/site/pdk/emx/missing.proc", executor=SshExecutor("lab", "/tmp/icopt", execute=host))
    stages = {s.name: s for s in report.stages}
    assert not report.passed and stages["schema"].status == "PASS" and stages["consistency"].status == "PASS"
    why = ("cannot read proc file /site/pdk/emx/missing.proc on lab: download /site/pdk/emx/missing.proc: return code 1: "
           "scp: /site/pdk/emx/missing.proc: No such file or directory")
    for name in ("emx-names-vs-proc", "gds-layers-vs-proc"):
        assert stages[name].status == "FAIL" and stages[name].details == [why]
    assert not host.targets[0].parent.exists()


def test_without_an_executor_the_proc_is_the_controllers_path(profile_dir, tmp_path):
    local = tmp_path / "demo.proc"
    local.write_text(PROC_TEXT, encoding="utf-8")
    assert validate_profile(profile_dir, proc=str(local)).passed
    unread = {s.name: s for s in validate_profile(profile_dir, proc=REMOTE_PROC).stages}   # never looked up on a host
    assert unread["emx-names-vs-proc"].status == "FAIL" and "cannot read proc file" in unread["emx-names-vs-proc"].details[0]


def test_call_with_ssh_profile_reads_the_proc_on_that_host(profile_dir, tmp_path, monkeypatch):
    """The command line builds the host's executor from its site.yaml entry, as a run does; a host without an entry is
    refused with the entry to add (exit 2), and nothing is read."""
    monkeypatch.setattr(site, "SITE_FILE", tmp_path / "site.yaml")
    (tmp_path / "site.yaml").write_text("hosts:\n  lab: {max_threads: 8, max_memory_gb: 16, transfer_timeout_s: 60}\n", encoding="utf-8")
    host = ProcHost({REMOTE_PROC: PROC_TEXT})
    monkeypatch.setattr(ssh_module, "_default_execute", host)
    result = runner.invoke(app, ["call", "em.validate_profile", str(profile_dir), f"proc={REMOTE_PROC}", "--ssh-profile", "lab"])
    assert result.exit_code == 0, result.output
    assert "[gds-layers-vs-proc] PASS" in result.output and "result: PASS" in result.output
    assert any(call[0] == "scp" and call[-2] == f"lab:{REMOTE_PROC}" for call in host.calls)

    host.calls.clear()
    refused = runner.invoke(app, ["call", "em.validate_profile", str(profile_dir), f"proc={REMOTE_PROC}", "--ssh-profile", "far"])
    assert refused.exit_code == 2 and "has no entry for host 'far' (known: lab)" in refused.output and not host.calls
