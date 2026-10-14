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

Branch `n93-tap-pad-gap`, from `main` at 24ceb99; `git merge --ff-only main` (run before committing) brought it to 562d01c --
N-94 / N-53 merged and two backlog commits, nothing under `src/ic_opt/em` -- and every comparison below was run against 562d01c.
One commit for the fix with its tests and documents (328e160), one for this record; nothing merged, nothing pushed,
`docs/refactor/BACKLOG_CN.md` untouched.

| file | change |
| --- | --- |
| `src/ic_opt/em/pcell/_pcell_xfm_bs.py` | `_bs_tap_stack_gap` (with `_drawn_on` and `_run_side_by_side_um`), called by `xfm_bs` for each winding right after `_xfm_net_short`. A tap is measured when its stack reaches the other winding's metal (the tap metal at or below it, its winding above it): the tap's shapes on that metal -- the W x W pad, and the lead too when the tap metal is that metal -- against the other winding's, both windings as drawn in their sub-cells after the straight extension. Every pair of facing edges closer than the widest spacing those widths can need is judged against the rule below; the largest shortfall raises `PortError`. After the short gate, so a pad that touches the leads is still reported as a short. Docstrings: `xfm_bs` (with the other tap rules) and `_bs_center_tap` (its via-stack bullet). |
| `src/ic_opt/em/pcell/reference.py`, `docs/em/devices.md` | The xfm_bs tap paragraph (`XFM_BS_TAP_NOTE`) states the rule, the numbers the refusal names, the gap `OPENING - W/2` only where the pad lies alongside the leads, reference mode unchecked; `devices.md` regenerated (`python -m ic_opt.em.pcell.reference`). |
| `src/ic_opt/em/pcell/README.md` | One sentence after the net gate's ("fails closed only on a real short"): the one place the pcell holds the two nets to the profile's spacing. Not in the specification's list. |
| `tests/ic_opt/pcell/test_xfm_bs_tap_pad_gap.py` | 18 tests, below. |

The refusal on demo_6m: `xfm_bs: the primary's via-stack tap (CT_P_ME=M4) draws a 10 x 10 um pad on M5, the secondary's metal,
0.200 um from the secondary's own M5 metal over a 5.000 um parallel run, below the 0.4 um profile demo_6m asks between M5 lines W_P=10
and W_S=5 um wide over that run; the secondary's leads lie OPENING_S=5.2 um off the centre line (a wider OPENING_S or a narrower W_P
moves the pad away from them; a tap on the primary's own metal draws nothing on M5)`; "at a corner" when the closest edges do not run
side by side, "its 8 um wide lead" when the tap metal is the other winding's.

### The rule and where its value comes from

`_pcell_core._required_parallel_spacing(metal, min(W, W_other), run, process)`, the function `_check_trace_rules` calls, on the same
data: the profile's `layout_rules.metal_width_space.<metal>.min_space_um`, raised to the `min_space_um` of every
`layout_rules.passive_region.wide_parallel_spacing` entry that lists the metal, whose `when_width_gt_um` both lines exceed and whose
`when_parallel_length_gt_um` the pair's run exceeds. Both lines: the DRC audit (`drc_audit.wide_parallel_spacing_violations`) counts a
wide-line finding only between two edges that are both wider than the threshold. The run: one edge projected on the other, the length
the audit's `min_projection` filters on (boxes at the N-93 coordinates: KLayout reports the pair at a projection floor of 8.750 um,
not at 8.751); 0 for edges that are not parallel. Strict, as in the audit: a gap equal to the rule passes. Reference mode (no profile): skipped, as in
`_check_trace_rules`. For the N-93 case the value is the private profile's M9 wide-line spacing, read from its `rule.yaml` when the
device is built and not written here; on demo_6m, M5's min space (0.4 um) and its wide-line rule (2.0 um between lines wider than
5 um running more than 20 um).

### Tests

`tests/ic_opt/pcell/test_xfm_bs_tap_pad_gap.py`, on demo_6m (the M5 rules read from the profile) unless noted:

1. `test_a_pad_too_close_to_the_other_windings_leads_is_refused_with_its_numbers` -- the N-93 shape on M6 / M5, the primary tapped to
   M4 through M5: 0.2 um over a 5 um run refused, the message's numbers checked; a gap equal to the rule and a wider one build.
