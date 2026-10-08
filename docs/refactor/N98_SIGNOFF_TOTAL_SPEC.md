# N-98 — `signoff total=`: the search advanced in batches with the re-check at the end; the skill's procedure for a run an agent advises between batches

Status: specification (2026-10-08), for the coding subagent. Decision: the user's 2026-10-08 "先进行这两处" after the
language-model comparison (`BACKLOG_CN.md` 0.14; the sessions' protocol and findings in
`ic-opt-accept/llm_compare/RESULT_CN.md` and the projects' `SESSION_LOG.md` files, read-only). N-96 (merged, `optimize
total=`) gave the search step a design sized for the whole run; this ticket gives the `signoff` recipe the same increment,
and writes the agent's procedure into the skill so that an agent reading it uses the advice interface without a task
description telling it to.

Read before writing: `src/ic_opt/recipes/signoff.py` (whole file: the search call, `_recheck_stop`, rounds and tighten,
`signoff_rounds.json`, `--plan`), `src/ic_opt/recipes/optimize.py` (how `total` and `initial_trials` are passed since
N-96), `src/ic_opt/blocks/optimize.py` (`total`, `.icopt/steps.json`), `src/ic_opt/digest.py` (version 5: the re-check
section, the advice table, the `library` entry -- what it holds about the grid combinations), `src/ic_opt/advice.py`
(what an advice may do, `in_effect`), `tests/ic_opt/test_signoff_tighten.py`, `test_cli_recipes.py`, `test_optimize.py`
(N-96's increments test), `skills/ic-opt/SKILL.md` (whole file), `docs/refactor/N96_DESIGN_SIZE_SPEC.md`,
`N97_DIGEST_RECHECK_SPEC.md`, `T17_1_5_ADVICE_SPEC.md`, and, for the procedure's content,
`ic-opt-accept/llm_compare/PROMPT_B.md`, `PROMPT_COMMON.md` and three session logs (`B_opus_s1`, `B_dspro_s0`,
`B_sonnet_s0`: `SESSION_LOG.md`, `advice_*.yaml`).

## 1. `signoff total=<N>`

`signoff` takes `total` (optional, default `budget`; `total < budget` refused before anything runs, `--plan` too).
The search step is called with `budget=budget, total=total` (N-96: the design sized for `total`, recorded by the first
call). When `budget < total` the recipe stops after the search: it prints `signoff: the search holds <k> of <total>
points; the re-check runs when it reaches <total> (signoff budget=<total> total=<total>)` and returns; no re-check, no
rounds, no report. When `budget == total` (the default call included) everything runs as today: the re-check of the
top `top`, the rounds, the report. A re-run of a finished signoff is unchanged. `--plan` prints the search's plan and the
same line (or the re-check's plan when `budget == total`). Four calls `budget=10/20/30/40 total=40` give exactly what
one call `budget=40` gives (search points and re-check), and advice adopted between the calls is in effect from the next
batch as `optimize`'s is.

## 2. The skill: the procedure of a run an agent advises between batches

`skills/ic-opt/SKILL.md`. Keep its shape (mental model, numbered procedure, cheatsheet, ...); change these:

- **Step 4 "Run"** gains a paragraph: a run the agent supervises is advanced in batches, not run in one call --
  `signoff PROJECT corner=tt budget=<k x batch> total=<N> ...` (multi-corner spec) or `optimize PROJECT budget=<k x batch>
  total=<N> ...` (one corner), reading the digest and deciding after each call (step 7), the last call (`budget=total`)
  doing the re-check. One call with `budget=total` is the run without advice.
- **Step 7 "Give the run advice"** is rewritten as the procedure the 24 sessions of 2026-10-08 followed, in this order:
  1. *When*: after every batch. Before the initial design is complete (metric_gp: `min(max(2 x active variables, 8), 20)`
     points, the plan line says how many) only start rows act; `ranges`, `fixed`, `vary` act from the first batch after
     it. Leaving the run alone is a decision; say why.
  2. *What to read*: the digest's best point and the score term that limits it (the objective's terms saturate: a metric
     past its saturation earns nothing more), the constraint margins, the "no value" splits (the same variable and side
     in two consecutive digests is evidence; one digest is not), the advice table's verdict for the advice in effect,
     the re-check section once a re-check ran (which corner erodes which metric), `--points` for per-point metrics, and
     for a library device the valid grid combinations (say exactly where the agent finds them: the digest's `library`
     entry if it lists them -- check; if it only counts them, add the list to `digest.json`'s `library` entry under
     `combinations` as the grid's text rows, `[{"xfmr.Lp": "110p", "xfmr.Ls": "90p", "xfmr.k": "0.55"}, ...]`, and say
     so in the skill: a start row or a proposed point off that list is refused).
  3. *How to decide* -- the six rules the skill already has (from the eight T17.6 sessions), reworded only where needed,
     plus what the 24 sessions added: a start row is the best point with one lever moved for the term that limits it,
     with the circuit's reason (the netlist says which device the variable sizes); a ranges advice rests on a split seen
     in two consecutive digests or on the best five points' common side, never on the initial design alone; the search
     optimizes at the search corner but the re-check ranks by the worst corner, so keep margin on the metrics the other
     corners erode (the spec's corners say what varies; after the first re-check the digest says which); the free fifth
     of each batch often holds the winner -- never fix or narrow every variable; after each batch review the advice in
     effect against the verdict and revoke or replace it with its evidence.
  4. *The commands*: the advice file (as today), `advise`, `--revoke`, `--list`; the loop of step 4; and that the
     reason of every advice is the record.
  5. *The report back* (when the agent works for a person): per batch the decision and its evidence, the best objective
     at the search corner and, after the re-check, the best point feasible at every corner and its worst-corner
     objective -- the two are not the same number.
- **The cheatsheet**: `signoff ... [total=N]`.
- Nothing else of the skill changes; its measured numbers (the T17.6 sentence) stay, with one sentence on the 2026-10-08
  comparison: advice between batches never ended worse than the same seed without it in 11 of 12 runs across four
  models, start rows and narrowed ranges alike; prior start points from the agent and points proposed by the agent in
  place of the strategy did not beat the strategy (`docs/refactor/BACKLOG_CN.md` 0.14).

## 3. Tests

`tests/ic_opt/test_signoff_tighten.py` (or a new `test_signoff_total.py`): the four-call increment equals the one-shot
run (search points per batch, re-check points, `signoff_rounds.json` when rounds > 1); the stop line and no re-check
when `budget < total`; `--plan` lines; `total < budget` refused; an advice adopted between two calls is in effect from
the next batch (reuse `test_advice.py`'s fixtures). `tests/ic_opt/test_skill*.py` if any checks the skill's text; the
digest test for the `library.combinations` entry if added. `ruff check src tests` clean.

## 4. Documents

The skill (section 2); `README.md` "Recipes" (`signoff total=`); `docs/refactor/T18_4_TIGHTEN_RECIPE_SPEC.md` gets a note;
append `## 5. Record` here.

## 5. Working rules for the coder

Branch `n98-signoff-total` in the worktree `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/worktrees/n98-signoff-total` (from main;
`git merge --ff-only main` first). Python `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-modular/.venv/bin/python` with
`PYTHONPATH=<worktree>/src`; never `uv run`, `uv sync` or pip. Run `tests/ic_opt/test_signoff_tighten.py`,
`test_cli_recipes.py`, `test_optimize.py`, `test_advice.py`, `test_digest*.py`, `test_library_device_run.py`,
`test_skill_author_spec.py`, `test_replay_parity.py` (with
`IC_OPT_RECORDED_RUNS=/home/zzchen/.ic-opt/remote_runs/zzchen@10.113.216.131/802f8b444b991da2:/home/zzchen/remote_opt/Mixer_CS_validation_second_batch_20260810`),
and `ruff check src tests`; all clean before committing. No simulator; nothing under `ic-opt-accept` or `ic-opt-library`
written (the session logs are read-only input); nothing private in the skill (no circuit names, no numbers of the
user's platform beyond the sentence of section 2). Do not edit `docs/refactor/BACKLOG_CN.md`. Use a private
subdirectory of your scratchpad for helper files. Commit style of the repository, trailer `Co-Authored-By: Claude Opus 5.5
<noreply@anthropic.com>`. Do not merge or push.

## 5. Record

Done 2026-10-08 by the coding subagent on `n98-signoff-total` (from main `f8c8771`; `git merge --ff-only main` found it
up to date), not merged, not pushed. Commits: `0fcbf15` (implementation, tests, skill, README, T18.4 note) and the commit
of this record.

What was done.
- `recipes/signoff.py`: `main(..., total=None)`. `_check_total` refuses a `total` below `budget` (and one that is not a
  whole number) before anything runs, `--plan` too. The search is called with `budget=budget, total=total` (`total`
  passed as given: a call without it prints the design line of before). While `budget < total` the call ends after the
  search with `[run] signoff: the search holds <k> of <total> points; the re-check runs when it reaches <total> (signoff
  budget=<total> total=<total>)` -- no re-check, no round, no report, no `signoff_rounds.json`; the call with
  `budget == total` runs everything as before. Under `--plan` the line follows the search's plan, `<k>` being the points
  the search will hold after the call (`max(held now, budget)`); with `budget == total` the re-check's plan prints
  instead. With `rounds` above 1 the line `signoff round 1 of N: ...` is printed on the last call only.
- `digest.py`: the `library` entry gains `combinations` -- `{device id: [{variable: grid text, ...}, ...]}`, the
  combinations a row of the device's table sits on (`link.resolve(spec)[device].space_table`, its sorted order, texts
  as the digest's grid writes them), `None` where the library cannot be read (with the existing note). Until now the
  entry only counted them (`combinations` of `of` per device). `digest.md`'s "Library devices" names the entry in one
  sentence; it does not print the list. `DIGEST_VERSION` stays 5.
- `skills/ic-opt/SKILL.md`: step 4 gains the paragraph of the supervised loop (`signoff ... total=<N>` for a spec with
  several corners, `optimize ... total=<N>` for one; the last call, `budget=<N>`, runs the re-check; one call with
  `budget=<N>` is the run without advice). Step 7 is rewritten as items 1-5 of section 2 (when; what to read, including
  `library.combinations` in `.icopt/reports/digest.json` and that a row off it is refused; how to decide -- the six
  rules and the sessions' additions; the commands; the report back). Kept as they were, moved into the items: the advice
  file's example, "What an advice may do" with the T17.6 measurements (item 4, the 2026-10-08 sentence added there), the
  "Tools on your side" paragraph (item 3). The cheatsheet's `signoff` line takes `[total=N]`, with a two-line comment.
  Nothing else of the skill changed.
- README "Recipes": a paragraph on `signoff total=`. `T18_4_TIGHTEN_RECIPE_SPEC.md`: a note at its end.

Tests. Section 5's suites (`test_signoff_tighten.py`, `test_cli_recipes.py`, `test_optimize.py`, `test_advice.py`,
`test_digest*.py`, `test_library_device_run.py`, `test_skill_author_spec.py`, `test_replay_parity.py` with
`IC_OPT_RECORDED_RUNS`): 187 passed before, 193 after, no skips. New: `tests/ic_opt/test_signoff_total.py` (five: four
calls `budget=4/8/12/16 total=16` against one call `budget=16`, for `rounds=1` and `rounds=2` -- the search's points
batch by batch, the re-checked points and their children, `steps.json`, `signoff_rounds.json` without fingerprints, the
report, and the first three calls leaving no re-check, rounds file or report; the same project without `total` records a
2-point design and parts from the second batch; an advice adopted after the first call against one call handed the same
advice row; the `--plan` lines of both cases, nothing written; `total` below `budget` refused, `--plan` too) and one in
`test_digest_library.py` (`library.combinations`; `None` where the library cannot be read). `ruff check src tests` clean.
No `test_skill*.py` checks the `ic-opt` skill's text (`test_skill_author_spec.py` reads the author-spec skill only).

Deviations and readings.
- `library.combinations` is keyed by device id (`{"xfmr": [{"xfmr.Lp": "110p", ...}, ...]}`), not the bare list of
  section 2's example: a spec may hold several library devices, each table over its own variables, and the per-device
  `combinations` key already holds the count. The skill shows the keyed form.
- The digest's version stays 5: one key added to an entry, every other value as it was.
- The tests use the fake project of `test_signoff_tighten.py` on a widened grid (21 x 13 levels) in batches of 4 to a
  total of 16, not 10/20/30/40 to 40: the design (8 points for 16, 2 for a first call of 4) then decides the second
  batch, so the equality is not a coincidence of the first batch; the test shows the parting without `total`.
- Step 7's rule "Review at every round" now reads "after every batch"; the rule on a limit read off the initial design
  is merged with the new rule on what a ranges advice rests on; the 2026-10-08 sentence reads "ended no worse than the
  same seed without it in 11 of 12 runs" (section 2's "never ended worse ... in 11 of 12" read as that).
- Item 1 of step 7 gives `metric_gp`'s design as `min(max(2 x active variables, 8), 20)` "at most half the run's total"
  -- the cap `initial_design_size` applies, which section 2's formula leaves out.

Not foreseen by the specification.
- Section 4 asks for `## 5. Record`, and section 5 is the working rules: this record is a second section 5.
- A finished signoff run continued with `budget < total` (say `budget=10 total=40` on a store holding 40 search points)
  stops after the search, as section 1 says, and prints "the search holds 40 of 40 points".
- An advice row carries the spec's fingerprint, which follows the project's path (the spec names its export): copying
  `advice.jsonl` to a twin project applies nothing there; the test restamps the row.
- In the loop of step 4 the re-check runs on the last call only, so "after the first re-check the digest says which
  corner erodes which metric" helps an agent only on a continued run or with `rounds` above 1; within one batch-advanced
  run the corners' erosion is known from the spec's corners alone until the end. An interim re-check is not part of this
  ticket.
- `total` below `budget` through `ic-opt run` ends in a Python traceback (the command catches only `SiteError`,
  `EnvelopeError`, `LinkError`), as N-96 recorded for `optimize`.
