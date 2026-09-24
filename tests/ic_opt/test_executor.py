import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import ic_opt.executor.local as local_executor
from ic_opt.executor import (
    CommandTimeout,
    CommandUnavailable,
    ExecutorError,
    LocalExecutor,
    SshExecutor,
    TransportError,
    hook_shell,
    shell_program,
)


def test_shell_program_wraps_cwd_and_cshrc():
    assert shell_program("spectre x.scs") == ["/bin/sh", "-c", "spectre x.scs"]
    assert shell_program("ls", cwd="/w d") == ["/bin/sh", "-c", "cd '/w d' && ls"]
    assert shell_program("ls", cwd="/w", cshrc="/e/c.csh") == ["csh", "-fc", "source /e/c.csh; cd /w; ls"]


# -- the environment file (site.yaml `cshrc`): csh or sh by its name (R-18) -------------------------


@pytest.mark.parametrize(("path", "shell"), [
    ("/e/cadence.csh", "csh"), ("/home/me/.cshrc", "csh"), ("/e/cadence.cshrc", "csh"), ("/e/setup.tcsh", "csh"),
    ("/home/me/.tcshrc", "csh"), ("/e/SETUP.CSH", "csh"),
    ("/e/cadence.sh", "sh"), ("/e/cadence.bash", "sh"), ("/e/cadence_env", "sh"), ("/e/csh.sh", "sh"), ("/e/cshrc", "sh"),
])
def test_the_environment_file_picks_its_shell_by_its_name(path, shell):
    assert hook_shell(path) == shell


def test_shell_program_sources_an_sh_environment_file_in_sh():
    """Any file that is not a csh file is sourced by POSIX sh with `.`, and the command runs in that sh. A bare name is
    made relative (`.` would look it up on PATH, where csh's `source` reads the working directory); the file's own exit
    status does not stop the command."""
    assert shell_program("ls", cwd="/w", cshrc="/e/env.sh") == ["/bin/sh", "-c", ". /e/env.sh; cd /w && ls"]
    assert shell_program("ls", cshrc="/e/my env") == ["/bin/sh", "-c", ". '/e/my env'; ls"]
    assert shell_program("ls", cshrc="env.sh") == ["/bin/sh", "-c", ". ./env.sh; ls"]


def test_an_sh_environment_file_sets_up_the_command_on_this_host(tmp_path):
    hook = tmp_path / "cadence env.sh"
    hook.write_text('IC_OPT_HOOK="from sh"\nexport IC_OPT_HOOK\ntest -n ""\n', encoding="utf-8")     # ends on a failed test
    ex = LocalExecutor(tmp_path / "scratch")
    work = ex.scratch("obs_0001")
    result = ex.run('echo "$IC_OPT_HOOK"; pwd', cwd=work, cshrc=str(hook))
    assert result.ok and result.stdout.splitlines() == ["from sh", work]


@pytest.mark.skipif(shutil.which("csh") is None, reason="csh is not installed here")
def test_a_csh_environment_file_sets_up_the_command_on_this_host(tmp_path):
    hook = tmp_path / "cadence.cshrc"
    hook.write_text("setenv IC_OPT_HOOK from_csh\n", encoding="utf-8")
    ex = LocalExecutor(tmp_path / "scratch")
    work = ex.scratch("obs_0001")
    result = ex.run("echo $IC_OPT_HOOK; pwd", cwd=work, cshrc=str(hook))
    assert result.ok and result.stdout.splitlines() == ["from_csh", work]


# -- local ----------------------------------------------------------------------

def test_local_executor_runs_copies_and_probes(tmp_path):
    ex = LocalExecutor(tmp_path / "scratch")
    work = ex.scratch("obs_0001/tb")
    assert Path(work).is_dir() and ex.exists(work) and not ex.exists(work + "/nope")

    result = ex.run("echo hi; pwd; echo err 1>&2", cwd=work)
    assert result.ok and result.stdout.splitlines() == ["hi", work] and result.stderr.strip() == "err"
    assert ex.run("exit 3").returncode == 3

    src = tmp_path / "in.scs"
    src.write_text("x")
    ex.put(src, work + "/input.scs")
    assert Path(work, "input.scs").read_text() == "x"
    ex.get(work, tmp_path / "back")
    assert (tmp_path / "back" / "input.scs").read_text() == "x"

    with pytest.raises(CommandTimeout):
        ex.run("sleep 5", timeout_s=1)


def test_local_executor_refuses_commands_on_windows(tmp_path, monkeypatch):
    """Windows has neither /bin/sh nor csh: a command there is an executor error that says what to do (the doctor reports
    it) instead of a FileNotFoundError from subprocess; the file operations need no shell."""
    monkeypatch.setattr(local_executor, "_WINDOWS", True)
    ex = LocalExecutor(tmp_path / "scratch")
    with pytest.raises(ExecutorError, match="--ssh-profile"):
        ex.run("true")
    assert ex.exists(ex.scratch("obs_0001"))


