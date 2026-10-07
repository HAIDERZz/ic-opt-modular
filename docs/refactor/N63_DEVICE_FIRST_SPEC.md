# N-63 — the device first: a point whose EM device fails a constraint runs no circuit simulation

Status: specification (2026-10-07), for the coding subagent; step 3 of the evaluation schedule
(`T17_8_SCHEDULE_SPEC.md` section 3 left it: "Device children keep their present place after the testbench children;
scheduling them is step 3"). Decision behind it: the user's polish batch of 2026-10-07 (`BACKLOG_CN.md` 0.13, item 1).
Data behind it: in the N-51 joint run (pcell + EMX, 3 testbenches × 3 corners) 22 of 80 points failed the device
constraint `SRF_p` the moment EMX had run, and each still ran its 9 Spectre simulations -- 198 of 861 simulations spent
on points known to be infeasible.

Read before writing: `src/ic_opt/eval/schedule.py` (whole file), `src/ic_opt/eval/engine.py` (`Child`,
`children_of`, `counted_children`, `_run_point`, `evaluate_job`'s simulation count), `src/ic_opt/blocks/evaluate.py`
(`stop_wanted`, `STOP_FROM_SIMULATIONS`, `_schedule`, `plan_shape`, the batch line), `src/ic_opt/sim/corner.py`
(`aggregate` with `wanted`), `src/ic_opt/objective.py` (`evaluate_partial`, `constraint_violations`),
`src/ic_opt/stages/em_chain.py` (which stage is the device child's measurement and where the EMX run is: point level),
`src/ic_opt/recipes/signoff.py` (`full=`), `docs/refactor/T17_8_SCHEDULE_SPEC.md` (all of it, sections 4, 6 and 14 in
particular), `T17_9_MULTI_CORNER_SPEC.md` (the `None` default and `STOP_FROM_SIMULATIONS`), `tests/ic_opt/test_schedule.py`,
`tests/ic_opt/test_replay_parity.py`, `tests/ic_opt/test_em_replay.py`, `tests/ic_opt/test_em_circuit.py`.

## 1. What changes

### 1.1 The device children run first

Today a point's children run testbenches first and the EM device children (`unit_kind == "device"`, the measurement of
the sNp the point-level EMX stage produced) last; a library device's child (T18.2B) already runs first. After this
ticket every device child runs first -- library devices as today, then the other devices in the spec's order -- and the
testbench children after them, in the learned order when the schedule learns one and in the spec's order otherwise.
A device child's measurement costs no simulation (the EMX run is the point-level stage, already done when the children
start), so moving it changes nothing of what a point costs; it only makes the device's metrics known before the first
Spectre run.

### 1.2 A device child's failure stops the point, whatever the count rule says

`Schedule.stop_after` already names a device child's failure (its `failed:<stage>` / `metric_failed` status, or a
violated constraint whose metric the child produced -- `SRF_p > ...`, `Qp_peak > ...`, `Lp_lf` within a window ...).
Today it is only consulted when the schedule is on (`stop_wanted`: the spec's switch, else at least
`STOP_FROM_SIMULATIONS` testbench simulations per point). After this ticket:

- a **device** child's failure stops the point whenever `simulator.stop_at_first_failure` is not explicitly `false`
  (and no recipe override says `False`): the point's infeasibility is known before any simulation ran, and the metrics
  the point would have given the models come from simulations of a device that cannot be used -- the measured
  benefit of running them (T17.9 revision 2) was for testbench stops, where a point stopped early loses metrics of
  simulations it would otherwise have run for a device that is fine;
- a **testbench** child's failure stops the point under today's rule, unchanged (`stop_wanted`: explicit switch, else
  the count rule);
- explicit `stop_at_first_failure: false` (or the recipe override `False`, `signoff full=true`) runs every child of
  every point as today, device included.

