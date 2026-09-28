# T17.7 — the existing strategies search on a logarithmic scale where a range spans a decade

Status: specification, 2026-09-28. Decided by measurement (section 1). One task, one commit.

## 1. Why

The ablations of `metric_gp` (plan, section 7) found that searching on a logarithmic scale carries most of its lead on
wide-range problems. The existing strategies were then given the same scale in a copy of the code and measured on the
12 wide-range development problems, seeds 0–4, 200 points, 48 measures each (first feasible point, best at 50 / 100 /
200 points; one-sided Mann-Whitney, 5%):

| comparison | better | worse | within the seeds |
| --- | --- | --- | --- |
| TuRBO, logarithmic against linear | 23 | 0 | 25 |
| OpenBox (`openbox_gp_eic`), logarithmic against linear | 14 | 1 | 33 |
| Sobol, logarithmic against linear | 11 | 5 | 28 |
| `metric_gp` against TuRBO, linear | 29 | 0 | 19 |
| `metric_gp` against TuRBO, logarithmic | 9 | 3 | 36 |
| `metric_gp` against OpenBox, logarithmic | 25 | 0 | 23 |

The two model-based strategies gain, without a measure that pays for it. They stay in use for every spec with EM devices
or several corners until stages 2 and 3 of T17, so the gain is worth having now. Pure sampling (Sobol) is mixed: it is
left as it is.

## 2. The rule

One rule, in one place, used by `metric_gp`, `openbox_*` and `turbo`:

> A variable is searched on a logarithmic scale when its lower bound is positive and `upper / lower >= 10`. Every other
> variable is searched on the linear scale.

`metric_gp` has this rule already (`suggesters/metric_gp/coords.py`, `LOG_SPAN`). Move the rule to one function both
sides call; `metric_gp`'s proposals must not change by a bit (its tests pin them).

The rule looks at the range alone, not at what the variable is: a transistor width, a bias current, a device's turn
width. No benchmark covers EM device variables; the documents say so.

## 3. What changes, and what does not

Changes — inside `suggesters/openbox.py` and `suggesters/turbo.py` only:

- The strategy's own numeric space is `log10(value)` for a variable under the rule: the bounds it is given, the history
  it is fed, its initial design (the unit cube is read evenly per decade, as `metric_gp`'s design is), its proposals.
- What a strategy hands back (`Proposal.raw`) stays in the numeric space of `space.bounds`: the strategy converts its
  proposals back (`10 ** x`) before it returns them. `blocks.optimize.suggest` snaps them as before.
- OpenBox: a variable under the rule is a continuous `Real` between `log10(lower)` and `log10(upper)` (no `q`); two
  proposals that snap onto one grid point are handled by the loop that already handles duplicates. ConfigSpace rounds
  the default value to 10 digits, which can land outside the bounds (`log10(0.5)` does): give a default that is inside
  by construction, not a `try`.
- TuRBO: `lb`, `ub`, `_raw` and the conversion back. The trust region's replay reads tags and objective values, not
  coordinates; check that a continued run equals an uninterrupted one (the existing test).

Does not change:

- `space.bounds`, `space.snap`, `space.to_raw`, `space.check`: the numeric space of the spec, used by `points.*`,
  `analyze`, the `random` and `sobol` strategies.
- The spec, its fingerprint, observations, origins, the store.
- A spec without a variable under the rule: every proposal of `openbox_*` and `turbo` is what it was, bit for bit. Pin
  the proposals of three calls per strategy on the commit you start from, as T17.4 did for advice.
- `strategy=auto`.

## 4. Behaviour change to document

A run continued with this version whose spec has a variable under the rule proposes differently from the version that
started it: the same history is read on another scale. Nothing stored is invalid. README ("Use"), the skill's cheatsheet
(the `openbox_*` and `turbo` lines), the plan's section 7 (a record with the table of section 1), BACKLOG (a new entry,
next free number), and a line for the next release's notes (`docs/refactor/` has none yet: put it in the BACKLOG entry).

## 5. Tests

1. The rule: positive range spanning a decade -> logarithmic; a range that starts at 0 or below, or spans less ->
   linear; one function, the same answer for `metric_gp`'s coordinates.
2. Without a variable under the rule: proposals pinned (section 3).
3. With one: every proposal is a grid point inside the range; the initial design is even per decade (of a design of
   64 points on a 1–1000 range about a third falls in each decade); OpenBox's search space builds for lower bounds
   whose `log10` rounds either way (0.5, 0.18, 5e-7); a history is fed without error and the strategy proposes.
4. Continuation: a run in batches equals the run in one piece, for both strategies, on a spec with a variable under the
   rule.
5. The benchmark: `benchmarks/icopt_bench` needs no change; say in the hand-back what `python -m icopt_bench.loop`
   gives for `turbo` on `syn_amplifier_like`, seed 0, 60 points, before and after (best feasible objective).

## 6. Not in this task

- A per-variable override in the spec (`scale: log | linear`): a schema change, and no case for it yet.
- The `random` and `sobol` strategies and the `points.*` blocks.
- Threads of the strategies' own computations (BACKLOG N-70).

## 7. As implemented

- The rule is `ic_opt.space.log_scale(lower, upper)`. `metric_gp`'s coordinates, the two strategies and the digest's
  thirds of a range call it (the digest had its own copy of the rule). The strategies' numeric space is
  `ic_opt.suggesters.base.SearchScale`.
- Section 3's "handled by the loop that already handles duplicates" did not hold: OpenBox removes duplicates by
  configuration, so without `q` its proposals often snapped onto evaluated grid points and `suggest` replaced them with
  random `:fill` points (8 to 35 of 45 model points on coarse grids). With a variable under the rule, a batch is chosen
  by `openbox.grid_suggestions`: OpenBox's own steps (`Advisor.get_suggestions` / `get_suggestion`) over its ranked
  candidates, with "already evaluated" judged on the grid. Without one, OpenBox is called as before. Measured in the
  plan's section 7; BACKLOG N-71.
