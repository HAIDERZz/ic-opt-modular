# N-111: PACOSYT deployed, fed with our library, compared with our models on the same data (2026-10-14)

The user asked (2026-10-14): deploy PACOSYT (Passos et al., IEEE J. Microwaves 2023; github nunocclourenco/pacosyt),
build its models from our library the way PACOSYT builds them, use the tool, and find out whether it really predicts
and optimizes in milliseconds and whether it is really more accurate than our library. The published numbers cannot be
compared with ours directly (other processes and design spaces, quasi-Monte-Carlo samples against our grid, two test
frequencies, test sets with the near-resonance devices removed). This study puts both on the same data, the same
held-out rows and the same quantities. `docs/refactor/PACOSYT_COMPARISON_CN.md` describes the tool; N-104 / N-107 /
N-108 measured other regressors behind our interface. No EMX, no change to the library or to product code.

Work directory (outside the repository): `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/pacosyt_eval/` -- `repo/` (their
code, never edited), `venv38/` (their environment), `scripts/` (ours), `export/` (our data in their format), `results/`,
`RESULT_CN.md`.

## Phase 1 -- deploy and reproduce their own result (now; at most 4 threads)

- `repo/`: their repository at commit `dee92717b0cb9d8d0668ae59042e298c76250f35`, cloned without the large data blobs
  (`git clone --filter=blob:limit=1m`, then fetch only `data/inductGR_350nm_200GHz.zip` (6 MB) and
  `data/transf_65nm_1_1T.zip` (91 MB)); record the commit.
- `venv38/`: Python 3.8 (`uv venv -p 3.8`; uv downloads the interpreter if needed) with their pinned requirements --
  numpy 1.22.2, scipy 1.8.0, scikit-learn 1.0.2, pandas 1.4.1, matplotlib 3.5.1 (the nearest patch release where a wheel
  is missing, recorded). Not installed: wxPython (the GUI; the server has no display, and its Simulate / Optimize
  buttons call the same `PassivesModel.simulate` / `optimize` / `save` this study calls) and tensorflow (their ANN
  comparison only). Their code runs from `scripts/` with `PYTHONPATH=repo/src`.
- Reproduce, in venv38, with their notebooks' code paths (`data_prepare_tmtt_indGR.ipynb` then `models_tmtt_indGR.ipynb`'s
  "turn and freq independent @ 5GHz SRF > 7GHz" cell: per turns level 1-5, `RBFInterpolatorModel(degree=4)`, inputs
  Din, W, the 5 GHz frequency index, SRF filter 7 GHz): their inductor L and Q maximum and mean errors against the
  paper's Table 5 (RBF, N=1..5: L ME/MAPE 0.16/0.05, 0.11/0.03, 0.18/0.04, 0.15/0.04, 0.08/0.05 %; Q 0.57/0.15,
  0.23/0.06, 0.32/0.05, 0.62/0.08, 0.15/0.05 %) and the notebook's printed output; then the 1:1 transformer subspace at
  28 GHz with SRF > 38 GHz against Table 5's N=1:1 RBF row (Lp 0.35/0.16, Qp 3.77/1.53, Ls 0.24/0.08, Qs 2.2/0.78, k
  0.45/0.21). Build their inductor tool model as their notebook pickles it and time one `PassivesModel.simulate` (every
  frequency point) and one `PassivesModel.optimize` (e.g. `freq=5, constraints=[['l', 2.0, 5.0]], objectives=['q']`).
- Report to the coordinator: the deployment record, the reproduction against the paper, the timings. Then wait for the
  coordinator's message before Phase 2.

## Phase 2 -- our library through their method (after the coordinator's go; at most 32 threads)

