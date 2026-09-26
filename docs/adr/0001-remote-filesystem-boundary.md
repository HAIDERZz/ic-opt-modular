# 0001: Remote mode treats filesystem separation as mandatory

- **Status**: Accepted (2026-08-07); enforcement and acceptance rewritten for 0.2 (2026-09-22); platforms note (2026-09-24); the acceptance sandbox keeps the controller's site.yaml (2026-09-25)

## Context

Remote mode drives Spectre/OCEAN on a laboratory Linux host from a separate
controller machine over OpenSSH (`--ssh-profile`). Under self-SSH or a shared
NFS mount, controller code that reads a remote-owned path directly off its own
disk still produces the right bytes, because the same path string resolves to
the same file on both sides. That accident is the opposite of the topology this
tool targets (personal PC → lab server, no shared filesystem), and 0.1.8 / 0.1.9
shipped exactly such "fake remote" paths: they passed every self-SSH test and
failed on the first genuinely isolated host.

In 0.2 the project (`spec.yaml`, `.icopt/`) is owned by the controller; the
remote host owns only the Maestro exports (`maestro_point_root`), the Cadence
installation and cshrc, and the simulation scratch (`<scratch_root>/<project>/`:
that host's `scratch_root` in the controller's `~/.ic-opt/site.yaml`, a path on
the host, `~/.ic-opt/scratch` by default).

**Platforms.** The simulation host is Linux. The controller may be Linux, macOS
or Windows 10+ (the personal PC is usually Windows or macOS), so nothing on its
side of the seam may assume POSIX: it needs only Python, the OpenSSH client and
`tar`; remote paths are strings / `PurePosixPath`, never a local `Path`; the
project lock is `fcntl` or `msvcrt`, and library fits run in spawned processes.
`csh`, `sh`, `test -e`, `sha256sum` and the Cadence tools run on the host,
through `Executor.run`. `LocalExecutor` (controller and host in one) runs its
commands under `/bin/sh` or `csh` and is Linux / macOS only.

## Decision

- Every remote-owned path is touched **only** through the `Executor` seam
  (`run`, `put`, `get`, `exists`, `scratch`). Controller code never infers a
  remote path's existence or contents from a same-named local path — not as a
  check, not as a fallback, not as a convenience (the 0.1 "find the cshrc on
  the controller's disk" rule is gone: the cshrc is given explicitly and is a
  path on the simulation host).
- Transport failures **fail closed**. `SshExecutor.exists` accepts only exit
  status `0` / `1` from `test -e`; anything else (255 transport, 126/127
  unusable executable, timeouts) raises. Uploads go to a temporary name and are
  `mv`-ed into place; directory transfers are tar streams. There is no
  same-named-path fallback, ever.
- Paths in `spec.yaml` are paths on the executor's host. With
  `LocalExecutor` that is the controller; with `SshExecutor` it is the remote.

## Enforcement

- One seam: `src/ic_opt/executor/` (`base.py` protocol, `local.py`, `ssh.py`).
  Blocks and stages receive an `Executor` and never import `subprocess` or
  `Path` for remote work: `netlist.import` fetches the export with
  `executor.get(..., dereference=True)` into `.icopt/decks/.staging/`; the
  Spectre pipeline renders locally, `put`s the netlist, `run`s Spectre / OCEAN
  with `cwd` under `executor.scratch(...)`, and `get`s `metrics/` back; the
  doctor probes exports with `executor.exists`.
- The controller keeps only what came back through the seam:
  `.icopt/sims/<obs>/<tb>/<corner>/{netlist, metrics, *.stdout, *.stderr}`. Raw
  PSF stays on the host under the retention policy.

## Acceptance

Self-SSH and shared-NFS runs exercise the transport but cannot detect a
same-named-path bug. The acceptance for any change near this boundary is the
isolated run in `docs/refactor/analysis/isolated_ssh_smoke.sh`: the controller
runs inside a bubblewrap sandbox where the Maestro exports, `/opt/eda`, the
cshrc and `~/.ic-opt` are hidden (tmpfs / empty file). Of `~/.ic-opt` only the
controller's own `site.yaml` is bound back, read-only: since 0.3.0 nothing
starts without the entry of the host a command runs on (`hosts.local` for the
local doctor, `hosts.<profile>` for the remote run), while the rest of
`~/.ic-opt`, where a self-SSH host keeps its scratch, stays hidden. A local
`ic-opt doctor` must FAIL on tools and exports, and the same project run with
`--ssh-profile` must complete with results identical to a recorded run. Passed
2026-09-22 on `t8_ssh_isolated` (2 points × 3 testbenches, metrics / fom /
penalty bit-identical to the 0.1.10 recording; evidence in
`docs/refactor/EXECUTION_PLAN_CN.md` §3), before 0.3.0 made site.yaml
mandatory.

The site.yaml bind (2026-09-25) is verified only up to the site check. On the
development host (bubblewrap 0.6.3) the script was run with a profile that has
no site.yaml entry, so nothing reached SSH: inside the sandbox `~/.ic-opt` held
only `site.yaml`, readable and not writable; the local doctor read
`hosts.local` and failed on tools, license and exports; the remote step
stopped at the missing entry. Without the bind, the local doctor and the
remote step both stopped at "site.yaml not found". The full remote run with
the bind is unverified: it has not been repeated (it needs the lab host and
Spectre).

The first Windows-controller acceptance (2026-09-26) failed in `Deck.save` on
a name no Linux run can catch: every Maestro export carries
`amap/__dspf_information__.`, whose trailing dot Win32 path normalization
strips, so on Windows fetched trees, and the trees packed for the host, are now
handled through extended-length paths (`localpath.literal`; `tarfile` instead
of `tar`), which the Windows re-run has yet to confirm.

## Alternatives considered

- **Shared-path fallback** when the transport is slow or briefly unavailable:
  rejected; correctness would depend on filesystem topology and defects would
  only surface on a genuinely isolated host.
- **Self-SSH as sufficient acceptance**: rejected; it cannot distinguish "the
  transport fetched the content" from "the controller already had the bytes".
- **Degrading an ambiguous probe exit status to "missing"**: rejected; a
  transport outage would become indistinguishable from an absent file.
