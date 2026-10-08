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

## 5. Record

Done 2026-10-09 by the coding subagent on `n99-deck-identity` (from main `eb32bc8`; `git merge --ff-only main` found it
up to date), not merged, not pushed. Commits: `c628728` (implementation, tests, README) and the commit of this record.

What was done.
- `deck.py`: `fingerprint()` hashes the templates as before, then for each testbench with a bundle (sorted) every file's
  relative path (`/` between names) and sha256, sorted by path (`tree_digests`: `os.walk(followlinks=True)` through
  `literal`, so a symlink counts by the content it points to, as `copytree(symlinks=False)` copies it; an unreadable
  directory or a dangling link raises; modes, times and empty directories do not count). A deck without bundles
  fingerprints as before (pinned: `acc85b226bd9babe` on main). The fingerprint is read afresh on every call (the bug
  report's bundle script changes a file under the same `Deck` object). `save()` writes `decks/<fp>/` as before; the same
  `<fp>` means the same files, so the rewrite of an existing bundle replaces it with identical content. New: `describe()`
  (`deck <fp> (<n> testbenches, <m> support files)`), `support_files(tb)`, `snapshot()` and the field `digests` (below).
  The module docstring states the identity and that files outside the bundle (PDK models by absolute path, Verilog-A,
  an sNp) are not part of it.
- `stages/spectre_chain.py`: `Render.identity` = `deck.fingerprint()` (a property); `Extract(waveforms=, operating_points=)`
  with `identity` = JSON of `[[name, expression, testbench], ...]` in order and the operating-point setting;
  `spectre_pipeline` passes both. `stages/em_chain.py`: `BindNport.identity` = the deck's fingerprint (it renders from
  the deck as `Render` does), and `em_circuit_pipeline` / `library/stage.py`'s `library_circuit_pipeline` build `Extract`
  with the exports and the setting.
