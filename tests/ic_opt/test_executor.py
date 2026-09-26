import contextlib
import io
import os
import shlex
import shutil
import signal
import subprocess
import sys
import tarfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import ic_opt.executor.local as local_executor
import ic_opt.executor.ssh as ssh_module
from ic_opt.executor import (
    CommandTimeout,
    CommandUnavailable,
    ExecutorError,
    LocalExecutor,
    SshExecutor,
    TransportError,
    hook_shell,
    process_group,
    shell_program,
)
from tests.ic_opt.fakes import forwarded_signals, posix_only


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
    result = ex.run("spectre input.scs", cwd="/r/obs", cshrc="/r/env.csh")        # no timeout: the command line as it is
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


# -- directory transfers keep the names Windows would change (N-21) --------------------------------------------------------

EXPORT = {                                   # a Maestro export in miniature: every real one carries amap/__dspf_information__.
    "input.scs": b"simulator lang=spectre\nparameters F=20\n",
    ".modelFiles": b"/pdk/models.scs\n",
    "amap/__dspf_information__.": bytes(range(38)),                  # the name ends with a dot (38 bytes, as on the host)
    "amap/trailing space ": b"\x00\xff the name ends with a space\n",
}


def tree(root: Path) -> dict[str, bytes | None]:
    """Everything under ``root`` by its relative name: a file's bytes, None for a directory."""
    return {p.relative_to(root).as_posix(): None if p.is_dir() else p.read_bytes() for p in sorted(root.rglob("*"))}


@pytest.mark.skipif(os.name == "nt" or shutil.which("tar") is None, reason="this machine plays the Linux host: /bin/sh, tar")
@pytest.mark.parametrize("windows", [False, True], ids=["local-tar", "windows-tarfile"])
def test_tree_transfers_carry_names_windows_would_change_byte_for_byte(tmp_path, monkeypatch, windows):
    """A Windows controller packs and unpacks directory streams with ``tarfile`` through ``localpath.literal`` paths, never
    its own tar; any other controller runs its tar exactly as before. This machine plays the host (its /bin/sh and tar
    run the remote side) and each branch sends a tree with ``amap/__dspf_information__.``, a name ending in a space and a
    hard link (a file tar reaches twice) there and back, byte for byte, then fetches it again over what came back. Here
    those names are ordinary: that Windows keeps them is for the Windows acceptance re-run to show."""
    real_run = subprocess.run
    local_runs: list[list[str]] = []

    def controller_run(argv, *args, **kwargs):                           # what the controller itself starts
        local_runs.append(list(argv))
        return real_run(argv, *args, **kwargs)

    def host(argv, *, input=None, timeout=None, encoding=None, errors=None, capture_output=True):
        assert argv[:4] == ["ssh", "-o", "BatchMode=yes", "lab"]
        return real_run(["/bin/sh", "-c", argv[4]], input=input, capture_output=True, timeout=timeout, encoding=encoding,
                        errors=errors, check=False)

    monkeypatch.setattr(ssh_module, "_WINDOWS", windows)
    monkeypatch.setattr(ssh_module.subprocess, "run", controller_run)
    sent, back, remote = tmp_path / "netlist", tmp_path / "back", tmp_path / "host" / "obs_0001" / "netlist"
    for name, data in EXPORT.items():
        (sent / name).parent.mkdir(parents=True, exist_ok=True)
        (sent / name).write_bytes(data)
    (sent / "empty").mkdir()
    os.link(sent / "input.scs", sent / "amap" / "input.hardlink")
    ex = SshExecutor("lab", str(tmp_path / "host"), execute=host)

    ex.put(sent, str(remote))
    assert tree(remote) == tree(sent)
    ex.get(str(remote), back, dereference=True)
    assert tree(back) == tree(sent)
    (remote / "amap" / "__dspf_information__.").write_bytes(b"rewritten on the host")
    ex.get(str(remote), back)                                            # into the tree already there, as metrics/ comes back
    assert tree(back) == tree(remote)
    assert local_runs == ([] if windows else [["tar", "-C", str(sent), "-cf", "-", "."], ["tar", "-C", str(back), "-xf", "-"],
                                              ["tar", "-C", str(back), "-xf", "-"]])


