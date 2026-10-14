# N-93, N-94, N-53: three small fixes from the backlog (2026-10-14)

User decision 2026-10-14 (list items 11, 15, 16): do all three. Two branches: N-94 and N-53 together (small, outside
the generators), N-93 alone (geometry).

## A. N-94: an SRF metric with no resonance inside the sweep is a lower bound, not a failure

Backlog row (2026-10-01, mixer platform run a, 5 of 40 points): a library device's `SRF_p` / `SRF_s` / `SRF` is empty
when its sweep shows no resonance, and a spec metric on it fails the point (`failed:measure`) -- although such rows
resonate highest of all; the index already treats the empty value as above the margin. N-105 (2026-10-13) made
`lib.region` count such a row as meeting an SRF minimum up to the sweep's end (`above_sweep`).

What changes (`ic_opt/stages/em_chain.py`, `Measure.run`): a metric on an SRF scalar (`SRF`, `SRF_p`, `SRF_s`) whose
value is None because the sweep has no resonance takes the sweep's last frequency (`ts.freqs[-1]`, in Hz) as its value,
and the child gets an issue line that does not fail it -- added after the status is decided, as the polarity warning
is: `metric <name>: no resonance inside the sweep up to <X> GHz; reported as <X> GHz, a lower bound`. Pcell and library
devices alike (the same stage). Every other None still fails the child as today. A metric at a frequency (curves) is
unchanged.

Tests: a synthetic two-port without resonance up to its last frequency: the metric equals the last frequency, the
child is `ok`, the issue line is there; with a resonance inside the sweep nothing changes; a non-SRF scalar that is None
still fails. Documents: where `docs/em/devices.md` / the README describe device metrics, and the author-spec skill
where it advises `srf_margin` instead of an SRF constraint (say that both now work and what the value means).

## B. N-53: each child's trace is kept

Backlog row (2026-09-27): `StageContext.trace` -- per child the commands, `bind:<instance>` with `signal_nodes`,
`dropped_ports`, `replaced`, the OCEAN timing rows -- is written and then lost: nothing under `.icopt/` holds it, and the
evidence of how a binding rewrote the netlist can only be read back from the rendered netlist.

What changes: when a child finishes (ok or failed), its trace entries are written to `trace.json` (a JSON list, UTF-8)
in the child's `sim_dir`. No change to `observations.jsonl`, to any fingerprint, to the digest or the report. If a
child has no `sim_dir`, nothing is written (say where that happens).

Tests: a fake run with a bind stage leaves `trace.json` in the child's `sim_dir` with the bind entry and its three
fields; a failed child keeps its trace too. Documents: where the docs list what a child's `sim_dir` holds.

## C. N-93: a via-stack tap too close to the other winding's leads is refused early, with its numbers

Backlog row (2026-10-01, the first tapped-table pilot: 1 of 4 failed, 75 of 390 grid points the same, all primary width
10 um and concentric): in `clean_port_xfm_bs`, a via-stack centre tap is a W x W stack at the winding's closed column
(`_pcell_xfm_bs._bs_center_tap`), e.g. M10 down to M8 through a W x W pad on M9. The other winding's lead pair has its
inner edges at +-its opening. With opening 6 um and primary width 10 um the M9 pad (+-5 um) and the secondary's M9
leads (+-6 um) are 1.0 um apart, below the process's spacing for wide parallel lines; the product DRC gate stops it
(`failed:pcell`, no EMX wasted), but late and without saying why. The tables were then built with opening 7 um (367
points, all passed DRC). Today's tapped tables use same-metal taps (T19.4), which this does not touch.

What changes: before drawing, when a via-stack tap's pad on an intermediate metal faces the other winding's leads on
that same metal, compute the gap the drawing would have (from the actual placement: both centres, offsets, openings,
widths -- not only the concentric case) and compare it with the spacing rule the process gives for those widths and
that parallel run length (the rule data `_check_trace_rules` already reads; reference mode without a profile: skip, as
there). Below it, raise `PortError` naming the gap, the rule value, the widths and the opening, like `_check_opening`.
Same-metal taps and builds without taps: unchanged.

Acceptance: (a) the N-93 case (opening 6 um, primary width 10 um, concentric, via-stack tap through M9) is refused with
the numbers; opening 7 um builds; (b) every golden GDS and the library replay are byte-identical with `main`
(demo_6m and the private profile), as T19.4 / T19.5 checked theirs; (c) the 367 tapped builds of
`/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-accept/t18_ct_table/` (or its replay) still build. Documents: the
generator's docstring and `docs/em/devices.md` where the tap rules are listed.

## Working rules for the coders

