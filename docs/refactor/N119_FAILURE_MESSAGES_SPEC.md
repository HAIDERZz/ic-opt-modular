# N-119: what a run says when a simulation fails, when it is interrupted and when the project is locked (2026-10-14)

Found by N-118's real-machine checks (`/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-accept/n118_failure_paths/RESULT_CN.md`,
MixerCS CG/NF testbench, tt). The behaviours were right; three messages were not. User decision 2026-10-14: do the first three of
N-119's four points (the fourth -- a strong-inversion metric -- is not done).

## 1. Spectre's own error in the point's record

Today a Spectre run that fails reads, in the child's issues, `spectre exited 2` and `spectre terminated prematurely due to fatal
error.`; the cause is only in `psf/spectre.out`: `ERROR: "input.scs" 12: ... Spectre cannot find the 'section Post_simu_top_no_
such_section' ...` (N-118 check t4: a corner whose model section does not exist). Change: when Spectre exits non-zero, add the first
`ERROR` entry of its log -- the line that starts it and its continuation lines, collapsed to one line, at most about 300
characters -- to the child's issues, before the existing lines. No ERROR found: as today. A timeout (`timed out after ...`) is
unchanged. OCEAN failures: the same if its log has an equivalent error line; say in the record what you found.

## 2. One line when a run is interrupted

Today Ctrl-C ends a run with exit code 130 and prints nothing; `steps.jsonl` records `"status": "interrupted"` with the recorded,
interrupted and not-started points (N-118 check t2). Change: before exiting, print one line on stderr, e.g. `interrupted: 2 points
recorded, 2 stopped, 2 not started; the same command continues the run` (the counts from the step record), for every recipe that
can be interrupted this way. Exit code stays 130; nothing else changes (records, cleanup, resume).

## 3. The project lock taken first, refused in one line

Today a second run on a locked project does `doctor` and the netlist import -- which writes the decks into the store -- before the
optimize block takes the lock and fails, and the user sees a ~70-line traceback ending in `LockHeld: project is locked by another
run: <path>/.icopt/run.lock` (N-118 check t3). Change: a recipe run that will take the project lock takes it before anything writes
to the store (before doctor and the import), holds it to the end, and the blocks that take it today do not deadlock against it
(re-entrant in the same process, or the block skips taking it when the run holds it). A held lock is refused like the other
refusals of `ic-opt run`: one line `error: project is locked by another run: <path> (pid <n> when the lock file says)` and exit code
2, no traceback, nothing written. `--plan` does not take the lock and is not refused (it writes nothing). Commands that read the
store without the lock (`digest`, `call` on the library) are unchanged.

## 4. Tests

The repository's fakes: a fake Spectre that exits 2 with an `ERROR` block in its log (its first error in the issues; none: as
today); an interrupt in the engine's tests (the line, its counts, exit code 130); two runs on one project (the second refused in one
line with code 2 before any file is written under `.icopt/decks` or `.icopt/sims`; the first unaffected; `--plan` allowed while the
lock is held).

## 5. Real check (Spectre, small)

On copies of N-118's projects under `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-accept/n119_failure_messages/` (start from
`n118_failure_paths/base/spec.yaml`; 1 thread per Spectre run, 2 runs at once; reuse `n118_failure_paths/drive.py`, which starts each
run in its own session with SIGINT at its default handling -- a run started with `&` from a non-interactive shell ignores SIGINT):
the wrong-model-section point shows Spectre's ERROR line in its issues; Ctrl-C prints the line; a second run is refused in one line
with exit code 2 and writes nothing. At most 8 threads in all; about 5 minutes of Spectre.

## 6. Documents

README (where failures, interrupts and the lock are described), the ic-opt skill's "how to stop a run and continue it" and its list of
refusals.

## 7. Working rules for the coder

Branch `n119-failure-messages` in `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/worktrees/n119-failure-messages` (from `main`; `git merge
--ff-only main` first). `PYTHONPATH=<worktree>/src` with `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-modular/.venv/bin/python`;
never `uv run` or `uv sync`; `git -C <worktree>` and absolute paths; for the real check the cshrc is
`/home/zzchen/Agent_virtuoso/cadence_ic231_env.csh` (in `~/.ic-opt/site.yaml`). Targeted tests (CLI, engine, evaluate, the Spectre
and OCEAN stages, the lock, optimize, signoff, fix_run) plus `ruff check src tests`; pytest with `OMP_NUM_THREADS=2`, no `-n`. Never
`pkill` by pattern. One commit per point (three) with tests and documents, then one for the `## Record` section below. No merge, no
push, no edit of `docs/refactor/BACKLOG_CN.md`. Messages in the repository's style, ending with
`Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Record

(to be written by the coder: base commit, per point the files and why, tests, the real check's outputs, deviations)