### 2.1 Our data in their form
Tables, the library as N-109 leaves it (`/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-library/n28_v2`, read only):
`ind_sym_m10` (port opening 8 um), `ind_sym_m10_hf` (2 um), `xfm_bs_m10`, `xfm_bs_m10_hf`, `xfm_ms_m10`. For every row
the library models (`query.Library(root, calibrate=False, cache_dir=<scratch>).dataset(stratum).rows`): its geometry,
its sNp (read with `ic_opt.em.touchstone.read`; find how the library locates a row's sNp), the frequency grid (check it
is 0-250 GHz in 1 GHz steps for every row; record any that is not), and the library's column values (the truth). Export
per table to `export/<table>.npz` (geometry, complex S [rows, freqs, ports, ports], frequencies, truth) for their side.
- Their parametrization: inductors per turns level, inputs inner diameter Din and width W (and spacing where it varies
  within a level); Din from our generator's definition (state the formula, verify it on a few rows against the GDS or the
  manifest). xfm_bs: Dinp, Wp, Dins, Ws plus our centre offset (our space has it, theirs has not); their "balun"
  subspace (windings overlapping: `douts > dinp - 10` in their code) translated to our geometry -- state the rule;
  one subspace otherwise (1:1). xfm_ms: subspaces by the secondary's turns, inputs Dinp, Wp, Dins, Ws, the secondary's
  spacing, the offset. 4-port S in our order P1 N1 P2 N2 is their order (P1 - P2 primary, P3 - P4 secondary).
- Variant P2: the same modelling with the library's own input coordinates (the feature map where the library uses one
  for that table's SRF and k; raw dims for the inductors), to separate "S-parameters per frequency" from "coordinates".

### 2.2 Their modelling, faithfully (in venv38, their `src/models/models_scipy.py` classes)
- P1 whole sweep (their released configuration): per subspace and per frequency point (all 251), one
  `RBFInterpolatorModel(degree=d)` on the RI values (8 for the 2-port inductors, 32 for the 4-port transformers);
  d as their production notebooks: inductor 4, transformer 5 (4 in the balun subspace). Where a fit fails or is
  singular on our grid data (N-104 saw this for degrees 3 and 5), step d down (3, 2, 1) and record the degree used per
  subspace. Derive Lp_lf, the Q peaks and SRF from the predicted sweep with our measure kernel
  `ic_opt.em.measure.quantities(freqs, s, topology)` (the topology the library measures that table with).
- P1 at the anchors (their Strategy IV): per anchor f0 in {28, 112} GHz, the f0 model trained only on training rows with
  SRF > 1.25 f0 (our margin, the role of their f0 + delta f), predicting the test rows with SRF > 1.25 f0 (the rows our
  anchored columns hold); derive Lp@f0, Qp@f0, Ls@f0, Qs@f0, k@f0 with the measure kernel. Their SRF model
  (`RBFInterpolatorModel(degree=2)` inductor / 3 transformer) on the training rows' SRF, scored too.
