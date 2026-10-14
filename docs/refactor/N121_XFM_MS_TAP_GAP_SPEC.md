# N-121: the same tap-pad gap check for the multi-turn transformer (and a look at the balun) (2026-10-14)

N-93 (merged 3cdf697; spec `N93_N94_N53_SMALL_ITEMS_SPEC.md` section C and its record) made `clean_port_xfm_bs` refuse, before the
fixture and the DRC gate, a via-stack centre tap whose pad (or lead, when the tap metal is that metal) on the other winding's metal
sits closer to that winding than the profile's spacing for those widths over that run (`_pcell_xfm_bs._bs_tap_stack_gap`, measured on
the drawn sub-cells, `_pcell_core._required_parallel_spacing`, strict like the audit, skipped in reference mode). Its coder reported
that `clean_port_xfm_ms` has the same case unguarded -- on demo_6m a tapped build that the audit fails with `min_space` on M5, x2 --
and that `xfm_balun`'s taps were not examined. User decision 2026-10-14: do it.

## 1. What changes

- `clean_port_xfm_ms` (`_pcell_xfm_ms.py`): the same check for its taps -- the single-turn primary's tap (same-metal or via-stack,
  T19.6) and the secondary's via-tower taps (at least two layers below) -- wherever a tap's shapes on another winding's metal face
  that winding: measured on the drawn sub-cells before the device cell, the fixture and the gate, refused with `PortError` naming the
  gap, the parallel run, the rule, the widths and the opening(s), like N-93. Reuse N-93's helper where it fits (move it to a shared
  module if both generators need it) without changing a byte of what `xfm_bs` builds or refuses.
- `xfm_balun`: find out whether its taps can face the other winding's metal the same way. If they can, the same check; if not, say
  in the record why not (with the drawing's facts).
- Every build that passes today builds the same bytes; a build refused today by the gate on the crossed metal is refused by the
  generator instead, with numbers; nothing else changes.

## 2. Acceptance

(a) the demo_6m case the N-93 coder found, refused with numbers (reconstruct it; the N-93 scratch evidence may still be in
`/tmp/claude-1011/-home-zzchen-Agent-virtuoso-EDA-AI-AGENT/22a251ad-03d3-4a55-8437-b0a4e97a3152/scratchpad/n93/`); (b) every golden
GDS and the library replay byte-identical with `main` for demo_6m and the private profile (the record of N-93 shows how: main's tests
run with main's and the branch's sources; `docs/refactor/reports/pcell_plan/pcell_byte_replay.py library`); (c) parity with the DRC
gate on a sweep of xfm_ms (and balun, if checked) tapped configurations, built with the check and with it switched off: the check
refuses exactly those whose audit finds `min_space` / `wide_parallel_spacing` on the crossed metal; (d) the pcell suite with and
without the private profile.

## 3. Working rules for the coder

Branch `n121-xfm-ms-tap-gap` in `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/worktrees/n121-xfm-ms-tap-gap` (from `main`; `git merge
--ff-only main` first). `PYTHONPATH=<worktree>/src` with `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-modular/.venv/bin/python`;
never `uv run` or `uv sync`; `git -C <worktree>` and absolute paths; the private profile with
`IC_OPT_PROFILE_DIRS=/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/EM-opt-workflow/process_data/profiles` -- its numbers are confidential:
none in committed code, docs or tests (tests use demo_6m or read the profile at run time). Targeted tests plus `ruff check src
tests`; pytest with `OMP_NUM_THREADS=2`; at most 8 threads in all; no EMX. Scratch work in the session scratchpad, not committed. One
commit for the fix with tests and documents, one for the `## Record` section below. No merge, no push, no edit of
`docs/refactor/BACKLOG_CN.md`. Messages in the repository's style, ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Record

(to be written by the coder: base commit, files and why, what the balun showed, the byte-for-byte and parity counts, tests, deviations)
