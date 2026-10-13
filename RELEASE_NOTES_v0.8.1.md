# IC-Opt 0.8.1

0.8.0 polished the chain of a circuit that takes its devices from a library
table. This release fixes what two uses of ic-opt found afterwards: agents
advising a run between its batches (the language-model comparison,
`docs/refactor/BACKLOG_CN.md` section 0.14) and the THz dual-band DPA project's
bug report (section 0.15). A run advanced in increments now proposes what one
call proposes (`optimize total=`, `signoff total=`); the digest reports a
signoff's re-check and can list every point; the report names each metric's
worst corner; the deck -- the netlists and the support files Maestro exported
with them -- and the requested exports are part of the pipeline identity, so a
changed netlist never reuses an old observation; waveform exports are real CSV
files at full precision, written from the vectors; operating points are read
only on the instances of the component types that report `gm`. Each item has
its specification and record under `docs/refactor/` (`N96_DESIGN_SIZE_SPEC.md`,
`N97_DIGEST_RECHECK_SPEC.md`, `N98_SIGNOFF_TOTAL_SPEC.md`,
`N99_DECK_IDENTITY_SPEC.md`, `N100_OCEAN_EXPORT_SPEC.md`). ic-opt itself still
calls no language model.

Stores written by 0.8.0 are read as they are and there is no migration step:
no spec field changed its meaning, every spec fingerprint recorded by earlier
releases is recomputed unchanged, and a continued `optimize` or `signoff` goes
on from its observations. One identity did change -- the pipeline's -- and
item 1 below says what that means for a store.

## Changes in behaviour

Read these before upgrading a project that runs.

1. **The pipeline fingerprint holds the deck and the requested exports**
   (`docs/refactor/N99_DECK_IDENTITY_SPEC.md`). `Deck.fingerprint()` covers the
   templated netlists and, for every testbench, each support file's relative
   path and content; the render and bind stages carry it, and the extract stage
   carries the waveform exports requested and the operating-point setting. A
   netlist fixed in Virtuoso and exported again, a changed support file, another
   export list or a changed operating-point setting is therefore another
   pipeline: the point is simulated again, and the observations of the earlier
   pipeline stay in the store as the problem's history -- the budget counts
   them, the strategies learn from them, none is returned as an evaluation.
   Before, a changed netlist under the same `spec.yaml` reused the old
   observation (the bug report's B01), and a changed support file overwrote the
   saved bundle under the same deck directory (B01-b); both reproduction scripts
   now report `bug_reproduced: false`. **Every Spectre and EM-circuit observation
   written by 0.8.0 or earlier carries a pipeline fingerprint no run forms
   now**: after upgrading, `optimize` and `signoff` continue a run without
   simulating any point again (they never evaluate a point their step already
   holds), while `fix_run` on a point an earlier version evaluated simulates it
   again. `--plan` prints the deck it would import (`deck <fp> (<n> testbenches,
   <m> support files)`) and `<k> of the store's <n> observations were evaluated
   with another deck or pipeline: they are history, not reused`; `ic-opt doctor`
   has a `deck` line; the digest's "How far the run is" says when the store
   mixes pipelines. `migrate-store` restamps 0.2.0's Spectre rows and the
   pre-T15.2 EM-circuit rows as before, to the pipeline's fingerprint without a
   deck: they are history too, no longer reused. Files a netlist names outside
   the export (PDK models by absolute path, Verilog-A, an sNp) are not part of
   the deck.
2. **The initial design's size is the step's first call's**
   (`docs/refactor/N96_DESIGN_SIZE_SPEC.md`). The size `metric_gp` and the
   `openbox_*` strategies use is recorded in `.icopt/steps.json` on the step's
   first call and kept by every later call; before, each call sized it from its
   own `budget`, so a run advanced in increments (`budget=10`, then `20`, ...)
   proposed other points than one call with the final budget. `optimize
   total=<N>` sizes the design for the budget the run is meant to reach, and
   the plan line says so (`initial design n points (sized for a budget of
   40)`); `total` below `budget` is refused before anything runs. A store
   written before this file existed sizes the design from the current call's
   budget, as it did. `ic-opt run optimize PROJECT initial_trials=N` now works
   as the README and the skill say (the recipe did not pass it on before).
   `turbo`, `sobol` and `random` are untouched.
3. **`signoff total=<N>`** (`docs/refactor/N98_SIGNOFF_TOTAL_SPEC.md`): the
   search advanced in batches (`budget=10 total=40`, then `20`, ...), a call
   whose `budget` is below `total` stopping after the search with one line and
   no re-check, round or report; the call that reaches `total` runs the re-check,
   the rounds and the report. Four calls give what one call `budget=40` gives,
   search points and re-check alike, and an advice adopted between two calls is
   in effect from the next batch. `coarse_to_fine` takes no `total`.
4. **The digest is version 5** (`docs/refactor/N97_DIGEST_RECHECK_SPEC.md`).
   A signoff run's digest has a "Re-check at every corner" section: the best
   point feasible at every corner under the search's best, every re-checked
   point and the corner where it first fails; re-check points are not counted
   as batches nor as an advice's other points, and an advice's statistics count
   its own step's points only. `ic-opt digest --points` (and `analyze.digest`
   with `points=True`) writes `reports/points.md`, one row per point with every
   metric. The `library` entry lists each device's valid combinations
   (`combinations`, keyed by device id), so an agent can give start rows the
   table holds.
