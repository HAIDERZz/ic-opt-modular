# N-114: one recommendation request across every table of a device kind (2026-10-14)

User, 2026-10-14: "我不太能接受就是我想要某个值的电感和变压器然后我需要推荐范围的时候得到的答案是拒答。那这个推荐功能感觉就没用".

`lib.suggest`, `lib.region` and `lib.pick` each take one `stratum`. A target outside that table comes back as an empty
list, without a reason and without pointing at the table that has it. Measured on 2026-10-14
(`/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-accept/n112_emx_verify/refusal_check/`): 60 GHz, `Lp@60` = `Ls@60` =
60 pH +-8 %, `max:Qp@60` -- `xfm_bs_m10` (port opening 8 um, outer diameters 60-240 um): `lib.suggest` satisfying 0,
`lib.region` 0 points; `xfm_bs_m10_hf` (opening 2 um, 20-60 um): 50 satisfying, 37 of them measured rows. The sixteen
tables together cover wide ranges (`table_coverage.json` there: e.g. the M10/M9 single-turn transformers reach 75-1001 pH
at 60 GHz with opening 8 um and 25-148 pH with opening 2 um).

N-50 (2026-09-27): `lib.suggest`'s pool is the measured rows plus 8192 Sobol points over the whole achieved box; a small
target region (on `xfm_bs_m10`, Lp / Ls / k windows met robustly by about 30 grid points) yields no candidate, while
`lib.region`'s grid finds them.

User decision 2026-10-14 (list items 4 and 5): do the cross-table request, with N-50 folded in. "多表查询" was shelved on
2026-10-07 (restraint batch); this reopens it.

## 1. Interface

- `lib.suggest` and `lib.region` take, instead of `stratum=`, either `family=` or `strata=` (exactly one of the three):
  - `family=ind_sym | xfm_bs | xfm_ms`: every stratum whose `generator` is that family's (`clean_port_ind_sym`,
    `clean_port_xfm_bs`, `clean_port_xfm_ms` in today's library; resolve by the generator name, not by the stratum
    name), filtered by `taps=`;
  - `taps=none` (default) | `any` | `only`: a stratum is tapped when its parts' device carries centre-tap ports (read
    from the part spec: ports `CTP` / `CTS`, or `ct_*` fixed fields -- pick the robust one and say which in the record);
  - `strata=a,b,c`: an explicit list (no tap filter).
- With `stratum=` the call and its answer are unchanged, field for field (existing tests pass untouched).
- `lib.region`'s `out=` (N-106) needs one stratum: with `family` / `strata` it is refused, unless only one stratum has
  points, with a message naming the strata that have points and telling to rerun with `stratum=`.
- The CLI (`ic-opt call lib.suggest LIB family=xfm_bs targets=... objective=...`) prints the JSON as today.

## 2. The family answer

```
{"family": ..., "taps": ..., "strata": {name: <the single-stratum answer, schema unchanged> | {"error": text}},
 "facts": {name: {"generator": ..., "fixed": {the part spec's fixed fields that tell the tables apart: port
          opening(s), metals, ground fixture metal, taps}, "rows": n}},
 "leaders": [... the best n across the strata, each entry the per-stratum entry plus "stratum" and that stratum's
            "fixed" ...],
 "unreachable": {name: {"reason": ..., "ranges": {quantity: [min, max]}, "outside": [...], "closest": {...}}},
 "notes": [...]}
```

- `leaders`: measured designs first, then predicted ones -- as each per-stratum answer orders them -- merged across
  strata by the objective (or by closeness to the targets when there is none); `n` as given. For `lib.region`,
  `leaders` lists per stratum the robust count and ranges, the strata with points first.
- `unreachable`, for each stratum with no satisfying design, says why in one of three ways, computed from the
  stratum's measured rows (the rows that have the quantity under the library's own rules, e.g. the SRF margin):
  (a) a target lies outside the stratum's measured range of its quantity: give the range and the target window in SI
  units ("Lp@60 window 55.2-64.8 pH lies below this table's measured 75-1001 pH"); (b) every target lies inside its
  range but no design meets them together: say so and give the closest measured row (smallest distance in targets
  scaled by their windows) with its values; (c) the stratum cannot take the request (a quantity it does not have, a
  frequency outside its sweeps): the error text. Keep this in one function, so that N-116 (answers outside the measured
  range, if adopted) can change its wording in one place.
