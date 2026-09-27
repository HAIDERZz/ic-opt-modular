"""Problem name -> :class:`icopt_bench.problem.Problem`, across both families, plus the held-out guard.

AnalogGym problems (T17.0a-B2, not built yet) are added through ``icopt_bench.analoggym.problems()``; imported
lazily and only if present, so this module works before that task lands.
"""

from __future__ import annotations

import importlib
import json
import os
from collections.abc import Callable
from pathlib import Path

from icopt_bench.problem import Problem
from icopt_bench.synthetic import PROBLEMS as _SYNTHETIC

_SPLIT_PATH = Path(__file__).resolve().parent.parent / "split.json"


def _analoggym() -> dict[str, Callable[[], Problem]]:
    try:
        from icopt_bench.analoggym import (
            problems as _ag_problems,  # not built yet (T17.0a-B2): absent until then
        )
    except ImportError:
        return {}
    return _ag_problems()


def _extra() -> dict[str, Callable[[], Problem]]:
    """Test-only extra problems, named by a module path in ``ICOPT_BENCH_EXTRA_PROBLEMS`` (its ``PROBLEMS`` dict is
    merged in): how ``tests/benchmarks/test_sweep.py`` gives a real ``icopt_bench.loop`` subprocess a problem that
    sleeps on demand, without adding test fixtures to the product's own registry."""
    module_name = os.environ.get("ICOPT_BENCH_EXTRA_PROBLEMS")
    if not module_name:
        return {}
    return importlib.import_module(module_name).PROBLEMS


def _all() -> dict[str, Callable[[], Problem]]:
    out = dict(_SYNTHETIC)
    out.update(_analoggym())
    out.update(_extra())
    return out


def _split() -> dict:
    if not _SPLIT_PATH.exists():
        return {"seed": None, "development": [], "heldout": []}
    return json.loads(_SPLIT_PATH.read_text(encoding="utf-8"))


def get(name: str) -> Problem:
    factories = _all()
    if name not in factories:
        raise KeyError(f"unknown benchmark problem {name!r}; known: {sorted(factories)}")
    return factories[name]()


def names(family: str | None = None, heldout: bool | None = None) -> list[str]:
    """Problem names, optionally filtered by ``family`` ("synthetic" / "analoggym") and by held-out status."""
    held = set(_split().get("heldout", []))
    factories = _all()
    out = []
    for name, factory in factories.items():
        if heldout is not None and (name in held) != heldout:
            continue
        if family is not None and factory().family != family:
            continue
        out.append(name)
    return sorted(out)


def is_heldout(name: str) -> bool:
    return name in set(_split().get("heldout", []))
