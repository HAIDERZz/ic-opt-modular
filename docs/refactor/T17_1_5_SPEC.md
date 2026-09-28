# T17.1.5 — the run digest, advice, operating points: specification

Status: specification for implementation (2026-09-28). Decisions behind it: `T17_OPTIMIZER_PLAN_CN.md`, D7, D7b, D8 and
the two requirements added under D10. The user decided on 2026-09-28 to enter this step.

## 1. What this step is for

ic-opt calls no language model (D7). What it does instead: after a run it states, computed by program, what the run
found (**the digest**); it takes **advice** as data — start points, narrower ranges, variables to hold — from whoever
read the digest, a person or the user's own agent; and it records the advice so that the run can be replayed without
anyone. The numeric strategy stays what it is; advice changes where part of a batch looks, never what the spec allows.

Three parts, three tasks, one commit each:

| task | part | files it owns |
| --- | --- | --- |
| T17.3 | the digest; the report's range section | `src/ic_opt/digest.py` (new), `src/ic_opt/blocks/analyze.py`, the `digest` command in `src/ic_opt/cli.py`, `tests/ic_opt/test_digest.py` |
| T17.4 | advice | `src/ic_opt/advice.py` (new), `src/ic_opt/blocks/optimize.py`, `src/ic_opt/suggesters/metric_gp/`, the `advise` command in `src/ic_opt/cli.py`, the recipes' `start` handling, `tests/ic_opt/test_advice.py` |
| T17.5 | operating points | `src/ic_opt/sim/netlist.py`, `src/ic_opt/sim/ocean.py`, `src/ic_opt/observation.py`, `src/ic_opt/eval/`, `src/ic_opt/blocks/evaluate.py`, `src/ic_opt/blocks/doctor.py`, `tests/ic_opt/test_operating_points.py` |

Sections 2 to 4 are the contracts between the parts. A task reads what another part writes only through them, and
works when the other part's data is absent (an older store, a run without advice, a netlist without operating points).

## 2. Contract: advice records

File `<project>/.icopt/advice.jsonl`, append only, one JSON object per line:

