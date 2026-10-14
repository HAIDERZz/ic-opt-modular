# N-116: answers a little outside the measured range along the outer diameters, labelled (2026-10-14)

User, 2026-10-14: "预测和推荐功能本质上就是要用模型去做外推的功能" and, after N-115, "第 2 项：范围外带标注的外推，做不做。做".

Today the domain guard refuses every point outside the achieved per-dim box (criterion 1) or outside the hull of its level
(criterion 3), whatever the model says. N-115 (`/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-accept/n115_extrapolation_study/RESULT_CN.md`,
tables_CN.md table 1b) measured, per dim, what the models give one and two level steps outside the range:

- along the outer diameters -- the inductor's, the transformer's primary and secondary -- one step (10-25 % of the range) gives
  p90 L 0.5-1.7 %, k 0.2-1.3 %, Q 3.1-4.4 %, SRF 6-9.5 %, calibrated 2 sigma coverage 94-100 %; two steps p90 roughly doubles,
  coverage 89-100 %;
- along the widths coverage falls to 75-89 % (the model's sigma too small), along the spacing (3 levels) p90 is 50-160 %, the low
  side of the offset is concentric devices predicted from off-centre ones (p90 20-50 %);
- sigma / mu alone cannot be the gate (Q2); criterion 3 alone refuses every edge row (table 6).

Decision: extrapolate along the outer diameters only, a bounded distance, answers labelled; every other dim refuses as today.

## 1. The rule

- **Dims**: `outer_diameter_um`, `primary_outer_diameter_um`, `secondary_outer_diameter_um` -- whichever the stratum has. Nothing
  else (widths, offsets, spacings, turns) is extrapolated.
- **Allowance per side**, for each such dim of a model, from that model's own rows: `min(F x range, F x boundary)` beyond the
  achieved minimum and beyond the maximum, with `range` = max - min and `boundary` = the min (low side) or the max (high side),
  `F = 0.15` by default (`EXTRAPOLATE_FRACTION`). On `xfm_bs_m10` (50-240 um after N-117): down to 42.5 um, up to 268.5 um; on an
  `_hf` table (20-60 um): 17 and 66 um. The relative bound keeps the small end conservative (one step at the low end of a base table
  was 25 % of the boundary).
- **Hull**: a point beyond the range along those dims, within the allowance, passes criterion 3 when the point with those dims
  clamped onto the achieved range passes it (the device is a scaled version of one the data surround). A point inside the range
  keeps today's hull test.
- **Criterion 2** (turns level) and **criterion 4** (sigma / mu ceiling, `uncertain`) unchanged; the N-113 resonance rule unchanged
  (the resonance itself may be extrapolated).
- **Intervals**: from N-115's edge rows (`edge_rows.csv.gz`, the outer-diameter dims only), measure the calibrated 2 sigma coverage
  of each column class at the distances inside the allowance; where a class is below 0.95, widen the interval of extrapolated
  answers by a factor that grows with the distance (e.g. k x (1 + a x d / allowance)), with `a` fitted per class so that the
  coverage reaches 0.95; record the factors and their evidence. Answers inside the range are unchanged bit for bit.

## 2. Answers

- `lib.query`: an extrapolated answer keeps its status (`predicted` / `uncertain`) and gains `extrapolated: {dim: {"side":
  "below" | "above", "beyond": <um>, "allowance": <um>, "measured_range": [lo, hi]}}`; a point beyond the allowance, or beyond the
  range along any other dim, is `out_of_domain` as today, with the reason saying which dim and how far, and the allowance where one
  applies.
- `suggest.predict_all` (and through it `lib.suggest`, `lib.region`, `lib.densify`, `lib_refine`, `lib_design`): rows within the
  allowance are in domain and carry the label; `lib.suggest`'s and `lib.region`'s pools and grids extend along those dims by the
  allowance, so designs just outside the measured range can be proposed; each such design is marked extrapolated, and measured
  designs still come first. `lib.region`'s ranges say which part lies beyond the measured range.
- The family request (N-114): a table whose designs are extrapolated has designs (marked); reason (a) of `unreachable` says
  "even with up to <F> extrapolation of the outer diameter" when it still applies.
- `lib_signoff`: extrapolated candidates are predicted and scored like the others; its report carries the label.
- Off switch: `extrapolate=0` (a call parameter of `lib.query`, `lib.suggest`, `lib.region`, `lib.densify`, the CLI blocks) gives
  today's answers exactly; another fraction replaces `F` for that call. No manifest field (a stratum's dump is part of its dataset
  key: editing `library.yaml` would refit every model).