- `blocks/netlist.py`: the import logs `deck=<fp>` (the saved directory's name) and `support_files=<m>` and returns
  `Deck.load(saved)`; under `--plan` `_plan_check` builds the deck it previews (templates per corner, bundles in its
  temporary fetch) and returns `deck.snapshot()` -- the bundles replaced by their digests before the fetch is removed, so
  it fingerprints as the run's import will and cannot run (`render_netlist` fails it as `failed:render`) or be saved.
  Before, the plan returned `Deck()`. `last_imported(store)`: the deck of the last `netlist.import` step.
- `blocks/evaluate.py`: `plan_identity(spec, pipeline, store, executor)` gives the deck line (when a stage carries a deck)
  and, when the store holds observations of this problem with another pipeline fingerprint, `<k> of the store's <n>
  observations were evaluated with another deck or pipeline: they are history, not reused` (the pipeline fingerprint is
  formed only then). `sim.evaluate` and `opt.optimize` print them under `--plan` after their shape line, prefixed as
  their own lines (`[plan] sim.evaluate step=...:` / `[plan] opt.optimize step=...:`). `plan_shape`'s string is unchanged.
- `blocks/doctor.py`: a `deck` check (informative, never failing): the deck last imported, `<fp> (<n> testbenches, <m>
  support files), as last imported; ...`, or `none imported yet: the run imports the exports`.
- `digest.py`: `progress.pipelines` = `None` while the points carry one pipeline fingerprint, else `{"pipelines": j,
  "current": k}` (current: the newest point's); markdown "How far the run is" prints `- observations from <j> pipelines;
  the current one holds <k>`. `DIGEST_VERSION` stays 5 (as N-98 did); no fingerprint reaches the digest.
- `migrate_store.py`: `legacy_pipeline_fingerprint` (frozen) sees `render`, `bind_nport`, `extract` without identity, so it
  keeps reproducing the stamps of before. A restamped 0.2.0 Spectre row or pre-T15.2 em_circuit row gets the pipeline's
  fingerprint formed with `Deck()` -- which no run forms -- so it is the problem's history, not reused (docstring).
- Docs: `engine.py` "Identity" paragraph, `eval/stage.py` module docstring, `deck.py` and `spectre_chain.py` docstrings;
  README "Evaluation = engine + stages" (one paragraph) and "Stores written by an earlier version" (the 0.2.0 sentence
  corrected, one paragraph added).

Tests. The specification's suites (`test_engine`, `test_em_engine`, `test_em_replay`, `test_replay_parity` with the
recorded runs, `test_blocks`, `test_em_circuit`, `test_library_device_run`, `test_cli_recipes`, `test_digest`): 141 passed
before, 141 after; with the new `tests/ic_opt/test_deck_identity.py` (13 tests: items 1-5 and 7 of section 2, plus a
modes-and-times test, the operating-point setting, the frozen formula, the no-bundle pin, a preview deck that cannot
run, the doctor line) 154 passed. The replay tests passed unchanged: no recording pins a pipeline fingerprint.
`ruff check src tests` clean. The whole `tests/ic_opt` (not required; run because every pipeline fingerprint changes):
2126 passed, 9 skipped before; 2139 passed, 9 skipped at `c628728` (the 13 new tests added).
Four existing tests asserted reuse of rows stamped before N-99 and were adapted:
- `test_operating_points.py::test_the_identity_of_the_problem_and_the_pipeline_is_what_it_was` -> `..._problem_is_what_it_was`:
  the problem assertions as they were; the 0.4.0 stamp `1c79f40effa5daab` is now asserted of the frozen formula, and the
  current fingerprint differs with the operating-point setting on and off.
- `test_operating_points.py::test_a_store_written_before_reads_as_it_did_and_its_points_are_reused` -> `..._are_history`:
  the 0.4.0 row still reads and writes back byte for byte; it is simulated again (`obs_0002`); the same row restamped with
  the current pipeline is reused with `operating_points` None (what the test compared before).
- `test_migrate_store.py::test_migrate_store_restamps_the_rows_0_2_0_wrote_and_the_engine_reuses_them` ->
  `..._and_they_are_the_problems_history`: the restamp and its report unchanged; afterwards the three points are
  simulated again (`obs_0004`-`obs_0006`, reused 0, new 3) where they were reused.
- `test_migrate_store.py::test_rows_it_cannot_place_stay_as_they_are_and_are_reported`: 3 Spectre runs where it was 2.

The bug report's scripts, run with this repository's interpreter against the worktree at `c628728`, `--output` under
the private scratchpad (`.../scratchpad/n99/repro_deck_reuse`, `.../repro_bundle_identity`); both exited 0 with no
external command:
- `reproduce_deck_reuse.py`: `bug_reproduced: false`, `Render_has_identity: true`, deck `4a5a0d29bb50c914` ->
  `f764c685db896738`, pipeline `f4a6070adf91cc34` -> `96bae98dfdfb4ad0`; first run obs_0001 0.05 (1 call), same deck
  obs_0001 0.05 (reused, 1 call), changed deck same store obs_0002 0.0 (2 calls), fresh store obs_0001 0.0 (3 calls);
  `new_EDA_runs: 0`.
- `reproduce_bundle_identity.py`: `bug_reproduced: false`, deck `5f625bcc62f67c28` -> `371277c02bd0f88a`
  (`deck_fingerprint_changed: true`), `same_saved_path: false`, the first snapshot's sha256 `9f4005b1...aee35080` before and
  after (`previous_saved_bundle_snapshot_overwritten: false`), `new_EDA_runs: 0`.

Deviations.
- `BindNport` got the deck identity too (the specification names `Render`): it is the render stage of the EM-circuit and
  library-circuit pipelines, which would otherwise keep the defect.
- `Extract.identity` holds each export's testbench as well as its name and expression.
- The plan lines are printed beside `plan_shape`'s line, not inside it: `plan_shape`'s string is compared exactly by
  about a dozen tests and is the one-line shape of a batch.
- `ic-opt doctor` names the deck the store last imported (from `steps.jsonl`), not one fetched afresh: the doctor
  fetches nothing; the plan's `netlist.import` and deck line show the deck the run will import.
- For the plan to name the real deck, `netlist.import` under `--plan` now returns a deck snapshot instead of `Deck()`.

What the specification did not foresee.
- Under `--plan` the deck was always empty (`import_netlists` returned `Deck()`), so a plan line could not name it nor
  compare pipelines without the snapshot above.
- `migrate-store` formed "the current pipeline fingerprint" with `Deck()`; with the deck in it, the restamp cannot know
  the deck a row ran, and its frozen pre-T15.2 formula would have changed with the new identities. Chosen: freeze the
  formula; restamped deck pipelines are history (the bug report's item 5: no old observation relabelled as trusted).
  README's sentence that restamped 0.2.0 rows "are reused from then on" was corrected. Taking the store's last deck
  instead would re-create B01 for a store whose netlist changed after its rows were simulated.
- Toggling `simulator.operating_points` now changes the pipeline fingerprint (it stays out of the problem): a point
  evaluated with them off is re-simulated for a run with them on. `test_operating_points` had pinned the opposite.
- `import_netlists` used to log `deck.fingerprint()` after removing the staging tree its bundles pointed at; with bundle
  hashing that would raise, so the log takes the saved directory's name.
- A deck directory saved before N-99 with bundles fingerprints, when loaded, to a value other than its name; nothing
  depends on it (each run imports again), the doctor line shows the recomputed value.

Open questions for the user: whether `fix_run` on a point a pre-N-99 store holds should be warned about beyond the plan's
history line; whether `migrate-store` should leave deck-pipeline stamps as they are instead of restamping them to the
deck-less fingerprint (both leave the rows unreused; the restamp keeps the report and its tests as they were).