5. **The report prints each metric at its worst corner and names it**: "Best
   observed" and "Top feasible candidates" read `NF_3G=10.27 dB (ss)`; a
   constrained metric at the corner its verdict is judged on, the others at the
   objective's worst corner; a one-corner run prints as before.
6. **The run's lines say what happened**: a metric that gave no value on some
   points (`no_value:nil`, a P1dB with no compression point on a low-bias point)
   is reported as that and not as an expression to fix; a spec whose devices
   come from a library table has no current design "for its own reason", not
   "because the spec has EM devices"; the line `the model proposes 0 of the N new
   points` is plain, not a WARNING, when they are all start points or when the
   model proposes some of the points left to the run's `total`.
7. **Waveform exports are real CSV files, at full precision, written from the
   vectors** (`docs/refactor/N100_OCEAN_EXPORT_SPEC.md`; the contract is
   `docs/waveform_export.md`). Each waveform asked for with `waveforms=` lands as
   `<name>.csv`: comma separated, one header row, `x,y` or `x,re,im` for a
   complex one, the x column named after its quantity and unit (`time_s`,
   `freq_Hz`), every value `%.17g` so each double reads back bit for bit; with
   `<name>.meta.json` (columns, units, the expression, the result, the point
   count, the precision); a family one file per member, `<name>__<i>.csv`, and
   `<name>.families.json`. OCEAN writes them from `drGetWaveformXVec` /
   `drGetWaveformYVec`, not through `ocnPrint`, and times each export apart from
   the metrics (`metrics/ocean.log`, `metrics/ocean_timing.tsv`, the child's
   trace). Before, the file was a blank-separated table with two header lines
   and six significant digits, which a long transient record could not survive
   (duplicate time stamps on read-back), and `ocnPrint` took minutes on it.
   **A waveform expression that returns a number is no longer written as an
   empty file**: the child is `metric_failed` with the issue
   `not_a_waveform:<type>`. **Readers of these files must change**: a comma
   CSV with a header row, not a blank-split table.
8. **Operating points are read only on the instances of a component type that
   reports `gm`** (`dataTypes()`, `outputParams(type)`, `outputs(?type type)`):
   diodes, sources, inductors and mutual inductors are not probed, so they write
   no OCN-6043 warning; a transistor that lacks a quantity still gets its
   warning; a result whose types cannot be listed is probed as before, with a
   line in the log. The table is byte for byte what it was.