2. `test_the_secondarys_tap_is_measured_the_same_way` -- the secondary on M6 tapped through the primary's M5.
3. `test_the_rule_follows_the_widths_and_the_parallel_run` -- with the tap metal the secondary's (M5) the 8 um lead runs 26 um between
   6 um leads: 1.5 um refused under the wide-line rule, a gap equal to it builds; the same 1.5 um from a pad (a 6 um run) or from
   5 um leads (not wider than the threshold) builds.
4. `test_the_gap_is_measured_from_the_actual_placement` -- OD_P 88 builds (the pad ends 1 um short of the leads: 1.02 um at the
   corner) where `OPENING_S - W_P/2` says 0.2; OD_P 90 refused at a corner; centre spacing 19 refused 0.092 um from the secondary's
   inner chamfer (18 builds); a secondary port spacing of 15.6 um (an inward jog) refused 0.3 um from leads whose opening keeps them
   3 um away (15.8 builds).
5. `test_contact_stays_a_short_and_nothing_else_changes` -- a pad overlapping the leads is still "nets short"; reference mode builds
   the refused geometry; a same-metal tap and an untapped primary build at a 1 um opening.
6. `test_the_pcell_refuses_exactly_what_the_drc_gate_would_fail` (12 cases, 7 refused, 5 built) -- each built through the generator
   with the check and with it switched off (monkeypatched), the unchecked GDS audited: refused exactly when the audit finds
   `min_space` / `wide_parallel_spacing` on M5; what builds passes the product gate and is byte for byte the unchecked drawing.
7. `test_the_n93_case_is_refused_and_opening_7_builds` (the private profile; skipped without it) -- obs_0003's configuration
   through the generator: refused, 1.000 um over the 8.750 um run, the rule as the profile gives it (read by the test), W_P=10,
   W_S=5, OPENING_S=6; a row of the table, OD 60 / 60 and W 10 / 5, refused at a 6 um opening and built at 7.

