# T18.4 — the `signoff` recipe tightens a constraint the re-check missed and searches again (bounded rounds)

Status: specification (2026-10-01), for the coding subagent. Decision behind it: the user's answer of 2026-10-01 to
`T18_LIBRARY_DIRECTIONS_CN.md` section 7, question 5 ("按照推荐": the check-and-tighten recipe, with a bounded number of
rounds), and the approval of stage L4 on 2026-10-01. The idea is COmPOSER's stage 4 (a design that misses a
specification after verification tightens that specification and is synthesized again) as a thin recipe over the
blocks that exist: nothing in the numeric core, the schedule or the verdict changes.

Read before writing: `src/ic_opt/recipes/signoff.py`, `src/ic_opt/blocks/optimize.py` (`optimize`, `adopt`,
`_at_corners`, `history_size`), `src/ic_opt/blocks/evaluate.py`, `src/ic_opt/sim/corner.py` (`aggregate`,
`worst_metrics`), `src/ic_opt/objective.py`, `src/ic_opt/spec.py` (`Constraint`, `problem`, `fingerprint`),
`src/ic_opt/digest.py` (how rows are selected by fingerprint), `tests/ic_opt/test_cli_recipes.py` (the signoff test and
the fakes it uses).

## 1. What the recipe does

`ic-opt run signoff PROJECT [corner=tt] [budget=60] [batch=10] [top=5] [strategy=auto] [seed=0] [full=false] [rounds=1] [tighten=1.0]`

Today (`rounds=1`): search at `corner`, re-check the top `top` points at every corner, report. With `rounds` above 1:

1. After the re-check, take the best re-checked point: among the re-checked points that are feasible at every corner,
   the best objective; if none is, the re-checked point with the smallest `constraint_penalty` (the aggregate's, over
   its corners). If a re-checked point is feasible at every corner the recipe stops: done.
2. Otherwise, for every constraint the chosen point misses at some corner, the *miss* is how far its worst corner's
   value lies outside the limit (in the metric's unit, from `sim.corner.worst_metrics` and the constraint's `op` and
   `value`). The recipe **tightens** that constraint by `tighten × miss` -- an upper limit moves down, a lower limit
   up -- and prints one line per tightened constraint: the metric, the limit as written, the miss, the limit for the
   next round. A constraint no re-checked point missed is left alone.
3. The next round searches at `corner` again with the tightened constraints: `b.optimize` on a copy of the spec whose
   constraint values are the tightened ones, handed the whole history of the original problem's search and of every
   earlier round as `initial=` (`adopt` re-derives each row's verdict under the tightened spec from its metrics; the
   `metric_gp` history rule about one set of corners holds, since every search row is at `corner`), with a step name
   that says the round (`search@tt#2`), `current=False`, and `budget` more points. Then the new top `top` points are
   re-checked at every corner **under the original spec** (the user's constraints, as written), step `signoff#2`.
4. Rounds go on until a re-checked point is feasible at every corner or `rounds` searches were made. The final report
   covers the last round's re-check; every round's tightening lines are also written to
   `.icopt/reports/signoff_rounds.json` (round, tightened constraints with the numbers above, the best re-checked
   point and its verdict).

## 2. Rules

- The user's spec is never rewritten on disk. The tightened spec is a `model_copy` with new constraint values; its
  fingerprint differs, so its search rows are stamped with it in the same store, and `initial=` is how the next
  round sees the earlier rows. Say in the docstring what that means for a reader of the store: `ic-opt digest`
  and `report` on the project show the original problem's rows; the rounds' search rows are listed by their step
  names, and the JSON file above ties them together.
- A miss is measured on the metric's own scale; `tighten` scales it (1.0: exactly the miss; the user may give
  another number). A tightened limit is written in the constraint's own text form and unit (`Constraint.value` is
  text: format it as the spec writes it, and refuse -- with a message -- a constraint whose value carries a unit
  the recipe cannot format back).
- A constraint on a metric that has no value at the chosen point (a failed simulation) cannot be tightened: the
  recipe says so and leaves it alone.
- `--plan` prints the rounds' shape (as the search and the re-check print theirs today) without tightening
  anything.
- `rounds=1` gives byte for byte what the recipe gives today (the existing test passes unchanged).
- A spec with library devices (T18.2B) runs the same way; nothing special.

## 3. Documents

- The recipe's docstring; `README.md`'s recipe list; `skills/ic-opt/SKILL.md` (the recipe cheatsheet line and step 6,
  where a miss after the re-check is a reason to run another round); `docs/refactor/T18_LIBRARY_DIRECTIONS_CN.md` is
  the coordinator's, leave it.

## 4. Tests (`tests/ic_opt/test_signoff_tighten.py`, the fakes of `test_cli_recipes.py`)

1. `rounds=1`: the existing behaviour, pinned against the current test's expectations.
2. A fake whose search corner passes a constraint that another corner misses by a known amount: with `rounds=2` the
   second search runs under the limit tightened by that amount (assert the printed line and the JSON file), its rows
   carry the tightened spec's fingerprint, the re-check runs under the original spec, and the store holds both
   steps.
3. The recipe stops early when the re-check is feasible at every corner (no second search).
4. `tighten=0.5` halves the move; a constraint nobody missed stays; a metric without a value is reported and left.
5. `--plan` with `rounds=3` prints three searches and re-checks and changes nothing.

Targeted run: the new file, `tests/ic_opt/test_cli_recipes.py`, `test_multi_corner.py`, `test_schedule.py`; then
`ruff check src tests`.

## 5. Working rules for the coder

- Branch `t18-4-tighten` in its own worktree (`git worktree add /home/zzchen/Agent_virtuoso/EDA_AI_AGENT/worktrees/t18-4-tighten -b t18-4-tighten main`);
  commits on that branch only; never touch `main`, never push.
- Python: `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-modular/.venv/bin/python` with `PYTHONPATH=<worktree>/src`;
  never `uv run`, `uv sync`, `pip`. No simulator, no network, no file of the user's private library.
- Tests count as load on a machine that runs the user's simulations: `OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
  MKL_NUM_THREADS=1`, at most two test processes at a time, never the whole suite, never `-n`.
- No name, number, layer or path of a real process anywhere (the repository is public).
- One commit per coherent piece; trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Hand back: branch and commits; the verbatim test and ruff output (last 30 lines); what the specification left open
  and how it was read (a numbered list); what could not be done and why.
