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

Base: `main` at 3542196 (the branch was already there: `git merge --ff-only main` had nothing to merge). Commits:
7ce4bcf (point 1), 5938902 (point 2), 482e3d1 (point 3), then this record. Not merged, not pushed.

### 1. Spectre's own error in the point's record (7ce4bcf)

- `src/ic_opt/stages/spectre_chain.py`. `Spectre.run`, when Spectre exits non-zero, fetches `psf/spectre.out` into the
  child's directory (`executor.get`: nothing to do on the local host, one scp from an SSH host) and puts
  `spectre_error(log)` -- or `spectre_error(stdout)` when there is no log or it cannot be fetched, Spectre's output
  carrying the same messages -- in front of `spectre exited <code>` and the end of its stderr. `spectre_error`: the
  first line that starts with `ERROR` after its indentation (`ERROR (SFE-868): ...`, `ERROR: "input.scs" 12: ...`) and
  the lines after it indented deeper, up to a blank line, joined with single spaces (the log's one long line and the
  output's 80-column wrapping give the same text), cut to `ERROR_LIMIT` = 300 characters with `...` at the cut. No
  `ERROR` entry: the issues are what they were. A timeout is raised by the executor before the stage looks at a result,
  so `timed out after ...` is unchanged (the existing engine test pins its issues).
- OCEAN -- what I found. Its log (`-log metrics/ocean.log`) is Virtuoso's tagged session log: `\o` output, `\i` input,
  `\t` results, `\p` prompts, `\#` comments, `\e` the error stream. The one real OCEAN stage failure on this machine,
  the N-100 acceptance's license refusal (`ic-opt-accept/n100_accept/runs/wave_r1/new_long_1/metrics/ocean.log`, no
  scalars written), has framework messages there: `ERROR (LMF-02005): License call failed for feature
  Virtuoso_Adv_Node_Framework, ...`, `ERROR (ELI-00111) ...`, `ERROR (ELI-00133) ...`. SKILL errors read `*Error*
  <function>: ...`; they are also printed on runs that succeed, by `errset(<expression> t)` for a metric whose
  expression failed (331 OCEAN logs on this machine carry them -- n15, n51, the THz DPA work --, every one beside a
  scalars file). So `ocean_error` takes the
  first `\e` line that starts with `ERROR` or `*Error*` (with indented `\e` continuation lines, none seen), and the
  stage adds it only when OCEAN wrote no scalars (`ocean produced no scalars after N attempt(s)`); on the N-100 log it
  gives `ERROR (LMF-02005): License call failed for feature Virtuoso_Adv_Node_Framework, version 23.100 and quantity
  1. The license server search path is defined as ... No such feature exists. ...` (cut at 300). `ocean scalars
  unreadable` is left as it was: a run that wrote a scalars file may have `*Error*` lines of its metrics that are not
  why the file is malformed.