Runs (`PYTHONPATH=<worktree>/src`, the main tree's `.venv`, `OMP_NUM_THREADS=2`): `tests/ic_opt/pcell` with the nine files that build
xfm_bs (`test_em_pcell`, `test_em_circuit`, `test_em_measure`, `test_library_footprint`, `test_library_xfm`, `test_library_composed`,
`test_spec_library`, `test_lib_tap`, `test_lib_refine`): demo_6m 635 passed, 636 skipped at 562d01c, 652 passed, 637 skipped on the
branch; with the private profile 1269 passed, 2 skipped and 1287 passed, 2 skipped. `ruff check src tests`: all checks passed.

### Byte for byte with `main`

As T19.4 / T19.5 did (scratch, not committed):

- main's tests (562d01c, exported) run once with main's sources and once with the branch's -- the suites above, each temporary
  directory kept with `--basetemp` -- and every GDS, `emx_ports.txt`, `geometry_manifest.json` and coordinates file at the same path
  compared. demo_6m: 6955 files, 2318 GDS, 2315 port files and 2312 manifests identical, 10 GDS that tests write with KLayout's default
  writer differing only in their BGNLIB / BGNSTR time, none differing otherwise, none on one side only. The private profile: 7433
  files, 2621 GDS, 2391 port files, 2384 manifests and 30 coordinates files identical, 7 such GDS. Among them 1996 / 2012 xfm_bs
  generator builds, 45 / 49 of them with a tap stack that reaches the other winding's metal (measured, passed, unchanged). The test
  outcomes are the same on both sides except main's `test_reference.py` (`devices.md` regenerated). The same comparison against
  24ceb99 before the fast-forward: 6916 / 7394 files, identical.
- The 13 golden GDS are among them; `test_golden.py` passes, and `test_the_golden_bs_cases_are_unchanged_byte_for_byte` pins
  `xfm_bs` and `xfm_bs_ct` to the committed bytes (`xfm_bs_ct`'s M6 tap passes M5, 5.5 um from the secondary's leads: measured).
- Library replay: every build recorded in the libraries on this machine
  (`/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-library`: 19372 `geometry_manifest.json`, 2293 ind_sym, 8995 xfm_bs -- 8120
  untapped, 508 same-metal tapped, 367 via-stack tapped -- and 8084 xfm_ms) regenerated from its recorded config with the same GDS
  name, as `docs/refactor/reports/pcell_plan/pcell_byte_replay.py library` does, with main's sources (562d01c, and 24ceb99 before)
  and with the branch's: the same outcome and the same sha256 of GDS, manifest and port file for all 19372, none refused, every GDS
  equal to the recorded one on both sides (14592 manifests differ from the recorded ones on both sides alike: rows that predate
  fields the manifest has gained since).

### The t18_ct_table check

- The 367 rows of the tapped table (primary M10, secondary M9, both taps on M8, openings 7 um) are the library's
  `n28/xfm_bs_ct_m10` part, copied there from `t18_ct_table/stage` (whose own `sims` are gone): in the replay above, built and
  identical on both sides. Each primary tap's M9 pad is measured -- the closest 2.0 um (W_P 10), 3.25 (7.5), 4.5 (5), that is
  `7 - W_P/2` -- and passes; the secondary's taps (M9 to M8) never reach M10.
- `ct_grid.json` replayed (408 points at its 7 um opening): the same 408 outcomes on main and on the branch, the 367 ok points built
  byte for byte alike (GDS, manifest, port file), the 41 refused (`_check_opening`) with the same messages.
- The opening-6 pilot's four points (`stage_pilot_opening6`): obs_0001 / 0002 / 0004 build as on main; obs_0003 (OD 50 / 42.5,
  W 10 / 5) is refused by the pcell -- the M9 pad 1.000 um from the secondary's M9 metal over an 8.750 um parallel run, below the
  profile's spacing for W_P=10 and W_S=5 lines over that run, OPENING_S=6 -- where main built it and the gate refused it later
  (`[wide_parallel_spacing] M9 x2`). At OD 50 no 7 um opening builds (`_check_opening`'s bound is 6.2 um at W 10); the opening-7
  half of acceptance (a) is shown on the table's concentric W 10 rows (OD 55-100), test 7 above.

### Parity with the DRC gate (scratch)

5145 demo_6m and 8000 private-profile configurations (both tap directions, the tap metal below and equal to the other winding's metal,
ODs 40-100 and unequal, widths 4-10, openings 2.6-8 um, centre spacing up to the overlap bound, inward port-spacing jogs, a straight
extension), each built through the generator with the check and with it switched off, the unchecked GDS audited. The check refuses
exactly those whose audit has a `min_space` or `wide_parallel_spacing` finding on the crossed metal (278 / 510) and no other; 3 / 91
more are refused by the check where the unchecked build is refused later by the ground fixture (overlapping or touching stubs); 2029
/ 3221 are refused by an earlier rule either way; 2835 / 4178 build.

### Deviations

1. **Measured on the drawing, not before it.** The specification says "before drawing, ... compute the gap the drawing would have".
   The gap is measured on the two windings as drawn into their sub-cells (after the taps and the straight extension), still inside
   `xfm_bs`, before the device cell, the fixture, the GDS and the gate. A formula for the pad between the leads would miss the
   placements the specification asks to cover: the pad short of the leads (test 4: 1.02 um where the formula says 0.2), the other
   winding's ring at a large centre spacing (0.092 um from its chamfer), the facing leads' inward jog, the extension (which moves
   the pad and the leads together but the other winding's closed column the other way).
2. **The lead too.** When the tap metal is the other winding's metal the stack ends there and its lead runs on between that winding's
   leads, over their whole length (the wide-line rule's run). The specification names the pad on an intermediate metal; the same
   shapes on the same metal, one measurement. No library build has such a tap; one test does
   (`test_xfm_bs_n65_ap_center_tap`, n65_1p9m: the AP primary tapped down to M9, the secondary's metal, its lead 5 um from the
   secondary's leads): measured, passed, byte-identical.
3. **The width.** `_required_parallel_spacing` takes one width; the rule is read with the narrower line's, the audit's reading.
4. **The README sentence** (beyond the specification's list).
5. The message names the run beside the gap, the rule value, the widths and the opening, and says what moves the gap.

### Not foreseen

1. **xfm_ms has the same case, unguarded.** Its single-turn primary's via-stack tap (`_bs_center_tap`) passes the multi-turn
   winding's metal whenever `ct_primary_metal` sits at or below `secondary_metal`, and the pad faces the multi winding's leads the
   same way: on demo_6m (single on M6, W 10; multi on M5, opening 5.2 um; tap to M4) the pcell builds it and the audit finds
   `min_space` M5 x2 between the pad and the leads. Section C is xfm_bs; not changed. xfm_balun's taps (the third caller) were not
   examined.
2. In the private sweep, 200 builds that pass the new check fail the audit with `min_space` on M1: the ground fixture's stubs on the
   default bottom metal at small openings, T19.4 / T19.5's matter, not the tap's.