So the schedule has two independent stop kinds. Implement it as the schedule carrying which child kinds may stop a
point (`stop_kinds: frozenset[str]`): `{"device"}` from the device rule, plus `{"testbench"}` when `stop_wanted` says
so; `stop_after` returns None for a child of a kind not in it. `sim.evaluate` builds a schedule whenever at least one
kind may stop (the learned testbench order only when the testbench kind may stop; otherwise `spec_order`, so a run
whose testbenches are not scheduled keeps today's order exactly). With no device child in the pipeline and the count
rule off, nothing is built: a circuit-only run below 20 simulations per point is byte for byte what it is today.

### 1.3 The record of a device-stopped point

Exactly T17.8 section 6 and revision 1: the children that ran (the device children, in the engine's order),
`status` from the failing device child (`constraint_failed` with the device's penalty; `failed:<stage>` or
`metric_failed` for a measurement that failed), `feasible` false, `not_run` = every testbench child (in the engine's
child order), `simulations` = the point-level cache misses (the EMX runs) + 0, the issues line
`"not simulated: <n> of <m> children (stopped after <device>/nominal)"`. The device's metrics stay in `metrics`, so
`metric_gp` trains its device-metric models on them (revision 1 item 5) and the summary shows what the device gave.
Nothing new in the observation's shape.

### 1.4 What the user sees

- `plan_shape`: when device children exist and the device kind may stop, the per-point line ends with
  `(the device measured first: a point whose device fails a constraint stops before any testbench simulation)`; when
  the testbench kind may stop too, today's text follows it.
- The batch line after `sim.evaluate` (T17.8 section 7) counts both: `<k> of <n> points stopped early (<d> at the
  device), <s> simulations not run`, only when `k > 0`.
- `digest` and `analyze`: a device-stopped point is a stopped point (`not_run`); add to the digest's counts
  `stopped_at_device: <points>`. `skills/ic-opt/SKILL.md`: one sentence under Read and one under Run;
  `skills/author-spec/SKILL.md`: one line (a device constraint written as a constraint is checked before any
  circuit simulation; `stop_at_first_failure: false` runs everything).

## 2. What does not change

The proposers and the strategies; `aggregate`, `evaluate_partial` and the ranking of stopped points (T17.8 revision 1
covers device-stopped points: fewest `not_run` first, and a device-stopped point has the most); the budget's ceiling per
point; the count rule for testbench stops and `STOP_FROM_SIMULATIONS`; the spec (no new field; the fingerprint
unchanged); the recorded-run replays (`test_replay_parity.py`, `test_em_replay.py` keep the switch off and replay full
evaluations -- they must pass unchanged); the library-row pipeline, whose device child already runs first and whose
rows are usually pre-filtered by the index (a row's device constraint can still fail, and then it stops the point the
same way).

## 3. Tests (`tests/ic_opt/test_schedule.py`, and `tests/ic_opt/test_em_circuit.py` for the EM chain; fakes only)

1. Order: with EM devices and testbenches, `spec_order` and `order` put the device children first (library devices
   before the others, then the spec's device order), testbenches after; a history that reorders testbenches never
   moves a device child behind one.
2. Stop kinds: with the count rule off (a two-testbench, one-corner spec) a device child whose constraint is violated
   stops the point; a testbench child's violation does not; with the count rule on both do; with explicit `false`
   neither; the recipe override `False` wins over everything.
3. Engine with a fake EM pipeline (point-level fake EMX stage + device measurement child + two testbench children):
   a point whose device violates its constraint has only its device child, `constraint_failed`, `feasible` False,
   `not_run` = the two testbench keys, `simulations` = the point-level runs, the issues line; a point whose device
   passes runs every testbench child; the batch log line carries the device count.
4. `signoff full=true` runs the device-stopped point's testbenches; without `full` it does not.
5. Byte for byte: a circuit-only run below the count rule builds no schedule and records what it recorded before
   (an existing observation line compared field by field); the replay tests pass unchanged.
6. Digest and report on a store with device-stopped points: no exception; `stopped_at_device` counts them.

`ruff check src tests` clean.

## 4. Acceptance (by the coordinator, not the coder)

1. The tests of section 3 and the existing suites.
2. Replay of the N-51 store (`ic-opt-accept/n15/scratch/mixer_cs_xfmr_joint_opt`, read-only): with the device first,
   the 22 points whose `SRF_p` failed would have stopped before their 198 simulations; no feasible point is among them
   (they are infeasible by their own device metrics, so this holds by construction -- the check is that the rule
   identifies exactly those 22 and no other).
3. On the mixer library platform (`ic-opt-accept/t19_5_tap/l3_mixer_v2`, Spectre, no EMX, the established envelope of
   9 jobs × 11 threads): the same run with a deliberately tight device constraint (so that some library rows fail it)
   with the device rule on and with `stop_at_first_failure: false`, same seed and budget: the simulations spent, the
   points stopped, and the best feasible objective of both.

## 5. Working rules for the coder

Branch `n63-device-first` in the worktree `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/worktrees/n63-device-first`
(created from main; `git merge --ff-only main` first). Python
`/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-modular/.venv/bin/python` with
`PYTHONPATH=/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/worktrees/n63-device-first/src` for every run; never `uv run`,
`uv sync` or pip. Run `tests/ic_opt/test_schedule.py`, `test_em_circuit.py`, `test_em_replay.py`, `test_replay_parity.py`,
`test_engine*.py`, `test_corner*.py`, `test_digest*.py`, `test_report*.py`, the recipes' tests, and `ruff check src tests`;
all clean before committing. No real simulator, nothing under `ic-opt-library` or `ic-opt-accept` touched, nothing
private in code, tests or docs. Do not edit `docs/refactor/BACKLOG_CN.md` (the coordinator records the status). Commit
in the repository's style, trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`; append a `## 6. Record`
section to this file (what was done, commits, test counts, deviations). Do not merge or push.

## 6. Record (2026-10-07)

Implemented by the coding subagent on `n63-device-first`: `dfb269e` (feature, tests, docs), this record in the commit
after it; not merged, not pushed.

What was done:

- `eval/schedule.py`: `Schedule(..., stop_kinds=)` and `Schedule.from_history(..., stop_kinds=)`, default both kinds
  (what a schedule meant before); `stop_after` says None for a child of a kind not in `stop_kinds`; the kind-agnostic
  test is `Schedule.failure` (the history's failure counts use it); `kind_of` reads a child's kind from its unit.
  `spec_order`: the device children first -- library devices, then the others, each in the spec's device order -- then
  the testbenches as before; `order`: those devices, then the testbenches by score. The order is learned only when the
  testbench kind may stop (`from_history` returns the spec's order otherwise and reads no rows).
- `eval/engine.py` (`_run_point`): a stop found at a device child takes effect at the first testbench child after it;
  the other device children still run. A point whose children are all devices is never cut short; the stop reason is
  None when every child ran.
- `blocks/evaluate.py`: `stop_kinds(spec, children, override) -> frozenset[str]`: `testbench` from `stop_wanted`
  (unchanged), `device` when the children hold a device child and a testbench child and neither the override nor,
  without one, the spec's switch is False. `sim.evaluate` builds a schedule when the set is non-empty; `_schedule`
  reads the store only when the testbench kind is in it. `plan_shape` and the batch line as in 1.4.
- `sim/corner.py`: `stopped_at_device(spec, o)`: the child `stopper` names is one of the spec's devices.
- `digest.py`: `counts.stopped_at_device`; `digest.md`'s head reads `N stopped early (D at the device; S simulations
  not run)` when D > 0, as before otherwise.
- Documents: `skills/ic-opt/SKILL.md` (a sentence under Run, one under Read, and the library-device paragraph, which
  said "with the stop on the device's child runs first"), `skills/author-spec/SKILL.md` (one line under the
  `stop_at_first_failure` field), `docs/em/library.md` (the same library-device sentence), the `signoff` docstring and
  the comment of `Simulator.stop_at_first_failure`.

Tests (fakes only; Python of the main tree's venv with `PYTHONPATH=<worktree>/src`), before (bf25119) -> after:

| suite | before | after |
| --- | --- | --- |
| `test_schedule.py` | 20 passed | 29 passed (9 new) |
| `test_em_circuit.py` | 9 passed | 10 passed (1 new) |
| `test_engine.py` | 19 passed | 19 passed |
| `test_digest.py` | 24 passed | 24 passed (the exact counts dict gains `stopped_at_device: 0`) |
| `test_digest_leak.py` | 3 passed | 3 passed |
| `test_digest_library.py` | 2 passed | 2 passed |
| `test_cli_recipes.py` | 40 passed | 40 passed |
| `test_signoff_tighten.py` | 24 passed | 24 passed |
| `test_library_device.py` | 9 passed | 9 passed (the plan line updated) |
| `test_library_device_run.py` | 6 passed | 6 passed (the plan and batch lines updated) |
| `test_replay_parity.py` + `test_em_replay.py`, without their variables | 3 skipped | 3 skipped |
| the same with `IC_OPT_RECORDED_RUNS`, `IC_OPT_EM_RECORDED_RUNS`, `IC_OPT_PROFILE_DIRS` set (recordings outside the repository, read only) | 6 passed | 6 passed, unchanged files |
| `tests/ic_opt` whole, once | -- | 1358 passed, 647 skipped |

No `test_corner*.py`, `test_report*.py` or `test_recipe*.py` exists. Also run before and after, equal: `test_multi_corner`
15, `test_em_engine` 18, `test_em_measure` 15 + 1 skipped, `test_library_stage` 5, `test_library_signoff` 3,
`test_site` 47, `test_threads` 14, `test_optimize` 33, `test_metric_gp` 36, `test_skill_author_spec` 8,
`test_library_docs` 4, `test_lib_tap` 13, `test_blocks` 11 + 1 skipped, `test_advice` 32. `ruff check src tests`: clean.
The 9 new tests of `test_schedule.py` but the golden one fail on bf25119's source (checked); the golden one, the
circuit-only line recorded at bf25119, passes on both.

Deviations from the specification, and why:

1. `simulations` of a device-stopped EM point = its point-level cache misses + the device measurements that ran (1 per
   EM device), not "+ 0". The EM pipeline's `Measure` declares no `simulates = False`, so the engine has always counted
   an EM device's measurement as a simulation: in the budget's ceiling per point, in every record, in the plan line (the
   N-51 store: 861 = 80 × 9 Spectre + 80 measurements + 61 EMX runs). Section 2 keeps the budget, and changing the count
   would change every EM record. A library row's measurement (`simulates=False`) counts 0, as before.
2. All device children run before a device's stop takes effect, so a device-stopped point holds every device child and
   `not_run` is every testbench child (1.3) also with several devices; they cost nothing and their metrics reach the
   models. Under a stop that was already on, a spec with two devices now measures the second where it was cut off.
3. The device kind needs a testbench child among the children: an em_only / library_only run (no testbench) builds no
   schedule by default and records as before; under an explicit `true` its later devices are no longer cut off by a
   failing first one (before N-63 they were).
4. The plan line joins the two texts in one parenthesis, `up to N simulations per point (the device measured first: a
   point whose device fails a constraint stops before any testbench simulation; a point stops at the first simulation
   that fails it)`, and says "up to" for the device kind alone too (the count is a ceiling).
5. The batch line's `(D at the device)` appears when the batch's points have device children; a circuit-only batch
   prints the line of T17.8 unchanged.
6. With the device kind alone the testbenches run in `spec_order`, as 1.2 says; it differs from the engine's order only
   when a corner named `nominal` is not the first of the spec's corners or a `corners=` subset is given out of spec
   order. The observation keeps the engine's order, so no record changes.
7. A child's kind is read from its unit (a `ChildResult` carries no kind): a testbench whose id equals a device's would
   be read as the device (such a spec without corners already gives both children one key).
8. `DIGEST_VERSION` stays 4 (T17.9's `stopped_at` did not bump it either).

What the specification did not foresee:

- `signoff`'s re-check never meets a device-stopped point: it re-checks the feasible points of its search, a device's
  metrics are the same at every corner (one sNp, cached), and a later round searches under tighter limits. `full=` thus
  changes nothing for device stops in practice; test 4 goes through `signoff._recheck_stop` and `sim.evaluate`.
- OpenBox, which `strategy=auto` takes for EM devices, gets a device-stopped point as a failed trial (T17.8 revision 1,
  item 4): its objective and every constraint, the SRF too, are filled with the successful trials' column maxima instead
  of the point's own SRF residual, and such points do not count toward `surrogate_minimum`, so the space-filling design
  may run longer when many points stop at the device. In N-51's terms 22 of 80 points would change from
  constraint-failed trials with residuals to failed trials; TuRBO likewise takes them as failed trials. `metric_gp`
  (library devices) keeps training its device-metric models on them (revision 1, item 5). Acceptance 4.3 measures the
  `metric_gp` side; the OpenBox side on an EM run is not measured.
- Tests that pinned texts the rule now changes: `test_digest.py`'s counts dict, the library device plan line
  (`test_library_device.py`, `test_library_device_run.py`) and batch line (`test_library_device_run.py`); the
  monkeypatched `Schedule.from_history` in `test_schedule.py` forwards the new keyword.