@pytest.mark.parametrize("member", ["../outside", "/tmp/outside", "C:outside", "amap\\..\\..\\outside", "link"])
def test_on_windows_a_fetched_tree_refuses_members_that_would_leave_it(tmp_path, monkeypatch, member):
    """The tarfile unpacking keeps what the controller's tar enforced: a member stays inside the directory it is unpacked
    into (no absolute name, no climb above it, no Windows drive or separator inside a name), and only files and
    directories come through, so a symbolic link is refused; nothing is unpacked from such a stream."""
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        info = tarfile.TarInfo(member)
        if member == "link":
            info.type, info.linkname = tarfile.SYMTYPE, "input.scs"
            archive.addfile(info)
        else:
            info.size = 4
            archive.addfile(info, io.BytesIO(b"evil"))

    def host(argv, **kwargs):                                            # `test -d` says directory; the tar sends the stream
        if "tar -C" in argv[-1]:
            return subprocess.CompletedProcess(argv, 0, stdout=stream.getvalue(), stderr=b"")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(ssh_module, "_WINDOWS", True)
    with pytest.raises(ExecutorError, match="refusing tar member"):
        SshExecutor("lab", "/tmp/icopt", execute=host).get("/r/obs/metrics", tmp_path / "metrics")
    assert list((tmp_path / "metrics").iterdir()) == [] and not (tmp_path / "outside").exists()


# -- a timeout ends the whole job, not its first process (N-2) ------------------------------------------------------------

JOB = 'sleep 300 &\necho $! > "$1"\nwait\n'        # the job's shell starts a child that would run for minutes