- **Do not change `src/ic_opt/library/gp.py` or `composed.py`**: their source is part of every cached model's key, and a change
  would refit every model of every table (hours). The guard lives in `domain.py`, the answers in `query.py`, `suggest.py`,
  `region.py`, `family.py`.

## 3. Tests (fakes, no EMX)

A fake stratum with outer diameters 60-120 and widths 4-8: a point at 55 (inside the allowance) answered with the label and the
widened interval, at 40 refused with the allowance in the reason, at width 3 refused as today; a transformer stratum with both
outer diameters below their minima; the hull clamp (a point beyond the range whose clamped point is outside the hull: refused);
`extrapolate=0` equal to today's answers field for field; `predict_all` / `suggest` / `region` proposing a design beyond the range,
marked; the N-113 rule on an extrapolated point; the family reason wording.

## 4. Real check with EMX truth and no EMX

N-117 adopted 160 rows into `xfm_bs_m10`; before that, 13 of its 40 independent test points (outer diameters below 60 um) were
refused out of domain. Their EMX values are in
`/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-accept/n117_space_filling/lib_signoff_before.json` (test list `batch/points_test.json`).
Build a scratch library root whose `xfm_bs_m10` part holds the pre-N-117 observations
(`<library>/xfm_bs_m10/.icopt/observations.jsonl.bak-n117-20261014T075753Z`, the other files and `sims` by symlink; every other
part by symlink) and open it with `cache_dir` = the real library's `.cache`, where the old models (dataset key
`0a70b174ed75f2947bd7`, kept on purpose) still are: nothing is fitted -- stop and report if anything starts fitting. Query the 40
test points with extrapolation on and off: which formerly refused points are answered, their errors against EMX and the interval
coverage, per column class; the in-range answers must be identical on and off. Also on the live library: `lib.query` on
`xfm_bs_ap` at outer diameters 51 / 51 um, widths 4 / 4, offset 0 (labelled, no truth), and `lib.suggest family=xfm_bs` for 60 GHz
`Lp@60` = `Ls@60` = 60 pH +-8 %, `max:Qp@60` (which tables now have designs, marked). Evidence:
`/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-accept/n116_extrapolation/`. At most 8 threads.

## 5. Documents

`docs/em/library.md` (the domain section, the new field, the allowance, `extrapolate=`), the ic-opt and author-spec skills where they
describe refusals and recommendation ranges.

## 6. Working rules for the coder

Branch `n116-extrapolation` in `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/worktrees/n116-extrapolation` (from `main`; `git merge
--ff-only main` first). N-120 is developed at the same time and touches `query.py` (`Library.models`, `cached`, `_predict`): merge
`main` again before your record if it has landed, and resolve. `PYTHONPATH=<worktree>/src` with
`/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-modular/.venv/bin/python`; never `uv run` or `uv sync`; `git -C <worktree>` and
absolute paths; `IC_OPT_PROFILE_DIRS=/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/EM-opt-workflow/process_data/profiles`. Targeted
tests only (query, suggest, region, family, densify, signoff, refine, resonance, domain, docs, CLI) plus `ruff check src tests`;
pytest with `OMP_NUM_THREADS=2`, no `-n`; at most 8 threads in all. The library is read-only: never write its `library.yaml`, its
part stores or its `.cache`. Commits: the feature with tests and documents (one or a few), then one for the `## Record` section
below. No merge into `main`, no push, no edit of `docs/refactor/BACKLOG_CN.md`. Messages in the repository's style, ending with
`Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Record

(to be written by the coder: base commit, files and why, the widening factors and their evidence, tests, the real checks with
numbers and file paths, deviations)
