# T17.10 — the run digest, version 3: what an agent outside the tool needs to advise

Status: specification (2026-09-29, evening), for the coding subagent. Decision behind it: the user's answer of
2026-09-29 23:20 to `T17_STRUCTURE_CANDIDATES_CN.md` section 3.6 ("按照推荐": every item), recorded in
`T17_OPTIMIZER_PLAN_CN.md` section 7. The digest's contract is `T17_1_5_SPEC.md` section 5 and `T17_3B_DIGEST_SPEC.md`
(version 2). This step adds to it; nothing in the numeric core changes.

What the literature says (106 papers read; `optimizer_research/papers_2024_2026/LEARNINGS_CN.md` section 2.4): an
agent helps when it is asked at a stall and answers with a region or a start point (TopoSizing: 1.1 to 3.3 calls per
run); it must see what its last advice did (LEDRO); it can use a ranking of the variables (DIVE, ASTRA: a data ranking
and a model ranking did about equally well); it must see where a failure happened (AnalogGym-Opt); free text from the
design must not leak through the digest (SABLE); its input must be checked field by field (Atelier). Our own
sessions (T17.6, eight real agent sessions): the agents decided from the "no value" boundaries and the binding
constraint, never from the suggested ranges alone.

## 1. What is added (`digest_version: 3`)

### 1.1 Stall (`progress.stall`)

- `batches_since_improvement`: batches (by `batch_key`) since the run's best feasible objective last improved; `None`
  before the first feasible point.
- `batches_since_first_feasible`: `None` before it.
- `region_restarts`: for `metric_gp` runs, how many times the region restarted (origin tags `anchor:<r>:<k>`: the
  distinct `r` values beyond the first), and `last_restart_batch`.
- `stalled`: true when `batches_since_improvement >= 3` (three batches without improvement is when TopoSizing asks;
  the number is a constant in one place, `STALL_BATCHES = 3`).
- Markdown: one line under "Progress": "no improvement for N batches (region restarted at batch K)" or "improving".

### 1.2 The last advice's result, completed (`advice[]`)

Version 2 already gives per advice its period and, for both sides of the period, points / feasible / no value / best
objective. Add per advice: `improved_best` (did the run's best feasible objective improve during the period, and by
how much in the objective's own units), `share_kept` (of the points proposed in the period, the share that came from
the advice, and the free share), and a one-word `verdict`: `helped` (the advised side's best is better than the free
side's and the run's best improved), `no_help` (neither), `mixed`. Markdown: the verdict word in the advice table.

### 1.3 Variable importance (`variables[].importance`)