- A and B: branch `n94-n53-small-fixes` in `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/worktrees/n94-n53-small-fixes`.
- C: branch `n93-tap-pad-gap` in `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/worktrees/n93-tap-pad-gap`.
- Both from `main` (`git merge --ff-only main` first). `PYTHONPATH=<worktree>/src` with
  `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-modular/.venv/bin/python`; never `uv run` or `uv sync`; `git -C
  <worktree>` and absolute paths. The private profile is found with
  `IC_OPT_PROFILE_DIRS=/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/EM-opt-workflow/process_data/profiles`.
- Targeted tests only (the files that test what changed, and for C the pcell suite with and without the private
  profile), plus `ruff check src tests`; pytest with `OMP_NUM_THREADS=2`, at most 8 threads in all.
- One commit per item with its tests and documents, then one for that branch's record below. No merge, no push, no edit
  of `docs/refactor/BACKLOG_CN.md`. Messages in the repository's style, ending with
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Record: A and B

Branch `n94-n53-small-fixes`, from `main` at 24ceb99 as the working rules name it (`main` has since moved to 11ebf0d, a docs-only
commit on `docs/refactor/BACKLOG_CN.md` and `VERIFICATION_COVERAGE_CN.md`: nothing these commits touch, so no `--ff-only` merge was
made). One commit per item with its tests and documents (A 994b21b, B 1c0f580), one for this record; nothing merged, nothing
pushed, `docs/refactor/BACKLOG_CN.md` untouched.

| file | change |
| --- | --- |
| `src/ic_opt/stages/em_chain.py` | A. `Measure.run`: a scalar metric (no `frequency_hz`) on `SRF_p` / `SRF_s` / `SRF` whose value is None takes `float(ts.freqs[-1])` when `srf_above_sweep(q, quantity)` holds, and `bound_issue(name, value)` goes into a new list `bounds`; the child's issues are `issues + bounds + [polarity warning]` and its status is still decided by `issues` alone. `srf_above_sweep`: every drive the scalar reads (`SRF_DRIVES`: `SRF_p` the primary, `SRF_s` the secondary, `SRF` every drive of the quantities) has L > 0 -- the sign of Im Z -- at each of its finite samples above 0 Hz, and there are at least two. `bound_issue`: `metric <name>: no resonance inside the sweep up to <X> GHz; reported as <X> GHz, a lower bound`, X = `stop_hz / 1e9:g`. `Measure`'s docstring says so; `numpy` is imported. |
| `src/ic_opt/eval/stage.py` | B. `TRACE_FILE = "trace.json"` and `StageContext.write_trace()`: `json.dumps(trace, indent=1, default=str)` written with `encoding="utf-8"` to `workdir / TRACE_FILE`; returns the path. |
| `src/ic_opt/eval/engine.py` | B. `_run_point`: `cctx.write_trace()` right after the child's `sim_dir` is set (line 414), that is after its chain ran or raised a `StageFailure`; the module docstring says where a child has none. |
| `tests/ic_opt/test_em_measure.py`, `tests/ic_opt/test_library_device_run.py` | A. One test changed, four added (below). |
| `tests/ic_opt/test_em_circuit.py`, `tests/ic_opt/test_engine.py` | B. Two tests added to each. |
| `README.md` | A, "EM devices": what an SRF metric is, the bound and its line, a minimum judged on it, a maximum meaningless, the sweep that starts past the resonance. B, "Results": what a child's directory holds, `trace.json`, its records, the child that has none. |
| `docs/em/library.md` | A, section 8: a paragraph on an SRF metric of a row with no resonance, and `srf_margin` beside it. |
| `docs/waveform_export.md` | B: the OCEAN timing record in `trace.json`, beside the log line and the tsv row. |
| `skills/author-spec/SKILL.md` | A: the device-metric paragraph, and the library device's `srf_margin` (both work; what the value means). |
| `skills/ic-opt/SKILL.md` | A: the EM devices failure list (`failed:measure`). B: `trace.json` among the files that say why a child failed. |
| `docs/em/devices.md` | **not changed** (reading 5). |

Tests (fakes, no EMX, no Spectre):

- changed `test_measure_reads_the_quantities_the_spec_asks_for` (`test_em_measure.py`): the fake host's 3-point sweep (1, 5, 10 GHz)
  shows no resonance, so its `SRF_p` metric used to fail the point (`failed:measure`, `SRF_p is None`); the point is now `ok` with
  `SRF == 10e9` and the one issue line, and the second half (without the SRF metric) also expects no issue.
- `test_an_srf_with_no_resonance_in_the_sweep_is_the_sweeps_last_frequency_a_lower_bound` -- the synthetic inductor (resonance
  26.7 GHz) swept to 20 GHz: `SRF_p` and the system `SRF` are 20e9, child and point `ok` and feasible, the two lines in the child's
  issues and the point's (prefixed `ind/nominal: `), `quantities.json` still null; swept to 60 GHz the measured value (bit for bit
  what the kernel gives) and no issue; `SRF_p > 15e9 Hz` keeps the point, `> 25e9 Hz` is `constraint_failed` (judged on the bound);
  `bound_issue` formats 250e9 and 12.5e9.
