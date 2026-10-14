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

(to be written by the coder)

## Record: C

(to be written by the coder)