- `notes` names, when some strata are unreachable and others are not, which strata have designs ("60 pH at 60 GHz:
  designs in xfm_bs_m10_hf, xfm_bs_ap_hf; xfm_bs_m10 and xfm_bs_ap reach 75 pH at the least").

## 3. N-50: the pool of a small target region

In `suggest`, when no predicted candidate of the Sobol pool satisfies the targets, draw a second pool on the grid of
`region`'s coarse pass for the same targets (reuse `region`'s grid machinery and its `relax`), score it the same way
and mark each candidate's origin (`"pool": "sobol"` or `"region_grid"`). Acceptance: a case where `lib.region` finds a
few dozen robust points and the Sobol pool finds none now yields candidates. Reconstruct the 2026-09-27 case if it is
recorded (search `ic-opt-accept/` and the docs); otherwise build one on the real library and record its targets.

## 4. Tests (fakes, no EMX)

Two small fake strata of one family differing only in a fixed field (an opening): a target reachable in one only ->
leaders from that stratum, labelled, and `unreachable` reason (a) for the other with its range; a target pair that is
each in range but not jointly -> reason (b) with the closest row; a tapped third stratum excluded by `taps=none`,
included by `any`, alone by `only`; `strata=` explicit; `stratum=` answers unchanged; `region` with `family` and `out=`
refused as in 1; the N-50 second pool.

## 5. Real check (read-only on the library; threads <= 8 per call, `OMP_NUM_THREADS=8`)

Library `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-library/n28_v2` with
`IC_OPT_PROFILE_DIRS=/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/EM-opt-workflow/process_data/profiles`. Ten tables are
being pre-fitted until both `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-accept/prewarm_20261014/prefit_A.exit` and
`prefit_B.exit` exist: do not query a table whose `lib.cached` still shows `none` before then (it would fit in the
foreground). Checks: (1) `family=xfm_bs`, 60 GHz `Lp@60` = `Ls@60` = 60 pH +-8 %, `max:Qp@60`: leaders from the opening
2 um tables, reason (a) for the opening 8 um ones; (2) the same with 100 pH: leaders from both openings; (3)
`family=ind_sym`, 80 GHz `Lp@80` 120 pH +-8 %, `max:Qp@80`; (4) the N-50 case. Answers saved under
`/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-accept/n114_family_recommend/`.

## 6. Documents

`docs/em/library.md` (the family request, the answer's fields, the three reasons), the ic-opt skill's library section
and the author-spec skill's flow from pre-simulation targets to `lib.region` (start from `family=`, then write the
combinations of the chosen stratum with `stratum= out=`).

## 7. Working rules for the coder

Branch `n114-family-recommend` in `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/worktrees/n114-family-recommend` (from
`main`; `git merge --ff-only main` first; N-113 changes `query._predict` and `suggest.predict_all` -- merge `main` again
before the record if N-113 has landed, and resolve). `PYTHONPATH=<worktree>/src` with
`/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-modular/.venv/bin/python`; never `uv run` or `uv sync`; `git -C
<worktree>` and absolute paths. Targeted tests only: the files that test `suggest`, `region`, `query`, `pick` /
`index`, the library blocks and the CLI, plus `ruff check src tests`; pytest with `OMP_NUM_THREADS=2` and no `-n`. At most
8 threads in all. Commits: the feature with tests and documents (one or a few), then one for the `## Record` section at
the end of this file. No merge into `main`, no push, no edit of `docs/refactor/BACKLOG_CN.md`. Messages in the
repository's style, ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Record

(to be written by the coder: branch and base commit, the files changed and why, the family resolution and tap
detection chosen, the tests, the real checks' answers in short with their file paths, the N-50 case used, deviations)
