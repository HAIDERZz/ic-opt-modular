# T18.2A — some variables may only take combinations from a table: the search space and every strategy honour it

Status: specification (2026-10-01), for the coding subagent. Decision behind it: the user's answer of 2026-10-01 to
`T18_LIBRARY_DIRECTIONS_CN.md` section 7, question 1: a library device's candidates are the library's rows, each row
at its own electrical values taken onto the variables' grid ("rows are the candidates"). For the search space that
means: the variables of such a device (say `xfmr.Lp`, `xfmr.Ls`, `xfmr.k`) keep their ordinary grids, but only some
combinations of their levels exist -- the ones a row sits on. This task teaches the space and the strategies that,
with no knowledge of libraries: where the table comes from is the next task's (`T18.2B`, after `T18.1` lands).

Read `src/ic_opt/space.py`, `src/ic_opt/suggesters/base.py`, `src/ic_opt/suggesters/metric_gp/{__init__,coords,
candidates,region}.py`, `src/ic_opt/blocks/{optimize,points}.py` and `src/ic_opt/advice.py` before writing anything.

## 1. The contract

```python
# ic_opt/space.py
@dataclass(frozen=True)
class Table:
    names: tuple[str, ...]                  # variables of the spec, in the spec's order
    levels: tuple[tuple[int, ...], ...]     # the allowed combinations of their level indices: distinct, sorted
    label: str = ""                         # for messages, e.g. "device xfmr (library rows)"

def tables(spec: Spec) -> list[Table]: ...
```

