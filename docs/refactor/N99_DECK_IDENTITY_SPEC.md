# N-99 — the deck is part of the process identity: a changed netlist or support file never reuses an old observation

Status: specification (2026-10-09), for the coding subagent. Source: the bug report of the THz DPA project
(`/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/THz_TX_Dual_band_DPA/bug_report_20261009/IC_OPT_BUG_REPORT_CN.md`, read-only,
items B01 and B01-b, with the reproduction scripts `reproduce_deck_reuse.py` and `reproduce_bundle_identity.py` in the
same directory), verified on main 925cf72: both reproduce. The user's decision (2026-10-09): fix.

The defect. `Deck.fingerprint()` (`src/ic_opt/deck.py`) hashes the template texts only; `Deck.save()` stores the bundle
(Maestro's support files) under that fingerprint and replaces an earlier bundle there. `Render` (`stages/spectre_chain.py`)
carries no `identity`, so `pipeline_fingerprint()` (`eval/stage.py`) does not change when the deck changes, and
`engine.run` (`eval/engine.py`, the `reusable` dictionary) reuses a whole observation of the same spec, pipeline, point
and children although its netlist was another. Seen in the field: after a netlist fix and re-import, the same point
returned the old result (`new=0, reused=1, simulations=0`).

Read before writing: `src/ic_opt/deck.py` (whole), `src/ic_opt/blocks/netlist.py` (`import_netlists`: how the deck is
built and saved), `src/ic_opt/stages/spectre_chain.py` (`Render`, `Extract`, `render_netlist`), `src/ic_opt/eval/stage.py`
(`identity`, `resolve_identity`, `pipeline_fingerprint`), `src/ic_opt/eval/engine.py` (`run`: `reusable`, the "Identity"
paragraph of its docstring), `src/ic_opt/stages/em_chain.py` (how an EM stage's identity is defined -- the model to
follow), `src/ic_opt/recipes/lib_refine.py` (how `link.identity` enters the pipeline for library devices), `src/ic_opt/blocks/evaluate.py`
(`plan_shape`, the plan lines), `src/ic_opt/blocks/doctor.py`, `tests/ic_opt/test_engine.py`, `test_em_engine.py`,
`test_em_replay.py`, `test_replay_parity.py`, `test_blocks.py`, `test_deck*.py` if any, `README.md` ("Evaluation = engine +
stages", "Stores written by an earlier version"), `docs/refactor/N91_LIB_REFINE_SPEC.md` (the "new generation for the next
process" rule, which this ticket generalizes).

## 1. What changes

1. **`Deck.fingerprint()` covers everything the deck hands the simulation**: the template texts as today, and every
   file of every bundle -- relative path and content hash, in a fixed order; symlinks by their content as copied
   (`copytree(symlinks=False)`), file modes and times excluded. Two decks with the same templates and different support
   files have different fingerprints; `Deck.save()` therefore never replaces another deck's bundle, and a deck loaded
   from `.icopt/decks/<fp>/` fingerprints to `<fp>` (test). Files outside the bundle that a netlist references (a
   PDK's model files by absolute path, Verilog-A, an sNp) are not part of it, and the docstring says so: the deck's
   identity is the exported netlist and its support files, as Maestro wrote them.
2. **`Render.identity = deck.fingerprint()`** and **`Extract.identity`** = the waveform export list (names and
   expressions, in order) plus the operating-point setting it depends on -- so `pipeline_fingerprint` changes when the
   netlist, a support file or the requested exports change, and `engine.run` reuses no observation across that change.
   Observations of the other deck stay in the store as this problem's history (the spec fingerprint is the problem's
   identity, unchanged): the strategies learn from them; the budget counts them; they are never returned as an
   evaluation. This is the rule N-91 states for a grown library table, applied to the deck.
3. **The record says so.** `plan_shape` / the plan prints `deck <fp> (<n> testbenches, <m> support files)`, and, when
   the store holds observations of this problem with another pipeline fingerprint, one line:
   `<k> of the store's <n> observations were evaluated with another deck or pipeline: they are history, not reused`.
   `ic-opt doctor` prints the deck line too. The digest's "How far the run is" gets one line when the store mixes
   pipeline fingerprints (`observations from <j> pipelines; the current one holds <k>`).
4. **Stores written before this change.** Their observations carry pipeline fingerprints computed without the deck:
   after upgrading, a continued run evaluates its new points as before (the budget and the history are by spec
   fingerprint, unchanged) and would re-simulate only a point it is asked to evaluate again, which `optimize` and
   `signoff` never do with an already-evaluated point of the same step. No migration. `README.md` "Stores written by an
   earlier version" gets a paragraph. The replay tests (`test_replay_parity.py`, `test_em_replay.py`) must pass
   unchanged: they replay recorded points, not pipeline fingerprints -- if a recording pins a pipeline fingerprint, say
   so and adapt the test to compare what it compared before.

## 2. Tests (`tests/ic_opt/test_deck_identity.py` or in `test_engine.py`; fakes, no simulator)

1. The same input twice: the second run reuses (as today).
2. Only the netlist text changed and re-imported: the point is evaluated again, a new observation, the old one kept;
   `reused=0`.
3. Only a support file's content changed (the template unchanged): the deck fingerprint differs, `save()` writes a new
   directory and the earlier bundle snapshot is untouched (byte for byte); the point is evaluated again.
4. `Deck.load(path).fingerprint() == path.name` for a saved deck with bundles.
5. A changed `waveforms` request: no reuse of an observation that lacks the export (evaluated again).
6. The two reproduction scripts of the bug report, run against the worktree's source (`--repo <worktree>`, a fresh
   `--output` under your scratchpad), report `bug_reproduced: false`; paste their result summaries into the record.
7. The plan line and the digest line of item 3 (one test each). `ruff check src tests` clean.

## 3. Documents

`README.md` (the two paragraphs), the engine docstring's "Identity" paragraph, `deck.py` docstring; append `## 5. Record`.

## 4. Working rules for the coder

Branch `n99-deck-identity` in the worktree `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/worktrees/n99-deck-identity` (from main;
`git merge --ff-only main` first). Python `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-modular/.venv/bin/python` with
`PYTHONPATH=<worktree>/src`; never `uv run`, `uv sync` or pip. Run `tests/ic_opt/test_engine.py`, `test_em_engine.py`,
`test_em_replay.py` and `test_replay_parity.py` (with `IC_OPT_RECORDED_RUNS=/home/zzchen/.ic-opt/remote_runs/zzchen@10.113.216.131/802f8b444b991da2:/home/zzchen/remote_opt/Mixer_CS_validation_second_batch_20260810`
and `IC_OPT_EM_RECORDED_RUNS=/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/EM-opt-workflow/experiments/m9_optimizer_inductor_smoke/ind_ct_turbo_smoke:/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/EM-opt-workflow/.scratch/em-library-workflow-acceptance/local-turbo`,
`IC_OPT_PROFILE_DIRS=/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/EM-opt-workflow/process_data/profiles`), `test_blocks.py`,
`test_em_circuit.py`, `test_library_device_run.py`, `test_cli_recipes.py`, `test_digest.py`, the new test, and `ruff check
src tests`; all clean before committing. No simulator; nothing under `THz_TX_Dual_band_DPA`, `ic-opt-accept` or
`ic-opt-library` written (the bug report and its scripts are read-only input; the scripts write only to the `--output`
you give under your scratchpad); nothing private. Do not edit `docs/refactor/BACKLOG_CN.md`. Private scratchpad
subdirectory for helper files. Commit style of the repository, trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
Do not merge or push.
