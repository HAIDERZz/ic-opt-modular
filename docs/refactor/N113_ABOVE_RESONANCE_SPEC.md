# N-113: no curve answer above the device's own resonance (2026-10-14)

Found by N-112 (`/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-accept/n112_emx_verify/RESULT_CN.md`, finding 3).
`lib.query` on `xfm_bs_m10_hf` at primary/secondary outer diameter 51/51 um, width 4/4 um, offset 0 answers, with status
`predicted` and a relative sigma of 0.3-0.4 %:

| column | answer | EMX at that geometry |
| --- | --- | --- |
| `SRF` | 162.8 GHz [162.7, 163.0] | 162.9 GHz |
| `Lp@170` | 307 pH | past the resonance: the L curve is negative (-614 pH) |
| `Qp@170` | 10.1 | negative |
| `k@170` | 1.022 [1.013, 1.031] | k above 1 is impossible |
| `Lp@140` | 260 pH | the curve reads 259 pH, but a measured row with this SRF has no `Lp@140`: 162.9 GHz <= 1.25 x 140 GHz |

Two mechanisms let these through. A `model: resonance` curve holds (f0 / SRF)^2 at `composed.U_MAX` (the rise stays
at 5) and relies on the SRF's uncertainty to widen the interval until the confidence gate flags it; a confident SRF
(as here) leaves a narrow interval. A direct curve model (`k@170`) is guarded by the achieved box and hull of every row
that has the column, which here includes other widths and offsets at outer diameters up to 60 um, while the rows of
this family (same widths, no offset) have `k@170` only up to 40 um.

User decision 2026-10-14 (list item 3): fix it, and before any answer outside the measured range is allowed (N-116).

## 1. The rule

A curve column `<curve>@<f>` (a declared anchor or an extension column) is answered at a point only where the
resonance predicted there lies above `srf_margin x f` -- the condition under which a measured row has a value for that
column (`dataset._anchor_value`). The resonance is the stratum's system `SRF` when the stratum declares it; otherwise
per drive: `SRF_p` for `Lp` / `Qp`, `SRF_s` for `Ls` / `Qs`, both for `k` -- the choice `suggest.implied_srf` already
makes. `srf_margin` is the curve's own (`stratum.quantities[curve].srf_margin`). SRF is fitted in GHz and answered in Hz
(`query.fit_unit`): compare in Hz.

## 2. What changes

1. `ic_opt/library/query.py`, `_predict` (so `lib.query`, `lib_signoff`'s predictions and every caller of `query`): for a
   curve column, first take the resonance answer at the point through the same `_predict` path (so `above_sweep` and an
   exact measured row behave as today), then:
   - resonance `above_sweep` whose `lower_bound` is at or above `srf_margin x f`: answer the curve as today;
   - resonance `predicted` or `uncertain` with value at or below `srf_margin x f`: the curve's answer is the new status
     `above_resonance`, without `value` / `lo` / `hi`, with `srf` (the resonance answer's `value`, `lo`, `hi`),
     `threshold_hz` (= `srf_margin x f`), `frequency_hz`, `srf_margin`, `unit`, `nearest` (as `out_of_domain` gives it)
     and `reason`: "the resonance predicted here (<X> GHz) is at or below srf_margin x f (<Y> GHz): a measured row
     there has no value for this column";
   - resonance value above the threshold but its lower bound at or below it: answer the curve as today and add
     `near_resonance: {"srf_lo_hz": ..., "threshold_hz": ...}`; the status does not change;
   - resonance `out_of_domain`: the curve's answer is `out_of_domain` with the resonance model's criterion and nearest
     rows, its reason prefixed `"resonance model: "` (the rule cannot be checked, so the answer fails closed).
   An exact measured row answers as today (its value is already empty where the rule cut it).
2. `ic_opt/library/suggest.py`, `predict_all` (the gate of `lib.suggest`, `lib.region`, `lib.densify`, `lib_refine`,
   `lib_design`): for every anchored column among the models it predicts, a row whose predicted resonance (mean) is at
   or below `srf_margin x f` is not ok -- as an out-of-domain row is -- whether or not SRF is among the targets.
   Rows settled above the sweep (`srf_floors`, `_settled`) pass. Targets and objectives already get the robust
   version of this through `implied_srf`; this closes trends and any caller that predicts an anchored column without
   it. An answer that `implied_srf` already constrained must not change: say in the record whether any test answer
   changed, and why.
3. Every other place under `src/ic_opt/library` and `src/ic_opt/recipes` that predicts a curve column (grep for
   `predict_all`, `.gp.predict`, `query(`): apply the rule, or say in the record why it does not need it.
4. `lib.coverage` / `lib.load` / the index / `lib.pick`: unchanged (measured rows only).

## 3. Tests (fakes, no EMX)

(a) a `model: resonance` curve at a point whose predicted SRF is below `srf_margin x f` -> `above_resonance`, no value,
the fields of 2.1; (b) predicted SRF above the threshold, lower bound below -> `predicted` with `near_resonance`;
(c) SRF `above_sweep` -> the curve is answered; (d) a direct `k@f` model at such a point -> `above_resonance`; (e) a
stratum without system SRF uses `SRF_p` / `SRF_s` per drive (both for `k`); (f) the SRF model out of domain -> the curve
`out_of_domain` with the prefixed reason; (g) `predict_all` drops the rows for an anchored trend quantity; (h) existing
`suggest` / `region` answers with anchored targets are unchanged.

## 4. Real check (read-only on the library; its models are cached, nothing is fitted)

`lib.query` on `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-library/n28_v2`, stratum `xfm_bs_m10_hf`, primary and
secondary outer diameter 51, widths 4, offset 0: `Lp@170`, `Ls@170`, `Qp@170`, `Qs@170`, `k@170`, `Lp@140` are
`above_resonance`; `Lp@60`, `Qp@60`, `k@60`, `SRF` give the values they give on `main` (bit for bit). Run with
`IC_OPT_PROFILE_DIRS=/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/EM-opt-workflow/process_data/profiles` and
`OMP_NUM_THREADS=4`; write the before / after answers into the record.

## 5. Documents

`docs/em/library.md` where the answer statuses are listed (add `above_resonance`, `near_resonance`); the ic-opt and
author-spec skills where they list `lib.query`'s statuses, if they do.

## 6. Working rules for the coder

Branch `n113-above-resonance` in `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/worktrees/n113-above-resonance` (from `main`;
`git merge --ff-only main` first). `PYTHONPATH=<worktree>/src` with
`/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-modular/.venv/bin/python`; never `uv run` or `uv sync`; `git -C
<worktree>` and absolute paths. Targeted tests only: the files that test `query`, `suggest`, `region`, `composed`,
`densify`, `lib_signoff`, `lib_refine`, plus `ruff check src tests`; pytest with `OMP_NUM_THREADS=2` and no `-n`. At most
8 threads in all. One commit for the fix with its tests and documents, one for the `## Record` section at the end of
this file. No merge, no push, no edit of `docs/refactor/BACKLOG_CN.md`. Messages in the repository's style, ending
with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Record

(to be written by the coder: branch and base commit, the files changed and why, the tests added and changed, the real
check's before / after answers, every place found in 2.3 and what was done there, deviations from this specification)
