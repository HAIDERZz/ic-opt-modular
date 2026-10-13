# N-109: the `_hf` parts become strata of their own -- one table, one opening (2026-10-14)

The investigation `ic-opt-accept/n109_duplicate_rows/REPORT_CN.md` found the single cause of N-108's duplicate
geometries: the second-generation library's `_hf` parts (the high-frequency extension, outer diameters up to 60 um)
were built with a port opening of 2 um (`HF_OPENING = 2.0` in `t19_build/build_v2.py`), the base parts with 8 um;
everything else (geometry generation 7, fixture metal, EMX settings, sweep) is identical. The opening is a fixed field
of the device, not a dim the library models on, so two devices share one modelling coordinate: Lp_lf 4-7 % lower,
Q peak 9-19 % lower, SRF 7-10 % lower in the `_hf` row of every one of the 32 duplicate pairs (all at OD 60 um), and
every `_hf` row (2 300 in eight parts) mixes into its base table. The user decided on 2026-10-14: split the `_hf`
parts into strata of their own (no row deleted, no EMX), the convention of the library (one stratum = one device
with one set of fixed fields; parts differ only in EMX settings or sweep).

## 1. What changes (the private library only; `library.yaml` of `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-library/n28_v2`)

- Back up `library.yaml` to `library.yaml.bak_<UTC timestamp>` first (as the earlier manifest changes did).
- For each stratum that holds an `_hf` part, move that part into a new stratum named `<stratum>_hf` with the same
  `generator`, `dims`, `nt_dim`, `steps`, `quantities` (anchors, `model`, `feature_map`, `rel_sigma_max`, bands) and
  `low_freq_max_hz` as the base stratum, a `note` saying the parts' port opening is 2 um and the base stratum's 8 um,
  and the base stratum's `note` saying the same the other way. The eight: `ind_sym_ap` -> `ind_sym_ap_hf`,
  `ind_sym_m10` -> `ind_sym_m10_hf`, `xfm_bs_ap` -> `xfm_bs_ap_hf`, `xfm_bs_m10` -> `xfm_bs_m10_hf`, `xfm_ms_ap` ->
  `xfm_ms_ap_hf`, `xfm_ms_m10` -> `xfm_ms_m10_hf`, `xfm_bs_ap_ct` -> `xfm_bs_ap_ct_hf` (part
  `xfm_bs_ap_ct__xfm_bs_ap_hf`), `xfm_bs_m10_ct` -> `xfm_bs_m10_ct_hf` (part `xfm_bs_m10_ct__xfm_bs_m10_hf`).
  Keep every other part where it is (the `_nt1` parts stay with their base stratum: same opening). Preserve the file's
  comments (edit the text, or re-dump and copy the header comment back; say which).
- Expected row counts afterwards (from the investigation): `ind_sym_ap` 511 + `ind_sym_ap_hf` 83; `ind_sym_m10` 527 +
  114; `xfm_bs_ap` 1576 + 635; `xfm_bs_m10` 1576 + 635; `xfm_ms_ap` 1400 + 225; `xfm_ms_m10` 1400 + 225;
  `xfm_bs_ap_ct` 78 + 183; `xfm_bs_m10_ct` 47 + 200. Verify with `ic-opt call lib.load` / `lib.coverage` and a
  duplicate scan (identical dims within a stratum: 0 everywhere).
- Rebuild: the datasets rebuild themselves (new cache keys); run `lib.load` for every stratum, then let the
  calibrations run for the columns the 80 GHz demonstration uses (`Lp@80`, `Qp@80`) and the library's declared columns
  of the two inductor strata pairs: `ic-opt call lib.coverage` / `lib.query` on them (at most 64 threads in all:
  `workers` x `threads` <= 64; `IC_OPT_PROFILE_DIRS=/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/EM-opt-workflow/process_data/profiles`).
- Re-run the 80 GHz demonstration (`ic-opt-accept/region_demo_80g/run_region.sh`'s targets) on `ind_sym_ap`,
  `ind_sym_ap_hf`, `ind_sym_m10`, `ind_sym_m10_hf` and save the four answers under
  `ic-opt-accept/n109_split_hf_strata/`; summarise the robust ranges and the measured hits per stratum.
- Before / after: for the four inductor and single-turn transformer base strata, read the library's cached
  calibrations before the split (`.cache/calibration-<stratum>-<column>-*.json`: `median_rel`, `coverage_2sigma_before`,
  `k_scale`) for `Lp_lf`, `Qp_peak`, `SRF`/`SRF_p`, `Qp@28`, `Qp@112` (the old keys: find them by the stratum name and
  the dataset key the old dataset files carry), then the same after the rebuild, and tabulate the change. No EMX, no
  product code change.

## 2. Deliverables

`/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-accept/n109_split_hf_strata/RESULT_CN.md` (Chinese: what changed in
`library.yaml` with the backup's name, the row counts per stratum, the duplicate scan, the before/after calibration
table, the four demonstration answers' summaries, every command run), the scripts used, the raw answers; the
`## Record` of this file. Spec files in `ic-opt-accept/*` that name a stratum (`library.stratum: xfm_bs_m10_ct` and
the like) are the user's experiments: do not edit them; list the ones that would now see fewer rows (grep
`stratum:` under `ic-opt-accept/` and the library's own tooling), so the user knows which to point at the `_hf`
stratum.

## 3. Working rules

Read only outside `library.yaml` and the evidence directory; the library's `.cache` is written by the library itself
when it rebuilds -- that is expected. Branch `n109-split-hf-strata` in `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/worktrees/n109-split-hf-strata`
for the Record only (`git -C`, absolute paths; one commit; no merge, no push, no BACKLOG edit); the manifest change
lives in the library, not in the repository. Commit message in the repository's style ending with
`Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`. Report back in Chinese.