- `test_every_other_missing_value_still_fails_the_child_and_a_sweep_past_the_resonance_gives_no_bound` -- a sweep from 5 GHz: `Lp_lf`
  stays a failed metric (`failed:measure`) with the SRF's line after the failure and its value in the child's metrics; sweeps 40-80
  GHz (past the resonance), one sample, and 0 Hz plus one sample: `failed:measure` with `SRF_p is None` as before; 0 Hz plus two: a bound.
- `test_each_drive_of_a_pair_is_bounded_by_its_own_reactance_and_the_system_srf_by_all_of_them` -- a pair with a 60 fF primary and a
  1 fF secondary (k 0.05; `library_fixtures.coupled_touchstone`): to 100 GHz `SRF_p` and `SRF` are measured (75.25 GHz) and `SRF_s`
  is the bound 100e9 with its one line; from 80 to 120 GHz `SRF_p` and `SRF` fail (`... is None`, in that order) and `SRF_s` is a bound
  after them; 2 fF on both: all three are 60e9 with three lines; `srf_above_sweep` is False for a quantity that is no SRF.
- (`test_library_device_run.py`) `test_a_library_row_with_no_resonance_in_its_sweep_gives_the_sweeps_end_as_its_srf` -- the library
  fixture, a circuit spec with an `SRF` device metric: the first grid point whose row has no resonance in its 60 GHz sweep is `ok`
  with `SRF == 60e9` and the line (before: `failed:measure`, the point stopped at its device), the first whose row resonates inside
  gives the row's value, no line; both children record their row.
- (`test_em_circuit.py`) `test_a_childs_trace_is_kept_in_its_sim_dir_the_binding_rewrite_with_it_ok_or_failed` -- an EM circuit run
  (two corners, a device metric, a binding that drops a third instance port, Spectre failing at `ss`): `tb/tt`'s `trace.json` is
  `bind:NPORT0` (`device` ind, `signal_nodes` `["net4", "net3"]`, `terminals` `["P1", "N1"]`, `dropped_ports` `[3]`, `replaced`
  `/old/ind.s3p`), `spectre#1`, `ocean#1`; the child that failed at Spectre has the same bind entry and the failed `spectre#1`
  (returncode not 0) and no OCEAN record; the device child's file is `[]`; the observation holds no trace.
- (`test_em_circuit.py`) `test_a_child_that_never_ran_has_no_sim_dir_and_so_no_trace` -- EMX failing: both children `sim_dir` null,
  no `trace.json` under the point's directory.
- (`test_engine.py`) `test_a_childs_commands_are_kept_in_its_trace_json_failed_ones_too` -- 2 testbenches x 3 corners with Spectre
  failing for one: each child's list is `spectre#1`, `ocean#1` (the failed one `spectre#1` alone), every record with exactly
  `label`, `argv`, `returncode`, `seconds`; `spectre.stderr` still there.
- (`test_engine.py`) `test_the_trace_is_a_json_list_in_utf8_whatever_a_stage_put_in_it` -- `write_trace` of an empty trace is the
  bytes `[]`; a `Path` and non-ASCII text read back as text.