def alive(pid: int) -> bool:
    """The process exists and has not ended (a zombie, left for its new parent to reap, has ended)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
    except ProcessLookupError:                                   # it went while its stat was read
        return False
    except FileNotFoundError:
        return not Path("/proc/self/stat").exists()           # gone meanwhile; or no /proc to ask (macOS): kill -0 said yes


def gone(pid: int, seconds: float = 10.0) -> bool:
    deadline = time.monotonic() + seconds
    while alive(pid):
        if time.monotonic() > deadline:
            return False
        time.sleep(0.05)
    return True


def reap_leftover(pid: int) -> None:
    with contextlib.suppress(ProcessLookupError):
        os.kill(pid, signal.SIGKILL)


def job_files(tmp_path: Path) -> tuple[Path, Path]:
    job = tmp_path / "job.sh"
    job.write_text(JOB, encoding="utf-8")
    return job, tmp_path / "child.pid"


def read_pid(path: Path, seconds: float = 10.0) -> int:
    deadline = time.monotonic() + seconds
    while not (path.exists() and path.read_text().strip()):
        assert time.monotonic() < deadline, f"{path} was never written"
        time.sleep(0.05)
    return int(path.read_text())


def running_job(pid_file: Path, seconds: float = 10.0) -> int:
    """The pid the job wrote, once process_group has it among the running commands (waited for: its Popen may return
    after the job already wrote the file)."""
    pid = read_pid(pid_file, seconds)
    deadline = time.monotonic() + seconds
    while not process_group._running:
        assert time.monotonic() < deadline, "the job never became a running command"
        time.sleep(0.01)
    return pid


@pytest.fixture
def forwarding():
    """process_group.forward_signals installed afresh for the test; the handlers it replaced are put back afterwards."""
    with forwarded_signals():
        yield


@posix_only
def test_a_timed_out_local_job_leaves_no_process_behind(tmp_path):
    """The job's shell starts a child that would run for minutes: the timeout kills the job's whole process group, so the
    child does not outlive the deadline (subprocess.run's timeout killed the shell alone, and the child ran on)."""
    job, pid_file = job_files(tmp_path)
    started = time.monotonic()
    with pytest.raises(CommandTimeout, match=r"timed out after 1s: .* \(its process group was killed\)"):
        LocalExecutor(tmp_path / "scratch").run(f"/bin/sh {job} {pid_file}", timeout_s=1)
    child = read_pid(pid_file)
    try:
        assert gone(child), f"the job's child {child} outlived the timeout"
    finally:
        reap_leftover(child)
    assert time.monotonic() - started < 10


@posix_only
def test_a_local_command_runs_as_the_leader_of_a_session_of_its_own(tmp_path):
    probe = f'exec {shlex.quote(sys.executable)} -c "import os; print(os.getpid(), os.getpgid(0), os.getsid(0))"'
    pid, pgid, sid = map(int, LocalExecutor(tmp_path).run(probe).stdout.split())
    assert pid == pgid == sid and pgid != os.getpgid(0)
    assert LocalExecutor(tmp_path).run('read line || echo "stdin is empty"').stdout == "stdin is empty\n"   # never the terminal


@posix_only
def test_an_interrupted_wait_kills_the_group_before_the_interrupt_goes_on(tmp_path):
    """Ctrl-C while this thread waits for the command: the KeyboardInterrupt comes after the whole group was killed (the
    job's background child ignores SIGINT, as a non-interactive shell's background children do)."""
    job, pid_file = job_files(tmp_path)
    ex = LocalExecutor(tmp_path / "scratch")
    main = threading.main_thread().ident

    def interrupt_once_it_runs():
        running_job(pid_file)
        signal.pthread_kill(main, signal.SIGINT)                     # what the terminal's Ctrl-C does to this thread

    threading.Thread(target=interrupt_once_it_runs, daemon=True).start()
    with pytest.raises(KeyboardInterrupt):
        ex.run(f"/bin/sh {job} {pid_file}", timeout_s=60)
    child = read_pid(pid_file)
    try:
        assert gone(child)
    finally:
        reap_leftover(child)


@posix_only
def test_ctrl_c_is_passed_on_to_a_job_that_another_thread_waits_for(tmp_path, forwarding):
    """The jobs left the terminal's process group, so its Ctrl-C no longer reaches them by itself: the forwarding handler
    sends SIGINT to every running group, then Python raises KeyboardInterrupt as before -- a parallel run's jobs end as
    they did when they shared its group."""
    pid_file = tmp_path / "leaf.pid"
    ex = LocalExecutor(tmp_path / "scratch")
    results: list = []
    worker = threading.Thread(target=lambda: results.append(
        ex.run(f"/bin/sh -c 'echo $$ > {pid_file}; exec sleep 300'", timeout_s=120)))
    worker.start()
    leaf = running_job(pid_file)
    try:
        with pytest.raises(KeyboardInterrupt):
            signal.raise_signal(signal.SIGINT)
        worker.join(10)
        assert not worker.is_alive() and results and results[0].returncode != 0
        assert gone(leaf)
    finally:
        reap_leftover(leaf)
        worker.join(10)


def test_a_forwarded_signal_reaches_the_groups_then_does_what_it_did_before(monkeypatch):
    """The forwarder counts the signal first (``interrupts``: a job whose command it kills already sees the count moved),
    then signals the groups, then calls SIGINT's handler (Python's KeyboardInterrupt); SIGHUP's default ends the process,
    so the forwarder restores the default and raises the signal again (patched here: the test process lives)."""
    calls: list = []
    before = process_group.interrupts()
    monkeypatch.setattr(process_group, "_interrupts", before)                       # put back afterwards
    monkeypatch.setattr(process_group, "signal_groups", lambda signum: calls.append(("groups", signum, process_group.interrupts())))
    process_group._forwarder(lambda signum, frame: calls.append(("previous", signum)))(signal.SIGINT, None)
    assert calls == [("groups", signal.SIGINT, before + 1), ("previous", signal.SIGINT)] and process_group.interrupts() == before + 1
    monkeypatch.setattr(process_group, "signal_groups", lambda signum: calls.append(("groups", signum)))
    calls.clear()
    monkeypatch.setattr(process_group.signal, "signal", lambda signum, handler: calls.append(("set", signum, handler)))
    monkeypatch.setattr(process_group.os, "kill", lambda pid, signum: calls.append(("kill", pid, signum)))
    process_group._forwarder(signal.SIG_DFL)(signal.SIGTERM, None)
    assert calls == [("groups", signal.SIGTERM), ("set", signal.SIGTERM, signal.SIG_DFL), ("kill", os.getpid(), signal.SIGTERM)]


@posix_only
def test_forwarding_is_installed_once_from_the_main_thread_and_never_over_an_ignored_signal(monkeypatch):
    saved = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGHUP)}
    monkeypatch.setattr(process_group, "_forwarded", set())
    try:
        signal.signal(signal.SIGHUP, signal.SIG_IGN)                  # e.g. under nohup: the commands ignore it as well
        other = threading.Thread(target=process_group.forward_signals)
        other.start()
        other.join()
        assert process_group._forwarded == set()                     # only the main thread may set a handler
        process_group.forward_signals()
        installed = signal.getsignal(signal.SIGINT)
        assert process_group._forwarded == {signal.SIGINT} and signal.getsignal(signal.SIGHUP) == signal.SIG_IGN
        process_group.forward_signals()
        assert signal.getsignal(signal.SIGINT) is installed           # once: never a forwarder around a forwarder
    finally:
        for s, handler in saved.items():
            signal.signal(s, handler)


