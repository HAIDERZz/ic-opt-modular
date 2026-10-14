# N-117: a space-filling batch on one table, measured against the grid (2026-10-14)

User, 2026-10-14: "为什么我感觉这是我们的样本库建立的不合理导致的？" The library's tables are grids with few levels per size
dim (`xfm_bs_m10`: 1576 rows, 8 primary outer diameters, 4 widths per winding; the multi-turn tables 3 widths), so the models
are weakest between levels and at the edges (N-110: direct models' median error 7-23 % per held-out diameter level, where a
random hold-out shows little). The same number of scattered points would give every dim hundreds of distinct values.
User decision 2026-10-14 (list item 7, with item 10 folded in): add points by space-filling sampling, first on one table,
after N-115 says where the models are weakest; EMX approved in principle: about 200 points on one table, 8 threads per job,
at most 64 threads in all.

## 1. The table and the box (fixed after N-115)

Default, unless N-115 points elsewhere: `xfm_bs_m10` (M10 / M9, port opening 8 um), over its own box extended below its
smallest outer diameters to 50 um (the gap N-112's OD 51 fell into), widths 4-10 um, offsets 0 to the table's maximum.
N-111's weak spot 1 (low-frequency inductance between levels) and N-19's offset band are inside this box.

## 2. Points

- Sample in the coordinates the table's models use where it has a feature map (`xfm_bs_dimensionless`: the mean outer
  diameter, the outer-diameter ratio, the widths, the offset relative to the mean diameter), each within the range the
  table's rows span (the diameter extended to 50 um), then map back to the dims: a box in the raw dims draws many shapes
  the table never holds (a trial draw on 2026-10-14 gave OD 112 / 203 um with a 61.5 um offset; 2 of 6 refused for an
  offset beyond (OD_P + OD_S) / 4). `draw_points.py` in the evidence folder draws in the raw dims today; extend it.
- 200 geometries: scrambled Sobol over the box (seeded, recorded), snapped to the manifest's steps, existing rows and
  duplicates dropped, each drawn first with the real generator (`suggest.build_check`: configuration, geometry, product DRC
  gate) and a refused one replaced by the next Sobol point, so the 200 all build.
- A random 40 of them, chosen before any EMX and recorded, are an independent test set: simulated, never adopted until the
  comparison of section 4 is written.

## 3. Runs

- `lib_signoff` (`adopt=false`) on all 200 in one project, with the part's own EMX settings (8 threads, 16 GB, its process
  file and mesh: the library's generation); 8 jobs at once (64 threads) only when nothing else runs, else fewer, keeping the
  total at 64. `--plan` first.
- Then `lib_signoff adopt=true` on the 160 in the same project: the EMX results are cached per geometry, so nothing is
  simulated again; the rows join the part store with origin `signoff:<project>:<obs>`.
- The table's models refit on first use afterwards (about 20-25 minutes at 24 threads for this table); pre-fit them with the
  pre-warm script (`ic-opt-accept/prewarm_20261014/prefit.py`).

## 4. Measured

- At the 40 test points: the library's answers before adoption (from the first `lib_signoff` report) against after, against
  EMX: median and p90 error and 2 sigma coverage of `Lp_lf`, `Ls_lf`, `k_lf`, `SRF`, `Qp_peak`, `Qs_peak` and the 28 / 60 GHz
  curves; the points below 60 um apart (they were outside the table's range before).
- Leave-one-diameter-level-out p90 before and after (N-115's tooling), on the grid levels: does the scattered batch lower the
  error between levels.
- A sentence for the user: what 160 scattered points bought, and whether the next additions to any table should be scattered.

## 5. Evidence and records

`/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-accept/n117_space_filling/` (the points, the two `lib_signoff` reports,
the comparison, `RESULT_CN.md` in plain Chinese); `library.yaml` is not edited (the rows join the existing part); the
library's adopted log records the batch. A `## Record` section below.

## Record

Run by Claude on 2026-10-14 (13:55-16:30). Started before N-115 reported, at the default of section 1, when the user raised the
cap to 80 threads ("现在总占用只有32线程，提高并行任务数量，当前最高允许80线程") and asked for more parallel work; adoption waited
until N-115's runs had ended and its report read a copy of the datasets.

- Table `xfm_bs_m10`; 1576 rows before, 1736 after. Points (`draw_points.py`): two parts drawn in the models' coordinates -- 40 with
  mean outer diameter 50-62 um (ratio 0.8-1.25) and 160 over the table (mean 50-246 um, ratio 0.5-2), per-winding limits OD_P 50-240,
  OD_S 48-252 um; widths 4-10 um; cs_rel in [-0.25, 0.5] with the negative part concentric (66 of 200 concentric); snapped to the
  manifest's steps; every point built by `suggest.build_check` (52 refused and replaced). A first draw without the per-winding limits
  reached OD_P 318 um and was discarded (`batch_rejected_box_overflow/`). 200 points: 105 distinct primary outer diameters (the grid
  has 8), 61 distinct primary widths (4), 15 with both outer diameters below 60 um.
- EMX: `lib_signoff adopt=false`, the part's settings (8 threads, 16 GB), 5 at once, 200/200 ok (14:16-15:47). Adoption: `lib_signoff
  adopt=true` on the 160 in the same project, the EMX results taken from its per-geometry cache (no EMX), origin
  `signoff:signoff:<obs>`; the part's `observations.jsonl` backed up first (`.bak-n117-20261014T075753Z`). Refit of the table's 43
  models at 64 threads: 1390 s. `library.yaml` not edited (its note is part of the dataset key).
- At the 40 test points (not adopted), before -> after (`compare.py`; `before_test40.txt`, `after_test40.txt`): inside the range
  (27 points) the answered count rose (Lp_lf 19 -> 23, Qp@60 10 -> 20, k@60 13 -> 20; the uncertain ones answered) at the same error
  level (medians 0.3-2.7 %, p90 1.1-8.6 %, coverage 0.78-1.00); below 60 um (13 points) from 1-2 to 5-7 answered (medians
  0.1-1.6 %, p90 0.6-3.7 %, coverage 0.71-1.00). Before adoption the library was already accurate between levels where it answered
  (medians 0.4-1.7 %); the grid's cost on this table is coverage -- answers refused by the range and hull tests -- not accuracy.
- Not done: section 4's leave-one-level-out before / after; the test points lie between the grid levels and answer that question
  directly. Evidence and the user's summary: `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-accept/n117_space_filling/RESULT_CN.md`.