- Tests. `tests/ic_opt/fakes.py`: `fail_spectre` / `fail_ocean` may return the failed run's log text (written to
  `psf/spectre.out` and Spectre's stdout with exit 2, or to `metrics/ocean.log`). `test_spectre_chain.py`: the entry
  first and the old lines after it, the cut at 300, the wrapped form equal to the one-line form, the first of two
  entries, texts without one, no log (the entry from the output), OCEAN's license lines and a SKILL error, no OCEAN log
  and no Spectre entry (both as before). `test_engine.py`: the stored observation of a two-corner point carries
  `tb/ss: ERROR ...` before `tb/ss: spectre exited 2`. Documents: README (a failed child's issues), the ic-opt skill
  (Reading failures: `failed:spectre`, `failed:ocean`).

### 2. One line when a run is interrupted (5938902)

- `src/ic_opt/eval/engine.py`: `interrupted_line(rows)`, next to the code that writes the rows: recorded = each
  finished batch's `new` plus the interrupted row's `recorded`; stopped = the interrupted row's `interrupted`; not
  started = its `not_started`; doctor, netlist.import and report rows count nothing. `src/ic_opt/cli.py`: `run` notes
  where `steps.jsonl` ends before the recipe starts; a KeyboardInterrupt out of the recipe prints the line made from the
  rows appended since on stderr and raises `typer.Exit(130)` -- typer 0.27 already exits with 130 on its own, but older
  typer versions print `Aborted!` and exit 1, which the `typer>=0.12` pin allows. Nothing is printed under `--plan`.
  The engine, its step record, the cleanup of the process groups and the continuation are as they were. Every recipe
  gets the line, the built-ins and a recipe file alike, since the command line prints it.
- Tests (`test_engine.py`): the line from N-118's t2 row, with a finished batch before it, singular, from no rows;
  `ic-opt run fix_run` (four points one at a time, Ctrl-C while the second simulates: `1 point recorded, 1 stopped, 2
  not started`) and `ic-opt run optimize` (batches of two, Ctrl-C in the second: `2 points recorded, 1 stopped, 1 not
  started`) through the command line with a SIGINT to the main thread: exit code 130, exactly the line on stderr, no
  traceback, no command left running, and the same command afterwards continuing to four points. Documents: README
  (Ctrl-C), the skill (step 4's "to stop a run", Reading failures).

### 3. The project lock taken first, refused in one line (482e3d1)

- `src/ic_opt/_lock.py`: `exclusive_lock(..., holder=True)` -- a run's lock -- writes `\n<pid>\n` into the lock file
  while it is held (from the second byte: on Windows the holder's msvcrt lock on the first byte keeps other handles from
  reading it) and empties it on release; a refused handle reads it and adds ` (pid <n>)` (a lock taken without
  `holder`, or by an older version, names nobody: no pid). It also marks the path as held in the current context (a
  `ContextVar`); `reentrant_lock(path)` holds nothing more where that mark is set and is otherwise `exclusive_lock(...,
  holder=True)`.
- `src/ic_opt/store.py`: `RUN_LOCK = "run.lock"` (moved from `blocks/optimize.py`, still importable there) and
  `RunStore.run_lock()` = `reentrant_lock(.icopt/run.lock, what="project")`.
- `src/ic_opt/cli.py`: `run` takes `exclusive_lock(.icopt/run.lock, what="project", holder=True)` right after loading
  the spec, the site entry and the store -- before its header lines, the doctor's step row and the netlist import --
  unless `--plan`, and keeps it through the recipe, its report and its refusals; `LockHeld` there is `error: <message>`
  on stderr and `typer.Exit(2)`, like the site / envelope / library refusals. The advise command's refusal record takes
  the lock through `run_lock()`.
- `src/ic_opt/blocks/optimize.py`: `optimize` (its loop), `advise` and `revoke_advice` take the project lock through
  `run_lock()`: inside `ic-opt run` they take nothing more; called alone (`ic-opt call`, a script, the tests) they take
  it as before, now naming their pid.
- Why it cannot deadlock: no lock here waits -- `exclusive_lock` refuses at once, and `waiting_lock` is used only for
  the library's cache files. The command line takes the project lock outright, never re-entrantly (a run is never inside
  another, so even two runs in one process refuse each other); every block that takes it goes through `run_lock()`,
  which in the run's own thread and context holds nothing more, so the run never refuses itself; the store's own lock
  (`.icopt/lock`) is another file, taken per batch by the engine under the run's lock as before and never by the command
  line. Another thread or process is refused, never waited for. With the re-entrancy switched off, the two tests below
  that run blocks inside the run fail (checked), as N-118's first run would have refused itself.
- `src/ic_opt/blocks/doctor.py`: under `--plan` the doctor no longer writes its `doctor` row into `steps.jsonl` (see
  Deviations), so `--plan` writes nothing to the store and needs no lock.
- Tests. `test_lock.py`: the holder's pid in the file and empty after release (fcntl and the msvcrt fake), the refusal
  naming it, `reentrant_lock` inside it yielding False, refused in another thread and inside a lock without `holder`,
  another interpreter reading the pid. `test_cli_recipes.py` (with a `fake_cli` helper: `ic-opt run` on the fake
  Cadence host): another process holds the project (N-118's t3) -- exactly one line with its pid, exit code 2, no file
  of `.icopt` changed, nothing run on the host, `--plan` answered without writing, the same command running once the
  holder is gone; two runs in one process -- a second run and a `--plan` issued between the first one's batches, the
  second refused in one line with this process's pid, no file changed by either, the first finishing its four points;
  a recipe file that searches, adopts an advice and searches on runs through under its own lock. Documents: README
  ("One run per project"), the skill (step 4, Reading failures: the refusal and what to do).

### Tests and lint

`OMP_NUM_THREADS=2`, no `-n`, `PYTHONPATH=<worktree>/src` with the ic-opt-modular venv. The targeted set --
test_cli_recipes, test_engine, test_blocks, test_spectre_chain, test_netlist_and_ocean, test_ocean_export, test_lock,
test_optimize, test_signoff_tighten, test_signoff_total, test_advice, test_store, test_executor, test_site --
324 passed, 1 skipped (base 3542196: 310 passed, 1 skipped; 14 tests added). Wider, as a check: test_deck_identity,
test_schedule, test_em_engine, test_em_circuit, test_migrate_store, test_digest, test_multi_corner,
test_operating_points, test_saturation_margin: 184 passed. `ruff check src tests`: clean.

### Real check (2026-10-14, 17:23-17:26)

Copies of N-118's base under `ic-opt-accept/n119_failure_messages/` (`base/spec.yaml`; MixerCS CG/NF, tt, 1 thread per
Spectre run, 2 at once, peak 4 threads), driven by `drive.py` there (N-118's driver with the worktree's ic-opt: each run
in its own session with SIGINT at its default handling, the interrupt sent to the run's process group; stdout and
stderr of each run in `<tag>.out` / `<tag>.err`; `drive.log`). About two minutes on the clock, some four minutes of
Spectre jobs (two at once).

- c1 (`c1_spectre_error/`, model section `Post_simu_top_no_such_section`, budget=1): exit 0, the point
  `failed:spectre`, its child's issues `ERROR: "input.scs" 12: The file `<PDK>/models/spectre/toplevel.scs' appears
  to be in Spectre syntax, but Spectre cannot find the `section Post_simu_top_no_such_section' library defined in it.
  Ensure that the `P...` (300 characters, the real model path about 120 of them), `spectre exited 2`, `spectre
  terminated prematurely due to fatal error.`; the point's issues the same with `cg_nf/tt: `.
- c2 (`c2_interrupt_lock/`, budget=6): SIGINT to the group 39 s after the start, with 2 points recorded and 4
  simulator processes running: exit 130 at once, stderr exactly `interrupted: 2 points recorded, 2 stopped, 2 not
  started; the same command continues the run` (the step row: recorded obs_0001-2, interrupted obs_0003-4, not started
  obs_0005-6); no simulator process left.
- c3, while the same command continues the run (pid 3334555; `run.lock` holding `\n3334555\n`): a second `ic-opt run
  optimize ... budget=6` ended in 0.5 s with exit code 2, nothing on stdout, on stderr only `error: project is locked
  by another run: <dir>/c2_interrupt_lock/.icopt/run.lock (pid 3334555)`; meanwhile the only files under `.icopt` that
  changed were the continuing run's own two `spectre.out`. A `--plan` then answered with exit 0 (`2/6 points done, up to
  4 more ...`), the same two files only changing. The continuation finished with exit 0, six points ok (obs_0001-2,
  0005-8), `run.lock` empty again; `steps.jsonl` holds exactly the first run's three rows and the continuation's four
  (doctor, import, batch, report): nothing from the refused run or the plan.
- `drive.py ocean` (no simulation): the parser on the N-100 license log gives the `ERROR (LMF-02005)` line quoted
  above; on N-118's interrupted point it gives `ERROR (SPECTRE-25): The simulation is stopped either by the user or the
  farm.` (such a point is not recorded: the interrupt's failure is not its own).

### Deviations and open points

1. The interrupt line's first count is every point the run recorded, its earlier batches' too, not only the
   interrupted row's `recorded`: a run cut in its third batch would otherwise say `1 point recorded` with twenty in the
   store. The two agree for a run cut in its first batch, as N-118's t2 and c2 were; the other two counts are the row's.
2. `--plan` did write: the doctor logged a `doctor` row into `steps.jsonl` under `--plan` too (N-118's `base/.icopt`
   shows one). It no longer does, so the premise "--plan writes nothing" holds; a run and `ic-opt doctor` log the row as
   before. (A project's very first `--plan` still creates the store's empty directories, as `RunStore` always has.)
3. The digest keeps an issue text's first 200 characters (T17.10). With this PDK's model path the ERROR line's section
   name starts at character 235 of the point's issue, so `ic-opt digest` shows `cg_nf/tt: ERROR: "input.scs" 12: The
   file '...toplevel.scs' appears to be in Spectre syntax, b...` without the cause; observations.jsonl holds it (the
   report shows no issue texts). Not changed here; a candidate: the digest keeps 300 characters of a failure text, or
   the entry shortens a long quoted path.
4. OCEAN: the log's first error is added on `no scalars` only (above). Spectre: `ERROR` entries only, as specified; a
   `FATAL` entry without one would leave the issues as they were (none seen on this machine). The fetch of the log from
   an SSH host is covered by the code path and the local test only, not by a real remote run.
5. Not locked, unchanged and outside this specification: `ic-opt call` of a block that writes a project (e.g.
   `sim.evaluate` between a run's batches; `opt.optimize` takes the lock itself) and the `doctor` row `ic-opt doctor`
   writes.
6. The lock file now holds the holder's pid while it is held (empty otherwise); deleting it while a run holds it would
   let a second run lock a new file, so the README and the skill say never to delete it.
7. The test of point 2 drives the command line from `test_engine.py`, as the specification asks; it and the lock tests
   share `test_cli_recipes.fake_cli`, added in the third commit (so the second commit's version of the test was
   reworded there).