def test_on_windows_a_command_gets_a_process_group_that_a_timeout_ends(monkeypatch):
    """Windows (the ssh client of a Windows controller): CREATE_NEW_PROCESS_GROUP instead of a session, and on the deadline
    Ctrl-Break to the group, then the first process terminated. Stubbed: this test runs anywhere."""
    calls: list = []

    class Popen:
        pid, returncode = 4242, None

        def __init__(self, argv, **kwargs):
            calls.append(("popen", kwargs))

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def communicate(self, input=None, timeout=None):
            raise subprocess.TimeoutExpired("ssh", timeout)

        def kill(self):
            calls.append(("terminate",))

    monkeypatch.setattr(process_group, "_WINDOWS", True)
    monkeypatch.setattr(process_group.subprocess, "Popen", Popen)
    monkeypatch.setattr(process_group.os, "kill", lambda pid, event: calls.append(("event", pid, event)))
    monkeypatch.setattr(process_group.os, "killpg", lambda *args: pytest.fail("Windows has no killpg"), raising=False)
    with pytest.raises(subprocess.TimeoutExpired):
        process_group.run(["ssh", "lab", "true"], timeout=5, encoding="utf-8")
    popen = calls[0][1]
    assert popen["creationflags"] == 0x200 and "start_new_session" not in popen and popen["stdin"] == subprocess.DEVNULL
    assert calls[1:] == [("event", 4242, process_group._CTRL_BREAK), ("terminate",)]


def test_the_ssh_client_reads_its_input_or_nothing_never_the_terminal():
    """ssh forwards its standard input to the remote command: without ``input`` that is nothing (it was the terminal)."""
    echo = [sys.executable, "-c", "import sys; print(repr(sys.stdin.read()))"]
    assert ssh_module._default_execute(echo, encoding="utf-8", capture_output=True).stdout == "''\n"
    assert ssh_module._default_execute(echo, input="tar", encoding="utf-8").stdout == "'tar'\n"
    assert ssh_module._default_execute(echo, input=b"tar").stdout.strip() == b"'tar'"        # bytes in, bytes out (tar streams)


def test_a_timed_ssh_command_leads_a_session_of_its_own_and_records_its_group(monkeypatch):
    """With a timeout the remote command runs under setsid as the leader of a session of its own, once it has written its
    process-group id into its working directory (the scratch root without one, created first); the wrapper passes the
    command's output and status on and deletes the record. Without a timeout nothing is wrapped."""
    monkeypatch.setattr(ssh_module.uuid, "uuid4", lambda: uuid.UUID(int=0xABC))
    fake = FakeSsh()
    ex = SshExecutor("lab", "/tmp/icopt", execute=fake)
    ex.run("spectre input.scs", cwd="/r/obs", cshrc="/r/env.csh", timeout_s=7)
    record = "/r/obs/.ic-opt-pgid-000000000000"
    exec_, sh, c, script = shlex.split(fake.calls[0][4])
    assert (exec_, sh, c) == ("exec", "/bin/sh", "-c")
    assert script == ssh_module.in_own_session("csh -fc 'source /r/env.csh; cd /r/obs; spectre input.scs'", record)
    assert script.startswith("s=; command -v setsid >/dev/null 2>&1 && s=setsid; $s /bin/sh -c ")
    assert f"echo $$ > {record}" in script and "exec csh -fc" in script
    assert script.endswith(f"; status=$?; rm -f {record}; exit $status")

    ex.run("spectre -V", timeout_s=300)
    script = shlex.split(fake.calls[1][4])[3]
    assert script.startswith("mkdir -p /tmp/icopt 2>/dev/null; ") and "echo $$ > /tmp/icopt/.ic-opt-pgid-000000000000" in script
    ex.run("rm -rf /r/obs/psf")
    assert fake.calls[2][4] == "exec /bin/sh -c 'rm -rf /r/obs/psf'"