- P3, the user's question "one GP per frequency point": at the anchors only, one sklearn GP per subspace and anchor on
  the RI values (multi-output, the library's kernel `ic_opt.library.gp._kernel`, `normalize_y`, 4 restarts), same
  filtering, same derivation.
- Ours: the library's model of each column (the settings `Library._fit_inputs` and the composed settings give), refitted
  on each fold's training rows -- reuse N-107's harness (`docs/refactor/reports/library_query/regressor_study.py`) where
  it fits.

### 2.3 Folds and scores (the same rows for every model)
- Table-level folds: (i) random 20 % of the table's rows, seeds 0-4 (`gp.split` on the row indices); (ii) one
  outer-diameter level held out (primary OD for inductors and xfm_bs, secondary OD for xfm_ms; up to 6 levels with at
  least 25 rows strictly inside the range, spread evenly).
- Every column scored on the fold's test rows that hold a value: median, p90, max relative error; SRF also how often a
  resonance is predicted where there is none or missed. In their terms too: MAPE and maximum error of L and Q at the
  anchors with their definitions (`em.compute_all_lq`) for their models, and the same statistics of our Lp@f0 / Qp@f0;
  first check on the true S that our columns equal their definitions (the largest relative difference over all rows).
- Verdicts per column and protocol, N-104's rule: ours vs P1, ours vs P2, ours vs P3.

### 2.4 Speed and synthesis, as their tool is used
- Build time per table (every frequency model) for P1; our GP fit time per column.
- Prediction time per device: their `PassivesModel.simulate` (the full sweep) and one frequency, with their pickled
  model dict built exactly as their notebook builds it, for `ind_sym_m10` and `xfm_bs_m10`; ours: one GP `predict` and
  `ic-opt call lib.query` on one geometry with a warm cache.
- Synthesis: their `PassivesModel.optimize` (scipy differential evolution, their code untouched) on (a) the 80 GHz
  inductor of the region demonstration (L 120 pH +-8 %, maximise Q; their units nH and GHz) and (b) a single-turn
  transformer at 60 GHz in a window the table holds (e.g. Lp and Ls 100 pH +-8 %, maximise Qp + Qs); wall time, the
  geometry returned, its prediction by their model and by our library (`lib.query`), the nearest measured rows; against
  our `lib.suggest` and `lib.region` on the same targets (wall time, top candidates). No EMX: list the five largest
  disagreements between the two tools on proposed geometries, for the user to decide whether to check them with EMX.

### 2.5 Deliverables and rules
- `pacosyt_eval/RESULT_CN.md` in plain Chinese for a reader who does not know the field's statistics: every statistic and
  protocol explained in one sentence where it is first used ("随机藏起 20% 的行" / "把某一档外径的行整档藏起" /
  "九成误差: 90% 的行不超过的误差"); the deployment record; the reproduction against the paper; per table and column the
  numbers and verdicts; the speed table; the synthesis comparison; the limitations (our grid sampling against their
  quasi-Monte-Carlo sampling, the degrees that had to step down, the definitions, the transformer offset input).
  Scripts in `scripts/`, raw results in `results/`. The `## Record` of this file, on branch `n111-pacosyt-eval`
  (worktree `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/worktrees/n111-pacosyt-eval`, from `main`), one commit, message in
  the repository's style ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`; no merge, no push, no
  BACKLOG edit, nothing under `src/`.
- Resources: Phase 1 at most 4 threads; Phase 2 at most 32 threads in all (`OMP_NUM_THREADS=1` per worker, at most 32
  workers): another study uses the other 32 of the user's 64. Pilot first, estimate in `results/run.log`; above 3 hours
  drop P2 first, then `xfm_ms_m10`, then levels to 4, and say so. Long runs detached (`setsid`, a `DONE` file), polled
  with sleeps; never match your own shell with `pgrep -f`. Absolute paths and `git -C` everywhere.

## Record

Done 2026-10-14 outside the repository; no EMX, no product code, the library only read (no file under `n28_v2` newer than
the start of phase 2; every cache in `pacosyt_eval/cache/`). Full write-up (Chinese), scripts, raw results:
`/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/pacosyt_eval/RESULT_CN.md`, `scripts/`, `results/`, `export/`.

- **Deployment.** `repo/` at `dee92717b0cb9d8d0668ae59042e298c76250f35`, blob-filtered clone with a sparse checkout of the
  two zips (work tree untouched). `venv38`: CPython 3.8.20 (uv), numpy 1.22.2, scipy 1.8.0, scikit-learn 1.0.2,
  pandas 1.4.1, matplotlib 3.5.1, every pin exact; wxPython, tensorflow, ipykernel not installed. Their `src/` ran
  unchanged (their one unit test passes); notebook cells were executed from the `.ipynb` with logged patches (paths, the
  ANN removed, the transformer on its 1:1 zip) and one runtime alias (`models.test_it_srf`, defined in
  `train_test_util.py` but not exported by `models/__init__.py`, which cells 14 / 22 call).
- **Reproduction.** The notebooks' saved outputs reproduce to the digit with the notebooks' own RBF class. Table 5 is
  reproduced by the tool's class (`models_scipy.RBFInterpolatorModel`, degree 4): 10 / 10 numbers of the 1:1 transformer
  row, 19 / 20 of the inductor rows (N=4 Q max error 0.27 % against 0.62 %; 0.61 % without the SRF filter). Their tool
  on their inductor model: `simulate` 7.0 ms (203 points), one frequency 0.034 ms, `optimize` 0.13 s.
- **Phase 2 data.** Five tables (527 / 114 / 1576 / 635 / 1400 rows), every row 0-250 GHz by 1 GHz (251 points, 50 ohm, no
  exception); port order P1 N1 P2 N2 as theirs, the secondary driven (N2, P2) so their k has the opposite sign. Din =
  OD - 2 N W - 2 (N - 1) S (secondaries likewise) checked on 11 GDS files to the 0.005 um grid. xfm_bs subspaces by their
  test `douts > dinp - 10` with douts = OD_s: xfm_bs_m10 1308 "balun" / 268 "1:1", every offset row in "balun".
  Columns: 7 per inductor table, 16 per transformer table; xfm_ms_m10's 112 GHz columns not scored (2 rows hold them).
- **Degrees.** Starting at 4 (the coordinator's decision for both device kinds), stepped down where the monomial matrix of
  the standardized inputs is rank-deficient: single-turn inductors 4, multi-turn 2 (spacing has 3 levels), xfm_bs 1:1 3
  (P2 4), every overlapping xfm_bs subspace and every xfm_ms subspace 1 -- the offset was swept only on identical
  windings, which makes offset x (W_p - W_s) and offset x (D_p - D_s) exact collinearities.
- **Verdicts** (N-104's rule on the rows both scored; library better / tie / theirs better): protocol (i) against P1
  53 / 3 / 0, P2 50 / 4 / 2, P3 30 / 2 / 1; protocol (ii) 43 / 2 / 5, 31 / 12 / 7, 23 / 5 / 1. Their method on our data in
  their own terms (MAPE at 28 GHz): ind_sym_m10 L 0.84 %, Q 43 %; xfm_bs_m10 Lp 3.4 %, Qp 445 % (Table 5: 0.03-0.05 % /
  0.05-0.15 %; 1:1 Lp 0.16 %, Qp 1.53 %); the two `_hf` tables come close to the paper. Their side better: Lp_lf / Ls_lf on
  (ii) (ind_sym_m10, xfm_bs_m10), xfm_bs_m10 Ls@112 on (ii), xfm_bs_m10_hf Qp@28 / Qs@28 (P2), xfm_ms_m10 SRF on (ii).
  Definitions: their `compute_all_lq` on the true S equals our columns within 0.1 % (k after the sign).
- **Speed.** Their build of a whole table (every frequency) 0.09-18.2 s; `simulate` 8.9 / 13.8 ms, one frequency 0.035 /
  0.053 ms, `optimize` 0.22 / 0.65 s. Library: one GP predict 0.094 / 1.0 ms, an in-process query 1.0 / 3.8 ms,
  `ic-opt call lib.query` 0.58-0.98 s, `lib.suggest` 0.97 / 26.4 s, `lib.region` 0.33 / 23.5 s; a big table's column
  fit 72-124 s per fold. Compute: 5.6 CPU-h (library + P3) + 0.12 (theirs) on 32 workers x 1 thread, 02:40-02:51.
- **Synthesis.** (a) 80 GHz inductor: both tools land on single turns, OD 74-81 um, W 8-10 um; L within 1 %, Q 0.1-6 %
  apart, theirs higher; their optimum (OD 77, W 10) Q 32.6 above both measured neighbours (29.95 / 31.18), ours 30.95.
  (b) 60 GHz transformer: their tool represents only identical stacked windings without offset and nested ones (1276 of
  xfm_bs_m10's 1576 rows outside it); its optimum, identical windings OD 51 um, lies outside our measured range (refused
  by the library) with Qp ~ Qs ~ 35 claimed against 15-17 measured at OD 60; of our 13 proposals it refuses 9 (primary
  inner diameter below the secondary's) and answers 4 offset ones as offset-free (k 0.64 against 0.19-0.32 measured).
  Five largest disagreements (unmeasured, both tools): (a)'s Q, 4.0-6.1 %; plus (b)'s out-of-range optimum, for the user.
- **Deviations.** Degree 4 for transformers too (coordinator); their tool's transformer dict keeps their structure
  (`_balun` = our identical-winding rows, inputs Dinp, Wp; `""` = nested rows, no offset input) while the study's P1 uses
  both subspaces with five inputs; an adapter outside their code (`pacosyt_shim.DropConstant`) drops inputs constant in a
  subspace; SRF "above the sweep" scored at 250 GHz; their tool's SRF filter 1.25 x the design frequency (100 / 75 GHz);
  our own fold runner rather than N-107's harness (folds per table, not per column; composed parts shared within a fold,
  the same rows `composed.holdout_fold` uses); our `lib.suggest` objective Qp@60 alone (one quantity).
- **Not foreseen.** The grid's collinearities above; Q peaks from a predicted sweep spike where the predicted loss nears
  zero (one row: 20 240 against 19.5); the library's xfm_bs_m10 Lp@112 / Ls@112 on the OD-120 hold-out (median 47 %: the
  composed resonance model's SRF part runs low between levels and the rise factor saturates); a first launch lost 8
  their-side jobs to an anchor subspace with no varying input (fixed, relaunched, caches reused); the library's fit
  processes use about 1.6 cores each with OMP_NUM_THREADS=2.