- `space.tables(spec)` returns `ic_opt.library.link.tables(spec)`. This task creates `src/ic_opt/library/link.py`
  with that one function returning `[]` and a docstring stating the contract (T18.2B fills it in: it will read the
  spec's library devices and cache per process); `space` imports it inside the function. The tests of this task
  replace `link.tables` (monkeypatch) with hand-built tables. A table is checked once where it is first used:
  its names are variables of the spec and appear in one table only, its level indices lie within each variable's
  levels, it is not empty; a violation is a `ValueError` naming the label.
- A *valid point* is a grid point whose linked variables' levels are, table by table, one of the table's
  combinations. With no table every grid point is valid and **nothing changes**: every function and every strategy
  gives what it gave before (the existing tests pass untouched).
- Nearest combination: the Euclidean distance over the table's variables in unit coordinates -- each variable's
  range mapped to [0, 1], logarithmically when `space.log_scale(lower, upper)`, else linearly, a one-level variable
  at 0: the coordinates `metric_gp`'s `Coords` uses; one function computes them for `space` and `Coords` alike.
  Ties go to the combination that comes first in the table's sorted order.

## 2. `ic_opt/space.py`

- `snap(spec, raw)`: as today per variable; then, for each table, the linked variables take the combination
  nearest to their **raw** values (clipped to the bounds first). The other variables are untouched.
- `check(spec, params)`: as today, and each table's combination must be allowed; the `ValueError` names the table's
  label, the values given and the nearest allowed combination in the variables' own text.
- `grid_size(spec)`: the number of valid points (the free variables' product times each table's size).
- `points_from_params` follows from `check`.

## 3. `metric_gp`

- `Coords` knows the tables: `project(idx)` moves rows of level indices to the nearest valid point (section 1's
  distance, on the levels' unit coordinates), `valid(idx)` says which rows are valid, and the number of valid points
  is available. `design_raw` projects, so the initial design consists of valid points (duplicates are skipped by
  `space_filling` as two unit points that snap to one grid point are today).
- Candidates (`candidates.py`): every candidate set holds valid points only. `whole_grid` enumerates the valid points
  directly -- the free variables' product times each table's combinations -- and is used when their number is at
  most `MAX_CANDIDATES` (never `np.indices` over the full product, which may be millions when the valid points are
  a few hundred). `local` and `wide` project what they generate before `fresh` removes repeats and evaluated points.
  The random streams of a spec without tables must stay what they are.
- Advice (`candidates.Advised`, section 6.2 of the T17.1.5 specification): a candidate brought inside an advice is
  projected afterwards; if the projection leaves the advice it is not a candidate of an advised slot (`contains`
  decides on the projected row). Free slots are unaffected. `ic_opt.advice.check` keeps accepting ranges and fixed
  levels on a linked variable; an advice's start rows must be valid points (`space.check` refuses them otherwise).
- The centre of the search region is an evaluated point; if it is not valid (the table changed since) it is
  projected before candidates are drawn around it.
- Everything else (models, region replay, selection, free-slot positions, tags) is untouched.

## 4. The other strategies and the point blocks

- `opt.suggest` (`blocks/optimize.py`) already snaps every raw vector with `space.snap` and drops repeats, so
  `openbox_*`, `turbo`, `random`, `sobol` and `latin_hypercube` hand out valid points once `snap` projects. Their
  proposals may collapse onto one combination more often than onto one grid point; the four attempts and the random
  filler that exist today cover it. Add nothing strategy-specific unless a test shows a batch coming back short
  while valid points remain; then fix it in `suggest`, not in the strategies.
- `base.space_filling` ends on `space.grid_size(spec)`: with tables that is the valid count.
- `blocks/points.py`: `sobol`, `grid` (the valid points; with `per_dim`, the chosen levels projected and repeats
  dropped), `one_at_a_time` (each move snapped to a valid point, repeats dropped; the centre must be valid),
  `fixed` (through `check`).

## 5. Tests (`tests/ic_opt/test_space_tables.py`; fakes as `tests/ic_opt/test_metric_gp.py` and `fakes.py` use)

With `link.tables` replaced by a function returning one table over three of a six-variable spec's variables (a few
dozen combinations out of a few thousand; one of the three logarithmic), and a second case with two tables:

1. `space.snap` gives valid points only and leaves the free variables as they are; an exact valid point is returned
   unchanged; ties resolve as stated. `space.check` accepts valid points and refuses others with the nearest
   combination in the message. `space.grid_size` is the valid count.
2. `Coords.project` and `space.snap` agree on level rows; `Coords.valid`; `design_raw` yields valid points.
3. `candidates.whole_grid` enumerates exactly the valid points minus the excluded ones, without building the full
   product (assert on a spec whose full product would be more than 10 million points: it returns in well under a
   second); `local` and `wide` hold valid points only.
4. `opt.suggest` for `metric_gp`, `random`, `sobol`, `openbox_gp_eic` (and `turbo` when it imports, else skipped):
   over several batches on a fake history every point is valid, no point repeats, and a batch is full while valid
   points remain; with every valid point evaluated but a few, the last batch returns exactly those few.
5. The same spec, history and seed give the same batch twice. With no table (`link.tables` as shipped) the batches
   of `tests/ic_opt/test_metric_gp.py`'s fixtures are what they were (that file passes unchanged).
6. Advice: a range on a linked variable narrows the advised slots' points to combinations inside it; a start row
   that is not a valid point is refused by `advise` with the nearest combination; free slots still come from the
   whole valid space.
7. `points.sobol`, `points.grid`, `points.one_at_a_time`, `points.fixed` under a table.

Targeted run: `tests/ic_opt/test_space_tables.py tests/ic_opt/test_metric_gp.py tests/ic_opt/test_spec_space_objective.py`
and every test file that covers `opt.suggest`, `points.*` and the advice (find them with grep); then
`ruff check src tests`.

## 6. Working rules for the coder

- Branch `t18-2a-allowed-combinations` in its own worktree (`git worktree add /home/zzchen/Agent_virtuoso/EDA_AI_AGENT/worktrees/t18-2a-allowed-combinations -b t18-2a-allowed-combinations main`);
  commits on that branch only; never touch `main`, never push.
- Another agent works at the same time on `t18-1-library-index` (`src/ic_opt/library/` except the new `link.py`,
  `src/ic_opt/blocks/library.py`, `src/ic_opt/em/pcell/`, the library documents). Do not edit those; `spec.py`,
  `stages/`, `blocks/evaluate.py`, `blocks/doctor.py`, `observation.py` and the digest are the next task's: leave
  them alone too.
- Python: `/home/zzchen/Agent_virtuoso/EDA_AI_AGENT/ic-opt-modular/.venv/bin/python` with `PYTHONPATH=<worktree>/src`
  (check `import ic_opt` resolves to the worktree); never `uv run`, `uv sync`, `pip`. No simulator, no network.
- The machine is running the user's simulations under a thread budget. Run tests with `OMP_NUM_THREADS=1
  OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1`, at most two test processes at a time, never the whole suite, never `-n`.
- One commit per coherent piece, each message saying what and why; trailer
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Hand back: branch and commits; the verbatim test and ruff output (last 30 lines); where the one distance function
  lives and who calls it; what the specification left open and how it was read (a numbered list); what could not be
  done and why.