| field | meaning |
| --- | --- |
| `id` | `a1`, `a2`, ... in order of adoption |
| `event` | `adopt` or `revoke` (a revoke row carries `id` of the advice it ends, `at`, `since`, `reason`) |
| `at` | UTC timestamp |
| `since` | the number of observations of this problem in the store when the row was written |
| `author` | free text: who gives the advice (a person's name, "agent: <model>") |
| `reason` | free text, required: why |
| `start` | list of complete grid parameter rows to evaluate first; may be empty |
| `ranges` | `{variable: [lower, upper]}` in the spec's own text form (`0.8u`), inside the spec's range |
| `fixed` | `{variable: value}`: hold the variable at this level |
| `vary` | list of variables; when given, every other variable is held at the level of the search region's centre |
| `spec_fingerprint` | of the spec the advice was checked against |

At most one advice is in effect at a time: adopting a new one ends the one before (no revoke row is needed; the
digest says "superseded by a3"). **The advice in effect for a batch proposed at history size `k`** is the last
`adopt` row with `since <= k` that no `revoke` row with `since <= k` ends. This rule alone decides; nothing else in
the store says which advice applied.

## 3. Contract: origin of a point proposed under advice

- A start point of an advice: origin `advice:<id>` (where a start point of the run has `start`).
- A point a strategy proposed inside an advice's ranges: the strategy's usual origin with `@<id>` appended:
  `suggest:metric_gp:tr:0:40@a2`. A point of the same batch proposed over the spec's whole range carries no suffix.
- Every reader of origins (the region replay of `metric_gp`, the design line, the report, the digest, the benchmark)
  must treat `...@<id>` as the origin without the suffix plus the advice id. T17.4 changes the readers that exist.

## 4. Contract: operating points in a child's result

`ChildResult.operating_points: dict[str, dict[str, float]] | None` — instance name as the simulator reports it
(`/M1`, `/I0/M3`), then quantity name, then value. `None`: not extracted (an older store, a child that is not a
Spectre simulation, a netlist whose results hold none). Quantities, fixed (D8), each present only where the
simulator gives a number for it: `region`, `ids`, `vgs`, `vds`, `vbs`, `vth`, `vdsat`, `gm`, `gds`, `gmoverid`,
`cgs`, `cgd`. Only instances that report `gm` are recorded (transistors, not resistors or sources).

The numeric strategies never read this field (D8). A test guards it: no module under `src/ic_opt/suggesters/` names
`operating_points`.

## 5. T17.3 — the digest

### 5.1 Interface
- `ic_opt.digest.digest(spec, observations, *, advice=(), top=5, step=None) -> dict`: pure, no file access, no
  simulator; works on any list of observations of the spec (the benchmark calls it without a store).
- `ic_opt.digest.markdown(d: dict) -> str`.
- Block `b.digest(spec, observations, store, ...)`: writes `reports/digest.json` and `reports/digest.md` (atomically)
  and returns the path of the Markdown file.
- Command `ic-opt digest PROJECT [--step S] [--top N] [--json]`: prints the Markdown (or the JSON) and writes both
  files. Read-only with respect to the run: it takes no lock and starts nothing; while a run holds the project it
  reads what is there.

### 5.2 Content (`"digest_version": 2`)
Every number is computed from the observations. Where there is too little to compute something, the entry says so
(`null` and a `note`), it is not guessed. Version 2 (2026-09-29, `T17_3B_DIGEST_SPEC.md`, after the first sessions that
advised runs from the digest) changed the rows `advice`, `failures`, `strategy` and `operating_points`.

| key | content |
| --- | --- |
| `problem` | variables (name, lower, upper, step, number of levels, `scale`: `log` where `upper / lower >= 10` and `lower > 0`, else `linear`), constraints, objective, the corners the observations were evaluated at |
| `counts` | points; by status; simulations; points per step |
| `progress` | index and id of the first feasible point; the best feasible point (id, parameters, metrics with units, objective as the spec states it); the best objective after every batch (a batch: the points with the same batch key in their origin, else the step's order in tens) |
| `constraints` | per constraint: scored points that meet it; points that fail only it; the best feasible point's margin and the margin of the closest point that fails it, both as the constraint states them and divided by the metric's spread |
| `variables` | per variable: levels visited of levels there are; the span of the `top` best feasible points; `at_bound`: `lower` / `upper` / `null` for the best point; the share of points that gave no value in the lower, middle and upper third of the range; per modelled metric the rank correlation with the variable over the scored points (omitted below 10 scored points) |
| `failures` | per status the count; per metric how often it is the one missing; for the three variables that separate scored from unscored points best: the level at which one split of the variable separates them, and the shares on either side; `messages`: the three most frequent texts among the `issues` of the points that gave no value, as stored, each with the number of those points that hold it (`[]` when every point gave a value) |
| `suggested_ranges` | per variable: the span of the `top` best feasible points widened by one level on each side, clipped to the spec's range; `reaches_bound` when the span touches the spec's bound (the message for a person: the spec's range, which only the user changes, may be too narrow there). With fewer than 3 feasible points: `null` |
| `strategy` | the strategies the origins name and how many points each; for `metric_gp` the search region as `MetricGpSuggester.region_state` gives it (index, side, successes, failures, the centre's observation id, regions ended) and the design size. The Markdown states what the side means where it prints it: per variable the region holds the levels within `side × weight / 2` of the centre in unit coordinates (a range 1 long; the weight between 0.2 and 5), and always the centre's level and its two neighbours; where `side × weight` reaches 2 it holds every level of the variable |
| `advice` | per advice (section 2): the row; `in effect` / `revoked` / `superseded by`; `period: [since, until]`: from its `since` up to the `since` of the row that ended it (its revoke row or the next adopt row; `until` is `null` while it is in effect); `under` and `others`, each `{points, feasible, no_value, best}`: the points proposed under it (origin suffix) and the others of its period -- the points proposed at a history size inside the period (the batch key of the origin, else the point's position in the run) that are neither under it nor its start points; `start_points`: per start point id, status, objective (`null` unless feasible), `best_then` (the run's best feasible point when it was evaluated); `best_at_bound`: `[{variable, side, value}]` where the run's best feasible point, proposed under the advice or lying inside its ranges, is at a bound of the advice's range that is not the spec's bound (`[]` when none; `null` when the best point is neither under nor inside the advice, or the advice has no ranges) |
| `operating_points` | for the best feasible point and for the first `start` point (the design as exported), per child: the table of section 4; `null` where the store holds none; `recorded`: how many points hold operating points (when none does, the Markdown says so in one sentence) |

### 5.3 Markdown
For a reader who has not seen the project: what is optimized, how far the run is, what is in the way, where the good
points are, what failed and where, what advice was given and how it fared, the operating points of the best point.
Values with units and SI prefixes as the report prints them. Tables, no prose filler; at most 200 lines for a spec of
12 variables and 8 metrics. It states at its end what the numbers are not: correlations are not causes; suggested
ranges describe the points found, not where the optimum is.

### 5.4 The report's range section
`blocks/analyze.py`'s "Space compression advisory" is replaced by "Where the best points are": the table of
`suggested_ranges`. The OpenBox compressor is no longer imported anywhere in `analyze.py`; the report must not need
OpenBox installed (test it with OpenBox made unimportable).

### 5.5 Tests
1. On a made-up run with a known structure (a constraint only one variable decides, a region that gives no value, a
   best point at a bound): every entry of 5.2 has the expected value.
2. Too little data: no feasible point; fewer than 10 scored points; no observation at all.
3. A store of 0.4.0 (no advice file, no operating points, origins without suffix) gives a digest without errors.
4. Origins with `@<id>` are counted under their advice and under their strategy.
5. The digest is the same whatever the order of the observations in the file.
6. The command writes both files; `--json` prints valid JSON; while another process holds the project's lock the
   command still works.
7. The report without OpenBox.

## 6. T17.4 — advice

### 6.1 Giving advice
`ic-opt advise PROJECT FILE` reads a YAML file with `author`, `reason`, and any of `start`, `ranges`, `fixed`,
`vary`; checks it; prints what it adopted, with the id; appends the row (section 2). It takes the project's lock: not
while a run goes.

`ic-opt advise PROJECT --revoke ID --reason "..."` and `ic-opt advise PROJECT --list`.

Checks, each with a message that says what to change:
- unknown variable; a variable in both `fixed` and `ranges`, or in `fixed` and `vary`;
- a range outside the spec's range: **refused**. An advice cannot widen what the spec allows; the message says that
  the spec's range is the user's to change;
- a range whose bounds lie between levels: moved inward to the nearest levels, and said so; a range that then holds
  no level: refused;
- a fixed value or a start value between levels: moved to the nearest level, and said so; outside the range: refused;
- a start row that does not name every variable: refused;
- an advice that names nothing (`start`, `ranges`, `fixed`, `vary` all empty): refused;
- `reason` or `author` missing: refused.

### 6.2 What advice does
- `start`: the rows not yet evaluated are proposed first in the next batch, origin `advice:<id>`, for every
  strategy, and count as start points do.
- `ranges`, `fixed`, `vary`: for `metric_gp`. A fifth of a batch's slots is free (D7b): a free slot chooses among
  the candidates the batch has without advice -- the search region's, where they are, and the ones spread over the
  whole space -- so the search the run was making goes on, at a fifth of its pace, whatever the advice says. The other
  slots are advised: they choose among the search region's candidates brought inside the advice -- a variable with a
  range is moved to the nearest level inside it, a fixed variable to its level, a variable not in `vary` (when `vary`
  is given) to the level of the region's centre; duplicates are dropped. If fewer advised candidates remain than
  advised slots, the batch is completed from the free candidates. Points chosen by an advised slot carry `@<id>`
  (section 3); a point chosen by a free slot carries none, also when it lies inside the advice.
- How many slots are free: `round(wide_share * slots)`, as the whole-space share of a batch without advice. A batch
  of one or two slots has none by that count; under an advice slot `b` of the batch proposed at history size `k` is
  then free when `(k + b + 1)` is a multiple of 5, so that a fifth of the points stays free whatever the batch size.
- The small-grid case (the whole grid is the candidate set, no region): the slots of the batch are divided the same
  way — the free slots choose among all grid points, the rest among the grid points inside the advice.
- Amended on 2026-09-28 after measurement (section 6.4). As first specified and delivered, the free fifth chose
  among the whole-space candidates only, and wrong advice ended the local search: the region's candidates were all
  moved onto the advice's boundary.
- Other strategies take `start` only. `opt.optimize` prints one line when an advice in effect has `ranges`, `fixed`
  or `vary` and the strategy is not `metric_gp`: which parts are not used and why.
- The search region's own course (section 9 of the metric_gp specification) is judged as without advice. The replay
  reads origins through section 3.
- `suggest(..., advice=rows)` takes all rows of the file; the strategy applies section 2's rule at `len(history)`.
  `opt.optimize` reads the file before every batch. The same history, seed and advice rows give the same proposal;
  a continued run equals an uninterrupted one given the same advice rows (test both).

### 6.3 Tests
1. Every check of 6.1.
2. The rule of section 2 for `adopt`, `revoke`, superseding, and rows whose `since` lies after the batch.
3. `start` rows come first, once, with their origin, for `metric_gp`, `openbox_gp_eic` and `turbo`.
4. `metric_gp` with ranges on a space with a region: of 10 slots 2 carry no suffix and may lie anywhere, 8 carry the
   suffix and lie inside the ranges; with `fixed`; with `vary`; on a small grid. The free slots' candidates are those
   of the batch without advice. Batches of one and of two slots: over 10 points proposed one batch after another, 2
   are free.
5. An advice whose ranges hold no unevaluated point: the batch is completed from the whole space, no error.
6. Wrong advice does not end the search: on a problem whose optimum lies outside the advised ranges, a run of 100
   points with the advice adopted at 20 finds a feasible point, and it keeps its share: its best objective at 100
   points is not worse than that of the same run without advice at 36 points (20, and a fifth of the 80 that
   followed). For seeds 0 to 4. The first criterion ("not worse than twice the distance from the optimum of the run
   without advice") asked of a fifth of the points what the whole run achieves, and cannot be met where the run
   without advice reaches the optimum (distance 0: the constrained Ackley problem of section 6.4); it is withdrawn.
   On the test's own problem the amended behaviour meets it as well (distance ratios 0.97 to 1.44, seeds 0 to 4).
7. Replay and continuation (6.2).
8. The design line and the `auto` line of `opt.optimize` are unchanged by an advice; the line about unused parts.
9. The store of a run without advice has no advice file, and everything behaves as before this task (pin the
   proposals of three calls on the commit you started from).

### 6.4 What was measured (2026-09-28)

Scripted advice, adopted at 40 points, runs of 200 points, against the run of the same seed without advice; 8 synthetic
problems with seeds 0-9 and the 12 wide-range development circuits with seeds 0-4. Three kinds: *right* (ranges of 0.3
of the axis around the best point any run had found, for up to six variables), *wrong* (the two most influential
variables sent to the far part of their axis), *digest* (every 40 points the ranges the digest suggests, taken as they
are). Three variants of what an advice does to a batch: S (as first specified), A (this section's 6.2), B (A, and the
advised slots search the narrowed problem: a region around the best point inside the advice, and points spread over its
box). Per cell: problems better / worse / within the seeds at 200 points (one-sided Mann-Whitney, 5%).

| variant | right | wrong | digest | runs that kept their share under wrong advice |
| --- | --- | --- | --- | --- |
| S, synthetic | 2 / 0 / 6 | 0 / 5 / 3 | 0 / 3 / 5 | 36 of 80 |
| A, synthetic | 2 / 0 / 6 | 0 / 1 / 7 | 0 / 0 / 8 | 80 of 80 |
| B, synthetic | 0 / 0 / 8 | 0 / 1 / 7 | 1 / 1 / 6 | 80 of 80 |
| S, circuits | 2 / 0 / 10 | 1 / 3 / 8 | 0 / 4 / 8 | 46 of 60 |
| A, circuits | 4 / 0 / 8 | 0 / 1 / 11 | 0 / 0 / 12 | 59 of 60 |
| B, circuits | 4 / 0 / 8 | 0 / 1 / 11 | 0 / 0 / 12 | 58 of 60 |

A and B do not differ beyond the seeds; A is the smaller change and is taken. Right advice helps on the circuits (about
seven runs in ten end better than without). Taking the digest's suggested ranges as they are helps nowhere: seed by seed
more runs end worse than better (A, circuits: 20 better, 39 worse). The ranges say where the best points found lie; an
advice needs a reason beyond them.

## 7. T17.5 — operating points

### 7.1 Getting them
Spectre writes operating points into its results when the netlist asks for them: an `info` statement with
`what=oppoint where=rawfile` after a DC analysis. An export from the design environment usually has both.

- At render time ic-opt looks for such a statement. If it is there, nothing changes. If it is not, and the netlist
  has a DC analysis, ic-opt appends `icoptOpInfo info what=oppoint where=rawfile` after the analyses. If the netlist
  has no DC analysis either, ic-opt appends a DC operating-point analysis and the statement. What was added is
  written into the rendered deck as a comment and printed once per run.
- The rendered netlist is otherwise byte for byte what it was (test it). A spec may switch this off:
  `simulator.operating_points: false` (default `true`); then nothing is added and nothing is extracted.
- `ic-opt doctor` and `--plan` say per testbench: `operating points: in the export` / `added by ic-opt (statement)`
  / `added by ic-opt (DC analysis and statement)` / `off`.

### 7.2 Reading them
The replay script (`sim/ocean.py`) gets a second part, after the metrics, that cannot make a metric fail: it selects
the DC operating-point result, lists its instances, and writes one row per instance and quantity of section 4 to
`oppoints.tsv`; an instance or quantity without a number is left out. A result without operating points gives an
empty file and `operating_points = None`, no issue, no failed child. The extract stage parses the file into the
child's result.

The cache: a child served from the cache carries the operating points stored with it. A cache entry written before
this task has none; it stays valid (the metrics are what they were) and the child's `operating_points` is `None`.
Whether the fingerprint of the Spectre stage must change is for the implementer to find out and state: adding the
`info` statement changes the rendered netlist of an export that lacked it.

### 7.3 Tests
With the fake executor of `tests/ic_opt/fakes.py`, extended to write an `oppoints.tsv`: the four cases of 7.1 on
made-up netlists (the statement present; absent with a DC analysis; absent without; switched off); a result without
operating points; the cache cases of 7.2; the observation's JSON round trip; a store written before this task reads
as it did. No test needs a real simulator; no netlist in the tests comes from a real design.

What the real check found (2026-09-28, one point, one testbench, run with the user's approval): Spectre accepts the
netlist with the DC analysis and the statement appended (0 errors) and the metric is the same to the last digit as
without them; OCEAN names the result of a statement that does not carry one of the design environment's own names
`"<name>-info"` (a string), not `<name>`, and `OP()` reads the design environment's own result only, so the first
version of the script read nothing. The script now looks the result up in `results()` under both names and reads with
`pv(... ?result ...)`; run on existing results of both kinds it returns twelve quantities for each transistor.

The real check, on the simulation host, is not part of the automated tests: one point of one testbench whose export
lacks the statement, run once with the statement added, to see that Spectre accepts the rendered netlist and that
the table comes back. It is run by the maintainer after review, with the user's approval of the run.

## 8. What is deliberately not in this step
- No call of a language model from ic-opt, no network access, no key (D7).
- No automatic constraint from operating points ("every transistor saturated"): they are shown, not used (D8).
- No adapting of the share of a batch that stays in the full range; it is a fifth (D7b: to be decided with data).
- Several advices in effect at once.
