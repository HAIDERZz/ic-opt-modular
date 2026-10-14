# N-127: a curve between two knots is answered from the knots' models, without a fit (2026-10-14)

Step 3 of 3 (user decision 2026-10-14, "按这三步执行"). With N-126 a library may declare a frequency grid, and `lib.prefit`
fits every knot column (a curve's anchors and the grid's frequencies) ahead. A curve asked at a frequency between two knots is
still an extension column fitted on first use. Measured on the library's own rows
(`/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-accept/n122_random_requirements/freq_grid/RESULT_CN.md`), interpolating a
row's curve between the grid's frequencies with PCHIP errs by at most a few tenths of a percent (p99 at or below 1.7 %), less
than the models' own error, so the knots' models can answer between them.

## 1. What changes

- When the stratum has a grid and a curve column `<curve>@<f>` is asked at an f that is no knot of that curve but lies between
  two of them: no model is fitted. Its prediction at a point is the PCHIP interpolation in frequency of the knots' predictions
  at that point, over the stencil of the two knots on each side of f (one where there is no second), with the knots' own
  calibrated bounds interpolated the same way and then widened by the curve's interpolation allowance: the p99 relative error of
  PCHIP-interpolating the stratum's own rows' measured curve over the same knots in the knot interval around f -- computed from
  the rows at first use (seconds; the extension measurements), cached per dataset key, curve and knots.
- **Domain**: a point answers only where every stencil knot column is in domain and each is below its own resonance margin at
  that point (the N-113 rule, per knot), and the curve's resonance rule at f holds. A point whose next knot above f lies above its
  resonance margin is `out_of_domain` with the reason ("between the knots a and b GHz and next to its resonance; exact=true fits
  this frequency's own model").
- A library row is answered with its measured value at f (the extension measurement), as today.
- **Answer**: the statuses as today, plus `interpolated: {"knots": [...], "allowance": a}` on an interpolated answer.
  `lib.suggest`, `lib.region`, `lib.densify` and `lib_design` use the same columns (their pools and grids predict through them).
- **`exact=true`** on `lib.query`, `lib.suggest`, `lib.region`, `lib.densify` and `lib_design` gives today's behaviour: the
  frequency's own extension column, fitted when not cached. Without a grid, nothing changes.
- `lib_design`'s prediction stage names the setting in its identity when it is not exact (as N-116's extrapolation does).
- Do not change `gp.py` / `composed.py`. Think about where the interpolation lives: a model object answering like a fitted
  column for every caller (`query.query`, `suggest.predict_all`, the guard, the resonance rule), or the callers asking for it;
  say which and why in the record.

## 2. Tests (fakes, no EMX)

At a knot the answer equals the knot column's, field for field; between knots the interpolated value and bounds on a fake whose
curve is known; the allowance widens; the domain and resonance rules (a stencil knot out of domain, the next knot above the
resonance margin); `exact=true` equals `main`'s answer; a library row answers its measured value; no fit happens between knots
(count); suggest / region / densify / lib_design through an interpolated column; without a grid everything as `main`.

## 3. Real check (no EMX; needs the grid fitted -- Claude runs `lib.prefit` on the library after N-125 and N-126 are merged and
leaves `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-accept/n126_prefit/prefit.done`; until then work with fakes)

1. N-122's 104 EMX-checked designs at their requirements' frequencies (`/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-accept/
   n122_random_requirements/results_I.json`, `results_T.json`, `results_R.json`; the EMX values are there): predict each with
   the exact column (cached by N-122) and interpolated; against EMX, per quantity: median and max error, coverage, for both.
2. Rerun the 20 N-122 requests (`run_request.py` there, a copy in your scratch directory, writing elsewhere) interpolated: the
   seconds and the leaders against `out/<ID>.json`.
At most 16 threads; read the machine's busy logical CPUs from `/proc/stat` before (another user's batches take up to about 130 of
256; above 150, wait).

## 4. Documents

`docs/em/library.md` ("Asking at any frequency", `exact=`), the ic-opt skill.

## 5. Working rules for the coder

Branch `n127-between-knots` in `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/worktrees/n127-between-knots` (from `main` after N-125
and N-126 are merged; `git merge --ff-only main` first). Same tools and rules as N-125 / N-126; scratch under `<session
scratchpad>/n127/` with `progress.log`. One commit for the change with tests and documents, one for the `## Record`. No merge,
no push, no edit of `BACKLOG_CN.md` or of the real library.

## Record

(to be filled by the coder)
