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
