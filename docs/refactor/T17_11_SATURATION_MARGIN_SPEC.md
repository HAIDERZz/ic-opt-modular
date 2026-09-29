# T17.11 — a metric read from the operating points: the worst saturation margin of named transistors

Status: specification (2026-09-30), for the coding subagent. Decision behind it: the user's answer of 2026-09-29 23:20
to `T17_STRUCTURE_CANDIDATES_CN.md` section 3.5 (option A) and the go-ahead of 2026-09-30 ("允许 ... 自行规划").

What the literature says (`optimizer_research/papers_2024_2026/LEARNINGS_CN.md` 2.2): the worst transistor's
saturation margin as a *continuous* metric beat a yes/no "all saturated" gate in a controlled experiment (PhysicsSAO,
TCAS-I 2026, 20 runs), industrial specs carry it as a constraint on most transistors (TradeOffMTS: 20 of 31
constraints), and a DC-only testbench that gives it is the cheapest simulation a point has — with T17.8's schedule
(cheap and often failing first) it becomes the first gate on its own. Nothing in the numeric core changes: it is one
more metric, modelled, constrained and reported like any other.

## 1. What it is

A metric of a testbench that is computed by ic-opt from the operating points the testbench's DC analysis wrote
(T17.5: `child.operating_points`, quantities `vds`, `vdsat`, ...), instead of by an OCEAN expression:

```yaml
metrics:
- name: SAT_MARGIN
  unit: V
  testbench: dc            # any testbench whose netlist has (or gets, T17.5) a DC operating-point analysis
  saturation_margin:       # the transistors watched, by the instance names the operating-point table uses
    instances: [M1, M2, M3, M4, M7]
constraints:
- {metric: SAT_MARGIN, op: ge, value: 0.05 V}   # a constraint's value takes no SI prefix: 50m V would read as 50 V
```

Value = the smallest, over the listed instances, of `|vds| − |vdsat|` (absolute values: a PMOS's table carries
negative voltages; the margin is how far the device is beyond its saturation voltage, positive inside saturation).
The user names the instances: a switch, a device meant to be in the triode region, must not be listed (PhysicsSAO
says so too), and the digest's operating-point table (T17.5) shows the names to use.

## 2. Contract