## New

- `ic-opt run optimize PROJECT budget=B total=N [initial_trials=K]`,
  `ic-opt run signoff PROJECT corner=C budget=B total=N`; `.icopt/steps.json`.
- `ic-opt digest PROJECT --points`; `analyze.digest(points=True)`;
  `digest.json`'s `library.combinations` and `progress.pipelines`.
- `sim.corner.worst_values`, `sim.corner.worst_objective` (what the report
  prints); `Deck.describe()`, `Deck.snapshot()`, `Deck.support_files()`,
  `blocks.netlist.last_imported()`, `blocks.evaluate.plan_identity()`.
- `docs/waveform_export.md`: the waveform file contract.
- `skills/ic-opt/SKILL.md` step 4 and step 7: the procedure of a run an agent
  advises between batches (when to look, what to read, how to decide, the
  commands, what to report), written from 24 sessions.
- Tests: `tests/ic_opt/test_deck_identity.py` (13), `test_ocean_export.py`,
  `test_signoff_total.py` (5), and the new cases in `test_optimize.py`,
  `test_digest.py`, `test_digest_library.py`, `test_blocks.py`,
  `test_migrate_store.py`, `test_operating_points.py`.

## Measured

Every number below comes from the user's private platforms and recorded runs;
none of them is in this repository.

- **Agents advising between batches, four language models** (BACKLOG 0.14;
  `ic-opt-accept/llm_compare/RESULT_CN.md`): on the mixer library platform
  (`signoff`, 40 search points at tt, the top 5 re-checked at tt/ss/ff, three
  seeds), advice between batches ended no worse than the same seed without it
  in 11 of 12 runs across Sonnet, Opus, DeepSeek pro and flash (means -0.704,
  -0.720, -0.719, -0.689 against the baseline's -0.651); an agent's own start
  rows in place of the designer's, and an agent proposing every point itself,
  were mixed on the worst corner. A designer's notes file added to the advice
  changed the result within seed noise (+0.012 and +0.003 on average, 3 up, 3
  down). The position of a language model therefore stays what 0.5.0 decided:
  it writes the request, reads the digest and gives revocable advice; it does
  not replace the proposal kernel.
- **The deck in the pipeline identity**: the bug report's two reproduction
  scripts (`reproduce_deck_reuse.py`, `reproduce_bundle_identity.py`) report
  `bug_reproduced: false` on this code -- the same store given `dc=0` yields a
  new observation with the new value, and a changed support file is saved
  under a new deck directory with the old snapshot intact.
- **Waveform export on a real PSF** (OCEAN only, no Spectre): a 102 401-point
  transient signal exported in 2.7 s from OCEAN's start to its exit against
  157 s through `ocnPrint`; every time and value reads back equal to the
  vectors to the last bit. On a 1 008-transistor result the operating-point
  table is byte for byte the table of before, OCN-6043 warnings 4 045 -> 0,
  `ocean.log` 1.68 MB -> 8.8 KB, OCEAN 43.8 s -> 19.9 s
  (`ic-opt-accept/n100_accept/`).

## Known limits

- `fix_run` on a point an earlier version evaluated simulates it again; the
  plan's history line is the only notice. Whether it should say more is the
  user's call.
- `migrate-store` restamps 0.2.0's and pre-T15.2 rows to a pipeline fingerprint
  without a deck, as before; they are history, not reused.
- Waveform files written by 0.8.0 and earlier keep their six significant
  digits; nothing rewrites them.
- `total` is taken by `optimize` and `signoff`; `coarse_to_fine` sizes each
  step's design from its own budget.
- Proposals that depend on the design's size differ from 0.8.0's only for a run
  advanced in increments without `initial_trials`; a run made in one call
  proposes exactly as before (pinned by the recorded-run replays).