def test_a_timed_out_ssh_command_ends_its_remote_group_with_one_more_ssh(monkeypatch):
    """The deadline kills the local client; one more ssh then reads the recorded group and sends it SIGTERM (SIGKILL after
    a grace), and the CommandTimeout says what happened -- or why the remote group could not be reached."""
    monkeypatch.setattr(ssh_module.uuid, "uuid4", lambda: uuid.UUID(int=0xABC))
    record = "/r/obs_0001/em/ind/.ic-opt-pgid-000000000000"

    def host(cleanup: subprocess.CompletedProcess):
        calls: list = []

        def execute(argv, **kwargs):
            calls.append((list(argv), kwargs.get("timeout")))
            if len(calls) == 1:
                raise subprocess.TimeoutExpired(argv, kwargs["timeout"])        # the job: its ssh client is killed
            return cleanup
        return calls, execute

    calls, execute = host(subprocess.CompletedProcess([], 0, stdout="process group 4242 ended on SIGTERM\n", stderr=""))
    with pytest.raises(CommandTimeout) as timed_out:
        SshExecutor("lab", "/tmp/icopt", execute=execute, transfer_timeout_s=30).run("emx ind.gds", cwd="/r/obs_0001/em/ind", timeout_s=600)
    assert str(timed_out.value) == "timed out after 600s: emx ind.gds; on lab: process group 4242 ended on SIGTERM"
    (_job, job_timeout), (cleanup, cleanup_timeout) = calls
    assert (job_timeout, cleanup_timeout) == (600, 30) and cleanup[:4] == ["ssh", "-o", "BatchMode=yes", "lab"]
    assert shlex.split(cleanup[4]) == ["exec", "/bin/sh", "-c", ssh_module.end_group_script(record)]
    assert f"pgid=$(cat {record} 2>/dev/null); rm -f {record}; " in cleanup[4] and 'kill -TERM -- "-$pgid"' in cleanup[4]

    calls, execute = host(subprocess.CompletedProcess([], 255, stdout="", stderr="ssh: connect to host lab: Connection refused"))
    with pytest.raises(CommandTimeout) as timed_out:
        SshExecutor("lab", "/tmp/icopt", execute=execute).run("emx ind.gds", cwd="/r/obs_0001/em/ind", timeout_s=600)
    assert str(timed_out.value) == ('timed out after 600s: emx ind.gds; its process group on lab was not ended: SSH transport failed '
                                    f'for profile "lab" (end the group recorded in {record}): ssh: connect to host lab: Connection refused')
    assert len(calls) == 2                                                         # one cleanup attempt, never a retry loop