Per variable and per modelled metric (constraints' and objective's), the mutual information between the variable's
unit coordinate (`metric_gp`'s `Coords`, log where the range spans a decade) and the metric's values over the rows
that have the metric — `sklearn.feature_selection.mutual_info_regression` with `n_neighbors=3`, `random_state=0` —
and `rows_used`. Also per constraint `violations` (rows violating it) and per variable a `rank` by the sum of its
mutual information over the metrics. No Gaussian process is fitted here (the digest must stay cheap: it is read while
a run goes). Markdown: a table "Variables by importance" — rank, variable, its top two metrics with their MI, the
count of rows — under "Where the good points are". Fewer than 20 rows with values: the entry is `None` and a note
says so.

### 1.4 Where failures happened (`failures.by_stage`)

Counts of points by the stage that failed them, from the observation's `status` and child statuses:
`render`, `spectre`, `ocean`, `pcell`, `emx`, `bind_nport`, `measure` (from `failed:<stage>`), `metric_failed`
(an expression gave no value), `constraint_failed`, `stopped_early` (points with `not_run`; they also count in the
status they stopped with). Markdown: one line under "Where points gave no value".

### 1.5 Refused advice, seen in the digest (`advice_refused[]`)

`ic-opt advise` refuses an advice with a message and today records nothing. Append a row to `.icopt/advice.jsonl`
with `status: "refused"`, `reason: <the message>`, `raw: <the fields as given>` (the same author/reason fields when
present), so the next digest lists it: `advice_refused`: id, when (history size), reason. Everything that reads
advice rows (`advice.in_effect`, `of_problem`, the digest's advice table, `--list`) skips refused rows where they
mean an adopted advice. Markdown: one line per refused advice under the advice table.

### 1.6 Leak test of the writer (`tests/ic_opt/test_digest_leak.py`)

The digest's markdown and JSON carry fixed fields and the texts the run produced. They must not carry: file paths of
the project or the host (`sim_dir`, netlist paths, `cshrc`), netlist text, OCEAN expressions of the metrics, the
spec's `simulator` block. Test: a spec and observations whose metric expressions, testbench paths, `sim_dir`, child
issues and operating-point tables carry canary strings (`LEAK_EXPR`, `LEAK_PATH`, `LEAK_ISSUE_FREE_TEXT`); the digest's
markdown and JSON contain the canary of an issue text only in the "issues given most often" entry (that is a run
output, kept, but truncated to 200 characters), and no other canary. Where the digest today copies something the test
catches, stop copying it.

### 1.7 Skill: tools on the agent's side

`skills/ic-opt/SKILL.md` step 7 (advise), one paragraph: what an agent may compute itself before advising — a
gm/ID lookup table of the process (from a single-transistor sweep the agent runs on its own, outside ic-opt) to turn
a target gm/ID and current into a width; the ranges a constraint implies (a power budget bounds the branch currents; a
slew-rate constraint bounds a branch current from below; a saturation margin bounds gm/ID from above); and that these
are inputs to `ranges` / `start` advice, never to the spec. Two sentences that these come from MultiForm (TCAS-I 2025)
and OTA-LUT (DATE 2025) and were not measured here.

## 2. What does not change

The numeric core; the record; the advice contract's adopted rows (a row that was adopted is byte for byte as
before; refused rows are new rows). The digest stays pure (no file, no simulator, no model fit).

## 3. Tests

`tests/ic_opt/test_digest.py` (extend) and `test_digest_leak.py` (new): each new entry on a small history built from
observations (as the existing digest tests build them): stall counts with and without a feasible point; the advice
verdicts on the three cases; importance on a synthetic history where one variable drives one metric (its MI is the
largest) and the `None` below 20 rows; `by_stage` counts; a refused advice appears in the digest and is skipped by
`in_effect`; the leak test. `test_advice.py`: the refused row's shape and that `--list` shows it as refused.

Targeted run: `tests/ic_opt/test_digest*.py test_advice.py test_optimize.py`, then `ruff check src tests`. Tests
count as machine load: run them once per commit, not in a loop.

## 4. Working rules for the coder

- Branch `t17-10-digest` in its own worktree (`git worktree add /home/zzchen/Agent_virtuoso/EDA_AI_AGENT/worktrees/t17-10-digest -b t17-10-digest main`);
  commits on that branch only; never touch `main`, never push.
- Another agent works at the same time on `t17-9-multi-corner`, which changes `sim/corner.py`, `blocks/evaluate.py`,
  `spec.py`, `suggesters/metric_gp/`, `suggesters/__init__.py`, `blocks/optimize.py`, and adds `stopped_at` to
  `digest._counts`. Keep out of those files; in `digest.py` add new functions and new entries only, and touch
  `_counts` not at all. The coordinator merges both.
- Python: `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-modular/.venv/bin/python` with `PYTHONPATH=<worktree>/src`
  (check `python -c "import ic_opt; print(ic_opt.__file__)"` shows the worktree); never `uv run`, `uv sync`, `pip`.
- No simulator runs, no network.
- One commit per coherent piece (suggested: 1.1 + 1.2 + 1.4; 1.3; 1.5; 1.6 + 1.7), each message saying what and why;
  trailer `Co-Authored-By: <the agent's own>`.
- Hand back: the branch name and commits, the test and ruff results verbatim, and what the specification left open
  and how it was read (a list).

## 5. Acceptance (by the coordinator)

Section 3 green, ruff clean; a digest of an existing development run (rebuilt from its observations, no
simulation) read by eye: every new entry present and sensible; the leak test green.

## 6. Record (2026-09-30)

Implemented by the coding subagent on `t17-10-digest` (`ef6e567`, `60e62ec`, `e613494`, `7baa4bb`), merged `ed59525`;
targeted tests 111 passed, ruff clean. How the specification's open points were read (the coder's list, kept here):

- A refused advice's row: `{"id": "r<n>", "event": "refuse", "status": "refused", "at", "since", "reason", "raw", "spec_fingerprint"}`;
  refused ids are their own series, so adopted rows are byte for byte what they were. Only refusals of `advice.check` are
  recorded (not an unreadable file, not `--revoke`, not while a run holds the project), and only by the `ic-opt advise`
  command (`opt.advise` called from code records nothing: `blocks/optimize.py` was out of this step's files).
- `stall`: batches of `progress.batches`; any strict improvement counts, the first feasible point included; before it,
  `stalled` is false and the markdown says so. `region_restarts` counts distinct `anchor:<r>:<k>` regions.
- `improved_best` compares the best before the period with the best up to its end; `share_kept` counts the advice's
  start points as its own; `verdict` is `None` while the period holds no point.
- Importance: the unit coordinate is `Coords`' formula repeated in `digest._Grid.coordinate` (pinned equal by a test),
  so the digest does not import the strategies; one `mutual_info_regression` call per metric; a one-level variable is
  `None` and unranked; scikit-learn is imported only when there are points enough.
- `violations` is JSON only and counts every point whose judged value violates, whatever its status.
- `by_stage` always lists the seven spec stages; other stages (`ngspice`, `extract`, `predict`) appear when met.
- The leak test caught issue texts copied whole: they are cut to 200 characters (counted whole). Paths inside the first
  200 characters of a simulator's message can still appear; scrubbing them is a separate decision. `cshrc` reaches no
  digest input.
- 1.7 as written said a saturation margin bounds gm/ID from above; the coder wrote "from below" (V_DSAT is bounded
  from above, gm/ID ~ 2 / V_DSAT), which is right and matches `T17_STRUCTURE_CANDIDATES_CN.md` 3.6.
- Not done here: the version-3 entries are described in `skills/ic-opt/SKILL.md` step 5 and this section, not in
  `T17_1_5_SPEC.md` 5.2; the digest of a metric at several corners still reads `Observation.metrics` for the
  importance (T17.9 adds `worst_metrics`; switching the importance to it is a later choice).