- `Metric` (`spec.py`): a new optional field `saturation_margin: SaturationMargin | None` with
  `instances: list[str]` (at least one, distinct, each a non-empty name). Validation: with it, `expression`,
  `device`, `quantity` must be absent and `testbench` present; the unit is the user's (`V`). A spec that names such a
  metric with `simulator.operating_points: false` is refused at load ("SAT_MARGIN reads the operating points; set
  simulator.operating_points true"). The fingerprint changes as it does for any metric (it is part of the problem).
- The OCEAN script (`sim/ocean.py`, `script(...)`): metrics without an expression are not written into it (today every
  testbench metric has one; check every place that iterates the testbench's metrics: the script, `required_signals`,
  the doctor's expression checks, `author-spec` checks).
- The extract stage (`stages/spectre_chain.py`): after the operating points are parsed, each saturation metric of
  this testbench is computed and added to the child's `metrics`. An instance missing from the table, or one whose row
  lacks `vds` or `vdsat` (a number), makes the metric absent and the child `metric_failed` with an issue
  `SAT_MARGIN: no operating point for M7` (one issue naming every missing instance), exactly as an expression that
  returned nil does — the other metrics stay. A testbench whose result has no operating points at all (T17.5:
  `operating_points None`) gives the same failure for this metric.
- The cache: a cached child written before this metric existed has no such metric; the pipeline fingerprint or the
  spec fingerprint (the metric is in the spec) already separates it — state which in the record.
- Everything downstream is unchanged: `sim.corner.aggregate`, `worst_metrics` (a lower-bound constraint: the
  smallest over corners), the strategies, the digest (it is a metric with a unit), the report.
- ngspice testbenches are out of scope (the product's testbenches are Spectre; the research benchmark is separate).

## 3. Documents

- `skills/author-spec/SKILL.md`: a subsection "a DC-only testbench as the first gate": export a testbench with only a
  DC analysis (or let T17.5 add one), one `saturation_margin` metric naming the transistors that must stay in
  saturation (not the switches), a constraint `ge 0 V` or `ge 0.05 V`; with the schedule on (several corners by default,
  or `simulator.stop_at_first_failure: true`) a point that fails it runs nothing else. Two sentences on where the rule
  comes from (PhysicsSAO, TradeOffMTS) and that it was not measured on this project's circuits yet.
- `skills/ic-opt/SKILL.md`: one sentence in step 5 (the metric appears like any other; its failure issue names the
  missing instance) and one in the spec cheat-sheet.
- `README.md`: the metric kind in the metrics list.

## 4. Tests (`tests/ic_opt/test_saturation_margin.py`)

With the fake executor / fake stages the existing tests use (`tests/ic_opt/fakes.py`, T17.5's `oppoints.tsv`):
1. Spec validation: the good form loads; `expression` together with it, no `testbench`, empty instances, duplicates,
   and `operating_points: false` are refused with the messages above.
2. The value: NMOS and PMOS rows (negative voltages) give `min(|vds| − |vdsat|)`; a missing instance and a missing
   `vdsat` give `metric_failed` with the issue naming it, other metrics kept; no operating points at all → the same.
3. The OCEAN script written for a testbench with an expression metric and a saturation metric holds the expression
   only; the extract stage adds the saturation metric to the child's metrics.
4. End to end through `sim.evaluate` on a two-testbench spec (a DC testbench with the saturation metric, a second
   testbench with an expression metric), fake stages: the observation holds both metrics; with the stop on and a
   history that makes the DC child fail often, the schedule runs it first and stops the point there (reuse
   `test_schedule.py`'s helpers).
5. The digest and the report on such an observation list the metric with its unit.

Targeted run: `tests/ic_opt/test_saturation_margin.py test_spec*.py test_ocean*.py test_spectre_chain*.py
test_schedule.py test_digest.py test_skill_author_spec.py` (whatever of these exist), then `ruff check src tests`.

## 5. Working rules for the coder

- Branch `t17-11-saturation` in its own worktree (`git worktree add /home/zzchen/Agent_virtuoso/EDA_AI_AGENT/worktrees/t17-11-saturation -b t17-11-saturation main`);
  commits on that branch only; never touch `main`, never push.
- Another agent works at the same time on `t17-12-threads` (the `Simulator` block of `spec.py`, `blocks/doctor.py`,
  `blocks/optimize.py`, `blocks/evaluate.py`'s plan line, `suggesters/openbox.py`, `suggesters/turbo.py`). In `spec.py`
  touch the `Metric` model and its validators only; do not edit those other files.
- Python: `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-modular/.venv/bin/python` with `PYTHONPATH=<worktree>/src`
  (check `import ic_opt` resolves to the worktree); never `uv run`, `uv sync`, `pip`. No simulator runs, no network.
  Tests count as machine load: run the targeted set once per commit, with `OMP_NUM_THREADS=1`.
- One commit per coherent piece (suggested: the spec model + validation; the extraction + script; docs + tests), each
  message saying what and why; trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Hand back: branch and commits; the verbatim test and ruff output (last 30 lines); what the specification left open
  and how it was read (a numbered list); what could not be done and why.

## 6. Record (2026-09-30)

Implemented by the coding subagent on `t17-11-saturation` (`309828c`, `d991862`, `9b3bd99`), merged `8c3cb2f`;
targeted tests 146 passed, ruff clean. How the open points were read (the coder's list, kept here):

- The spec's constraint example `50m V` was wrong: a constraint's value takes no SI prefix (`parse_scalar("50mV")`
  gives 50 with unit mV, compared as 50 V). The docs and tests write `0.05 V`; `objective.py` was left as it is (a
  change there would alter what existing specs mean). Corrected above.
- The failure issue reads `metric SAT_MARGIN failed: no operating point for M7` (the `metric X failed:` form the
  per-batch line and the digest recognise), one issue naming every missing instance; a row lacking `vds` or `vdsat`
  counts as missing; with no table at all every instance is named.
- Instance names are looked up as given, then with a leading `/` added or removed (T17.5's table writes `/M1`); no
  other normalisation. The real table's format on the user's netlists is unverified (no Spectre run here).
- `testbench` is required on the metric even in a one-testbench spec.
- The `operating_points: false` refusal lives in `Spec._cross_references`' metric loop; the instance checks on
  `Metric`. `Metric._dump` keeps the unset field out of the dump, so every existing fingerprint is unchanged (pinned
  tests); a spec that gains the metric simulates again (the spec fingerprint), a spec without it reuses its records
  (the pipeline fingerprint is unchanged).
- The script and `Extract` list expression metrics through `metrics_for`; `replay_script` filters by itself too; the
  EM circuit pipeline reuses `Extract`, so the metric works there (untested with EM).
- The end-to-end schedule test passes `stop_at_first_failure=True` (at one condition the stop is off by default since
  T17.9) and shows the DC child running first and stopping the point once the history says it fails often.
- Not done: a real Spectre check of the instance-name format; the per-batch line's "fix the expression" wording in
  `blocks/evaluate.py` (another branch's file) reads oddly for this metric.