def test_a_ctrl_c_that_reaches_the_ssh_client_ends_its_remote_group_too(monkeypatch):
    """N-11: a Ctrl-C forwarded to the ssh client (the interrupt count moves while it runs) makes ssh exit 255, "Killed by
    signal 2." -- while the remote command, the leader of a session of its own, would run on with no deadline at all. One
    more ssh ends its group as after a timeout, and the error says so; a client killed outright (a negative status) gets
    the same. A transport failure without an interrupt is left as it was: no cleanup ssh."""
    monkeypatch.setattr(ssh_module.uuid, "uuid4", lambda: uuid.UUID(int=0xABC))
    monkeypatch.setattr(process_group, "_interrupts", process_group.interrupts())     # put back afterwards
    record = "/r/obs_0001/em/ind/.ic-opt-pgid-000000000000"
    ended = "on lab: process group 4242 ended on SIGTERM"

    def host(client: subprocess.CompletedProcess, *, interrupted: bool):
        calls: list = []

        def execute(argv, **kwargs):
            calls.append(list(argv))
            if len(calls) > 1:                                                     # the cleanup ssh
                return subprocess.CompletedProcess(argv, 0, stdout="process group 4242 ended on SIGTERM\n", stderr="")
            if interrupted:
                process_group._interrupts += 1                                     # forward_signals counted a Ctrl-C
            return client
        return calls, SshExecutor("lab", "/tmp/icopt", execute=execute)

    calls, ex = host(subprocess.CompletedProcess([], 255, stdout="", stderr="Killed by signal 2."), interrupted=True)
    with pytest.raises(TransportError) as err:
        ex.run("emx ind.gds", cwd="/r/obs_0001/em/ind", timeout_s=600)
    assert str(err.value) == f'SSH transport failed for profile "lab" (emx ind.gds): Killed by signal 2.; interrupted: {ended}'
    assert len(calls) == 2 and shlex.split(calls[1][4]) == ["exec", "/bin/sh", "-c", ssh_module.end_group_script(record)]

    calls, ex = host(subprocess.CompletedProcess([], -2, stdout="", stderr=""), interrupted=True)
    killed = ex.run("emx ind.gds", cwd="/r/obs_0001/em/ind", timeout_s=600)
    assert (killed.returncode, killed.stderr, len(calls)) == (-2, f"interrupted: {ended}", 2)

    calls, ex = host(subprocess.CompletedProcess([], 255, stdout="", stderr="Connection reset by peer"), interrupted=False)
    with pytest.raises(TransportError, match="Connection reset by peer$"):
        ex.run("emx ind.gds", cwd="/r/obs_0001/em/ind", timeout_s=600)
    assert len(calls) == 1


@pytest.mark.skipif(shutil.which("setsid") is None or os.name == "nt", reason="needs setsid (util-linux), as a Linux host has")
@pytest.mark.parametrize("login_shell", ["sh", "csh"])
def test_the_remote_wrapper_and_the_cleanup_end_a_real_job_tree(tmp_path, login_shell):
    """This machine stands in for the host: sshd runs the remote command line with the user's login shell (sh or csh), and
    killing the ssh client leaves the remote side running -- as subprocess.run's timeout, which kills that shell alone.
    The wrapper's own session and the cleanup ssh then end the job's whole tree, and the record goes with it."""
    shell = shutil.which(login_shell)
    if shell is None:
        pytest.skip(f"{login_shell} is not installed here")
    job, pid_file = job_files(tmp_path)
    work = tmp_path / "work"
    work.mkdir()

    def execute(argv, *, input=None, timeout=None, encoding=None, errors=None, capture_output=True):
        assert argv[:4] == ["ssh", "-o", "BatchMode=yes", "lab"]
        login = [shell, "-f", "-c"] if login_shell == "csh" else [shell, "-c"]     # -f: not this user's own ~/.cshrc
        return subprocess.run([*login, argv[4]], input=input, capture_output=True, timeout=timeout, encoding=encoding,
                              errors=errors, check=False)

    ex = SshExecutor("lab", str(tmp_path / "scratch"), execute=execute, transfer_timeout_s=30)
    done = ex.run("echo hi; exit 3", cwd=str(work), timeout_s=30)                   # output and status come through the wrapper
    assert (done.returncode, done.stdout) == (3, "hi\n") and not list(work.glob(".ic-opt-pgid-*"))
    with pytest.raises(CommandTimeout, match=r"; on lab: process group \d+ (ended on SIGTERM|killed \(SIGTERM, then SIGKILL\))"):
        ex.run(f"/bin/sh {job} {pid_file}", cwd=str(work), timeout_s=2)
    child = read_pid(pid_file)
    try:
        assert gone(child), f"the job's child {child} outlived the timeout on the host"
    finally:
        reap_leftover(child)
    assert not list(work.glob(".ic-opt-pgid-*"))
