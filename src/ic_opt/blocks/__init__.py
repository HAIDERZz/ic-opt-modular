"""The block registry and the ``ic_opt.blocks as b`` namespace recipes use.

Every block is a plain function registered with :func:`block`; ``describe``
prints its signature and docstring, and the CLI can call any block by name.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass

from ic_opt.blocks import analyze as _analyze
from ic_opt.blocks import library as _library
from ic_opt.blocks import points as _points
from ic_opt.blocks.doctor import doctor as _doctor
from ic_opt.blocks.evaluate import evaluate as _evaluate
from ic_opt.blocks.netlist import import_netlists as _import_netlists
from ic_opt.blocks.optimize import optimize as _optimize
from ic_opt.blocks.optimize import suggest as _suggest
from ic_opt.spec import load_spec as _load_spec
from ic_opt.stages import spectre_pipeline
from ic_opt.store import RunStore


@dataclass(frozen=True)
class Block:
    name: str
    summary: str
    fn: Callable[..., object]

    def describe(self) -> str:
        sig = inspect.signature(self.fn)
        doc = inspect.getdoc(self.fn) or ""
        return f"{self.name}{sig}\n  {self.summary}\n\n{doc}\n"


REGISTRY: dict[str, Block] = {}


def block(name: str, summary: str):
    def register(fn: Callable[..., object]) -> Callable[..., object]:
        REGISTRY[name] = Block(name, summary, fn)
        return fn

    return register


def describe(name: str) -> str:
    return REGISTRY[name].describe()


# -- the namespace ------------------------------------------------------------------

load_spec = block("spec.load", "Read spec.yaml into a validated Spec")(_load_spec)
doctor = block("env.doctor", "Check executor, tools, license, exports, site envelope, budget")(_doctor)
import_netlists = block("netlist.import", "Fetch Maestro exports and template them into a Deck")(_import_netlists)
points_fixed = block("points.fixed", "User-listed points, validated against the grid")(_points.fixed)
points_sobol = block("points.sobol", "Space-filling points (sobol / latin_hypercube / random)")(_points.sobol)
points_grid = block("points.grid", "The full grid or an evenly thinned one")(_points.grid)
points_one_at_a_time = block("points.one_at_a_time", "Center plus ± one grid step per variable")(_points.one_at_a_time)
points_from = block("points.from", "Points of existing observations (e.g. top-k for a sign-off)")(_points.from_observations)
evaluate = block("sim.evaluate", "Run the pipeline on points and append observations")(_evaluate)
suggest = block("opt.suggest", "Propose n new points from observations (stateless)")(_suggest)
optimize = block("opt.optimize", "Loop suggest ⇄ evaluate until the step holds its budget")(_optimize)
best = block("analyze.best", "Best feasible observations under the corner policy")(_analyze.best)
report = block("analyze.report", "report.md + report.html with six sections and four figures")(_analyze.report)
lib_load = block("lib.load", "Build or read each stratum's dataset; integrity evidence")(_library.load)
lib_coverage = block("lib.coverage", "What a stratum covers: rows, ranges, levels, quantities")(_library.coverage)
lib_query = block("lib.query", "Measured values, or predictions with calibrated bounds and domain verdicts")(_library.query)
lib_suggest = block("lib.suggest", "Designs meeting targets with margin: measured first, then built predictions")(_library.suggest)
score_model = _analyze.score_model
store = RunStore

__all__ = [
    "REGISTRY", "Block", "best", "block", "describe", "doctor", "evaluate", "import_netlists", "lib_coverage", "lib_load", "lib_query", "lib_suggest",
    "load_spec", "optimize",
    "points_fixed", "points_from", "points_grid", "points_one_at_a_time", "points_sobol", "report", "score_model",
    "spectre_pipeline", "store", "suggest",
]
