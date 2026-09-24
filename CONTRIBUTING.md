# Contributing

IC-Opt is a small package: `spec.py` (WHAT), `blocks/` (the verbs a recipe
composes), `eval/engine.py` + `stages/` (evaluation), `suggesters/` (stateless
proposal from the observation table), `recipes/` (HOW), `cli.py`.

Rules of thumb:

- New need → new block, new stage or new recipe. Not a new mode, flag or workflow.
- Blocks take plain arguments and return values; state lives in `RunStore` only.
- Suggesters rebuild from `observations.jsonl`; nothing else is persisted.
- Remote paths are touched only through an `Executor` (see `docs/adr/0001-remote-filesystem-boundary.md`).
- Keep protective code out until a real run needs it; prefer a root-cause fix over a tolerance.

```bash
uv venv .venv && uv pip install -e vendor/open-box -e ".[dev,report]"   # add the turbo extra with -e vendor/TuRBO, or prf, in the same command
.venv/bin/python -m pytest -q            # fake Spectre host, no Cadence needed
.venv/bin/ruff check src tests
```

One task, one commit. Real Spectre smoke runs (local + `--ssh-profile`) are
recorded in `docs/refactor/EXECUTION_PLAN_CN.md` §3.