Run with the rule off (A: `srf_above_sweep` always False, the base's behaviour; B: `write_trace` a no-op), the changed test and the
three new A tests in `test_em_measure.py`, the library test, and three of the four B tests fail, for the right reason (`failed:measure`
where `ok` is asserted, `FileNotFoundError` for `trace.json`); `test_a_child_that_never_ran_...` pins what holds before and after.

Tests (`PYTHONPATH=<worktree>/src`, the main tree's `.venv`, `OMP_NUM_THREADS=2`; "before" = base 24ceb99):

| suite | before | after |
| --- | --- | --- |
| `test_em_measure.py` | 15 (+1 skipped: `IC_OPT_EM_DB`) | 18 (+1) |
| `test_library_device_run.py` | 6 | 7 |
| `test_em_circuit.py` | 10 | 12 |
| `test_engine.py` | 19 | 21 |
| `test_em_engine.py`, `test_spectre_chain.py`, `test_ocean_export.py` | 18, 7, 14 | 18, 7, 14 |
| together | 89 (+1) | 97 (+1) |

Also run after the changes, files untouched, all pass: `test_em_emx.py` 10 (+1), `test_library_device.py` 9, `test_library_stage.py` 5,
`test_schedule.py` 29, `test_blocks.py` 12 (+1), `test_threads.py` 14, `test_netlist_and_ocean.py` 7, `test_operating_points.py` 34,
`test_multi_corner.py` 15, `test_saturation_margin.py` 27, `test_store.py` 3, `test_deck_identity.py` 13, `test_spec_library.py` 36,
`test_skill_author_spec.py` 8, `test_library_docs.py` 4, `test_packaging.py` 9 (it requires every text write to name its encoding),
`test_lib_tap.py` 17, `test_lib_refine.py` 14, `test_library_signoff.py` 3, `test_optimize.py` 44, `test_digest.py` 28,
`test_cli_recipes.py` 41, `test_signoff_total.py` 5, `test_signoff_tighten.py` 24. `ruff check src tests`: all checks passed, before and after.

### Where a child has no `sim_dir`

In one place: the point-level failure branch of `_run_point` (`engine.py`, the dict `failed`, line 392). When `pcell`, `emx:<device>` or
`pick` raises a `StageFailure` (or a command of theirs outlives its deadline), every child of the point is made from that failure --
`ChildResult(status="failed:<stage>", issues=...)` -- without running: no `StageContext` of its own, no directory made, `sim_dir`
null, so nothing is written (`test_a_child_that_never_ran_has_no_sim_dir_and_so_no_trace`). A child the schedule stops before it
runs is not a result at all (`not_run`). A child interrupted (`Interrupted`, a `KeyboardInterrupt`) or ended by an unexpected
exception returns no result, is not recorded, and writes nothing; it is simulated again.

### Readings and deviations

1. **A: the bound is for one of the three reasons the kernel leaves an SRF None.** `measure._srf_first_sign_flip` returns None when
   (a) the reactance never goes from positive to non-positive and was positive at the first sample -- the resonance is above the
   sweep, the case the specification means --, (b) the first usable sample above 0 Hz is already at or below zero -- the sweep starts
   past the resonance, which lies below it --, (c) fewer than two usable samples. Read as "value is None", all three would report
   the last frequency as a lower bound, and (b) would be false and dangerous: a 50-120 GHz sweep of a device resonating at 30 GHz
   would pass `SRF > 100e9 Hz` at 120 GHz. Only (a) takes the last frequency; (b) and (c) fail the child with the old text
   (`metric SRF_p: SRF_p is None`), tested (40-80 GHz of the 26.7 GHz inductor; one sample; 0 Hz and one sample). (a) is decided from
   the L curves the quantities already carry (the sign of L is the sign of Im Z for f > 0), not by changing the kernel.
2. **A: pairs.** `SRF_p` and `SRF_s` are judged on their own drive, so `SRF_s` can be a bound beside a measured `SRF_p` and system
   `SRF`; the system `SRF` is None only when every drive's is, and is a bound only when each of them is (a pair whose primary starts
   past its resonance keeps `SRF` failing although the secondary is inductive throughout).
3. **A: what the value is good for.** It is a lower bound: a minimum on it is judged on the bound (a bound below the minimum fails the
   constraint although the device may resonate above the sweep), and a maximum or an objective that wants a low SRF is judged on a
   value that is not the device's. The documents say so; no code refuses such a constraint (the specification asks for none).
4. **A: order, and a failed child.** The child's issues are the failures in metric order, then the bound lines in metric order, then
   the polarity warning; a child that fails for another metric still carries the bound lines of the metrics it did report (test: a
   sweep from 5 GHz). `quantities.json` keeps what the kernel measured (null): only the metric takes the bound.
5. **Documents.** `docs/em/devices.md` is generated from the config models (`python -m ic_opt.em.pcell.reference`, "Do not edit by
   hand") and lists no device metrics; the README, `docs/em/library.md` and the two skills carry the change. The author-spec skill
   advised no `srf_margin` in place of an SRF constraint (the backlog row only proposed to write it), so there was nothing to
   revise: "both work and what the value means" is written where it describes `srf_margin` and in the device-metric paragraph.
   `skills/ic-opt/SKILL.md` said `failed:measure` came from "no resonance for SRF", which is no longer true; reworded.
6. **B: always written.** The file exists for every child that finished, an empty list for a device's measurement; a reader can tell
   "recorded nothing" from "recorded by an earlier version" (no file). Nothing reads it; no fingerprint, observation field, digest or
   report changed (the suites above pass unchanged).
7. **B: the point's own context.** The `emx:<device>` command record is made in the point-level context (EMX is a point-level
   stage), not a child's, and is still not kept; the specification scopes B to children. The EMX command and log are in
   `sims/<obs>/em/<device>/` (`emx.cmd`, `emx.log`), which the README now says.
8. **B: where the writer lives.** `StageContext.write_trace()` rather than an engine function: the context owns `trace` and
   `workdir`, the engine decides when. `ensure_ascii` is left at its default (the file is ASCII, hence UTF-8 under any locale; the
   encoding is named for `test_packaging`'s rule); `default=str` keeps a stage that put a path or a numpy integer into its record
   from failing the child's end.

## Record: C

(to be written by the coder)
