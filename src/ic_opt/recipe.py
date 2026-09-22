"""Run: what every recipe receives — project dir, Spec, RunStore, Executor, site limits.

A recipe is a Python file (or a built-in) with ``main(run: Run, **params)``.
``--plan`` sets plan mode: blocks that would simulate print what they would do
and return nothing, so the recipe's shape and cost are visible before any
Spectre process starts.
"""

from __future__ import annotations

import contextvars
import importlib
import importlib.util
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ic_opt import site as site_module
from ic_opt.executor import Executor, LocalExecutor, SshExecutor
from ic_opt.spec import Spec, load_spec
from ic_opt.store import RunStore

PLAN_MODE: contextvars.ContextVar[bool] = contextvars.ContextVar("ic_opt_plan_mode", default=False)
BUILTIN_RECIPES = ("optimize", "fix_run", "coarse_to_fine", "signoff")


@dataclass
class Run:
    project: Path
    spec: Spec
    store: RunStore
    executor: Executor
    cshrc: str | None
    site: site_module.Site

    @property
    def plan(self) -> bool:
        return PLAN_MODE.get()

    @property
    def jobs(self) -> int:
        """Concurrent simulations: the spec's parallel_jobs, capped by the site envelope."""
        return min(self.spec.simulator.parallel_jobs, self.site.slots(self.spec.simulator.threads_per_run))

    def note(self, text: str) -> None:
        print(f"[{'plan' if self.plan else 'run'}] {text}")


def load_run(project: str | Path, *, ssh_profile: str | None = None, cshrc: str | None = None,
             site: site_module.Site | None = None) -> Run:
    project = Path(project).resolve()
    spec = load_spec(project / "spec.yaml")
    store = RunStore(project)
    site = site or site_module.load()
    # cshrc is a path on the simulation host: given explicitly, never guessed from the controller's disk (ADR-0001)
    cshrc = cshrc or os.environ.get("IC_OPT_CADENCE_CSHRC") or site.cshrc
    if ssh_profile:
        executor: Executor = SshExecutor(ssh_profile, f"~/.ic-opt/scratch/{spec.project}")
    else:
        executor = LocalExecutor(store.root / "sims")
    return Run(project, spec, store, executor, cshrc, site)


def load_recipe(name_or_path: str) -> Callable[..., object]:
    """Built-in name (``optimize``) or a path to a ``.py`` file exposing ``main(run, **params)``."""
    path = Path(name_or_path)
    if path.suffix == ".py" and path.exists():
        spec = importlib.util.spec_from_file_location(path.stem, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    elif name_or_path in BUILTIN_RECIPES:
        module = importlib.import_module(f"ic_opt.recipes.{name_or_path}")
    else:
        raise ValueError(f"unknown recipe {name_or_path!r}; built-ins: {', '.join(BUILTIN_RECIPES)}")
    main = getattr(module, "main", None)
    if not callable(main):
        raise TypeError(f"recipe {name_or_path} has no main(run, **params)")
    return main
