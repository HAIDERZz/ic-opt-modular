# N-105: a measured row with no resonance in its sweep meets an SRF minimum below the sweep's end (2026-10-13)

Found by the `lib.region` demonstration of 2026-10-13 (`ic-opt-accept/region_demo_80g/RESULT_CN.md`, gap 4): the
query `Lp@80` 120 pH +-8 %, `Qp@80` >= 12 on an inductor table adds `SRF_p >= 100 GHz` (the margin). The row at outer
diameter 60 um, width 2 um measures Lp@80 120.1 pH and a Q peak of 19.6, and its sweep to 250 GHz shows no resonance:
its `SRF_p` is empty. `region._hits` requires a finite value for every goal, so the row is not among the `measured`
designs that already meet every target, while the prediction path treats the same row as settled above the sweep
(`suggest.srf_floors`, `_settled`) and lists it as the first candidate. The user decided on 2026-10-13 to fix it. Same
family as N-94 (a library device's empty SRF failing a point).

## 1. What changes

- `ic_opt/library/region.py`, `_hits(ds, goals)`: a goal on an SRF quantity (`quantity.startswith("SRF")`) whose window
  has no upper end (`kind == "min"`, or a window / target whose upper end is +inf) is met by a row whose value for it
  is missing **and** whose `stop_hz` is at or above the window's lower end: no resonance up to the sweep's end means
  the resonance lies above it. A missing value under a goal with an upper end (`max`, a finite window, a target with
  tolerance) is not met, as today: the row's resonance is somewhere above the sweep, which says nothing about an upper
  bound. Every other quantity keeps today's rule (a missing value is not met).
- `_measured`: the entry of such a row keeps `values[q]` null (it is not measured) and gains `"above_sweep": {q:
  stop_hz}` for every SRF goal the sweep's end settled, so a reader sees why the row counts; rows without such a
  quantity have no `above_sweep` key.
- `_any_measured` follows `_hits`; `_unanswered` is unchanged (its own gate already uses `srf_floors`).
- `ic_opt/library/suggest.py`: verify that `suggest` already ranks such a measured row among `measured_ranked` (the
  `floor[j] = ds.rows[i].stop_hz` line); add the test of section 2 for it. If it does not, apply the same rule there and
  say so in the record.
- `lib.query`'s domain verdicts, the index, `lib.pick`: unchanged.

## 2. Tests (`tests/ic_opt/test_library_region.py` or the file that tests `region`; fakes, no EMX)

A fake stratum whose rows include one with `SRF_p` missing and `stop_hz` 250e9: (a) an `SRF_p` minimum of 100e9 counts
it in `measured`, with `above_sweep: {"SRF_p": 250e9}` and `values.SRF_p` null; (b) a minimum of 300e9 does not;
(c) a maximum, a finite window and a target on `SRF_p` do not; (d) a missing non-SRF quantity still does not;
(e) `lib.suggest` on the same targets lists the row among its measured candidates (or the record says what was found).

## 3. Documents

`docs/em/library.md` section 5b, one sentence on `measured` and `above_sweep`; `skills/ic-opt/SKILL.md` where it reads
`lib.region`'s answer, if it lists the fields of `measured`.

## 4. Working rules for the coder

Branch `n105-measured-above-sweep` in `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/worktrees/n105-measured-above-sweep`
(from `main`; `git merge --ff-only main` first). `PYTHONPATH=<worktree>/src` with the main tree's `.venv/bin/python`;
`git -C <worktree>` and absolute paths. Run `tests/ic_opt/test_library_region.py`, `test_library_suggest.py` (or the
files that test `region` / `suggest`), `test_library_query.py`, and `ruff check src tests`. One commit for the fix
with its tests and documents, one for the `## Record` section at the end of this file. No merge, no push, no BACKLOG
edit. Messages in the repository's style, ending with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