def test_output_is_utf8_whatever_the_controller_locale(tmp_path):
    """The simulation host writes UTF-8: neither executor decodes it with the controller's locale (cp1252 or cp936 on a
    Windows controller), and a stray byte becomes U+FFFD instead of an exception."""
    remote = "import sys; sys.stdout.buffer.write('µ ✓ 中'.encode() + bytes([255]))"

    def ssh(argv, **kwargs):                            # stands in for ssh: the remote bytes, decoded as run() asks
        return subprocess.run([sys.executable, "-c", remote], check=False, **kwargs)

    assert SshExecutor("lab", "/tmp/icopt", execute=ssh).run("anything").stdout == "µ ✓ 中\ufffd"
    assert LocalExecutor(tmp_path).run("printf 'µ ✓ 中\\377'").stdout == "µ ✓ 中\ufffd"


# -- ssh ----------------------------------------------------------------------

class FakeSsh:
    """Records argv and answers with scripted exit codes."""

    def __init__(self, codes: dict[str, int] | None = None):
        self.calls: list[list[str]] = []
        self.codes = codes or {}

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        tail = argv[-1]
        code = next((c for needle, c in self.codes.items() if needle in tail), 0)
        return subprocess.CompletedProcess(argv, code, stdout="out", stderr="boom" if code else "")


def test_ssh_run_builds_batchmode_argv_and_wraps_cshrc():
    fake = FakeSsh()
    ex = SshExecutor("lab", "/tmp/icopt", execute=fake)
    result = ex.run("spectre input.scs", cwd="/r/obs", cshrc="/r/env.csh", timeout_s=7)
    assert result.ok and result.stdout == "out"
    argv = fake.calls[0]
    assert argv[:4] == ["ssh", "-o", "BatchMode=yes", "lab"]
    assert argv[4] == "exec csh -fc 'source /r/env.csh; cd /r/obs; spectre input.scs'"


def test_ssh_run_sources_an_sh_environment_file_in_sh():
    fake = FakeSsh()
    SshExecutor("lab", "/tmp/icopt", execute=fake).run("spectre input.scs", cwd="/r/obs", cshrc="/r/cadence.sh")
    assert fake.calls[0][4] == "exec /bin/sh -c '. /r/cadence.sh; cd /r/obs && spectre input.scs'"


def test_ssh_exit_code_contract():
    ex = SshExecutor("lab", "/tmp/icopt", execute=FakeSsh({"exit255": 255}))
    with pytest.raises(TransportError):
        ex.run("exit255")

    ex = SshExecutor("lab", "/tmp/icopt", execute=FakeSsh({"mkdir": 127}))
    with pytest.raises(CommandUnavailable):
        ex.scratch("k")

    probe = SshExecutor("lab", "/tmp/icopt", execute=FakeSsh({"test -e /gone": 1, "test -e /weird": 2}))
    assert probe.exists("/here") is True
    assert probe.exists("/gone") is False
    with pytest.raises(ExecutorError):
        probe.exists("/weird")          # never "missing" on an ambiguous exit code


def test_ssh_put_uses_temp_name_then_mv(tmp_path):
    fake = FakeSsh()
    ex = SshExecutor("lab", "/tmp/icopt", execute=fake)
    local = tmp_path / "input.scs"
    local.write_text("x")
    ex.put(local, "/r/obs/input.scs")
    scp, mv = fake.calls
    assert scp[0] == "scp" and scp[-1].startswith("lab:/r/obs/.input.scs.upload-")
    assert "mv -f -- /r/obs/.input.scs.upload-" in mv[-1] and mv[-1].endswith("/r/obs/input.scs'")


def test_ssh_failed_put_cleans_up_temp(tmp_path):
    fake = FakeSsh({"scp": 1})
    fake.codes = {"upload-": 1}          # scp (argv[-1] is the remote temp) fails; rm must follow
    ex = SshExecutor("lab", "/tmp/icopt", execute=fake)
    local = tmp_path / "input.scs"
    local.write_text("x")
    with pytest.raises(ExecutorError):
        ex.put(local, "/r/obs/input.scs")
    assert any("rm -f --" in call[-1] for call in fake.calls)


def test_ssh_scratch_resolves_tilde_once_even_from_parallel_jobs():
    class HomeSsh(FakeSsh):
        def __call__(self, argv, **kwargs):
            self.calls.append(list(argv))
            return subprocess.CompletedProcess(argv, 0, stdout="/home/lab" if "$HOME" in argv[-1] else "", stderr="")

    fake = HomeSsh()
    ex = SshExecutor("lab", "~/.ic-opt/scratch/p", execute=fake)
    with ThreadPoolExecutor(max_workers=4) as pool:
        paths = list(pool.map(ex.scratch, ["obs_0001", "obs_0002", "obs_0003", "obs_0004"]))
    assert sorted(paths) == [f"/home/lab/.ic-opt/scratch/p/obs_{i:04d}" for i in range(1, 5)]
    assert sum("$HOME" in call[-1] for call in fake.calls) == 1
    assert all("~" not in call[-1] for call in fake.calls if "mkdir" in call[-1])
