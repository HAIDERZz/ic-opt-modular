"""Observation: one evaluated point. The observations table is the only fact table."""

from __future__ import annotations

from collections.abc import Iterable

from pydantic import AliasChoices, BaseModel, Field, model_serializer

from ic_opt.space import Point


class ChildResult(BaseModel):
    """Outcome of one child: a testbench × corner simulation or an EM device's measurement. The 0.2.0 release wrote
    the unit as ``testbench`` (T9.1 renamed it): its rows read as they are."""

    unit: str = Field(validation_alias=AliasChoices("unit", "testbench"))   # testbench id or device id
    corner: str | None = None
    status: str                                   # "ok" | "metric_failed" (ran; an expression returned nil / non-scalar) | "failed:<stage>"
    metrics: dict[str, float] = Field(default_factory=dict)
    issues: list[str] = Field(default_factory=list)
    sim_dir: str | None = None                    # relative to the project root
    seconds: float | None = None
    # T17.5 (D8): instance as the simulator reports it (`/M1`, `/I0/M3`) -> quantity (sim.ocean.OP_QUANTITIES) -> value;
    # transistors only (instances that report gm). None: not extracted -- older stores, EM devices, operating points
    # switched off, results without any. Shown to whoever reads the run; no strategy reads it.
    operating_points: dict[str, dict[str, float]] | None = None

    @model_serializer(mode="wrap")
    def _dump(self, handler):
        """Left out while None: a child without operating points is written as before T17.5, byte for byte."""
        data = handler(self)
        if self.operating_points is None:
            data.pop("operating_points", None)
        return data


class Observation(BaseModel):
    """One evaluated point. A point the evaluation schedule stopped early (T17.8) holds the children that ran and names
    the others in ``not_run``; code reads that field, never the "not simulated" line its ``issues`` carry for the reader."""

    obs_id: str
    params: dict[str, str]
    origin: str
    children: dict[str, ChildResult] = Field(default_factory=dict)   # "<unit>/<corner>" -> result
    not_run: list[str] = Field(default_factory=list)                 # children it was to run and did not, in the engine's order
    metrics: dict[str, float] = Field(default_factory=dict)          # aggregated across corners
    fom: float | None = None
    objective: float | None = None                                   # minimization form
    feasible: bool = False
    constraint_penalty: float = 0.0
    status: str                                   # "ok" | "metric_failed" | "constraint_failed" | "failed:<stage>"
    issues: list[str] = Field(default_factory=list)
    spec_fingerprint: str
    pipeline_fingerprint: str
    step: str = ""
    cache: dict[str, str] = Field(default_factory=dict)              # point-level stage -> "hit" | "miss"
    simulations: int | None = None                                   # what this point cost; None on observations recorded before 0.2.x
    started_at: str
    finished_at: str

    @model_serializer(mode="wrap")
    def _dump(self, handler):
        """``not_run`` is left out while empty: a point that ran every child is written as before T17.8, byte for byte."""
        data = handler(self)
        if not self.not_run:
            data.pop("not_run", None)
        return data

    @property
    def point(self) -> Point:
        return Point(self.params, self.origin)

    @property
    def key(self) -> str:
        return self.point.key

    def corners(self) -> set[str]:
        """The corners the point was evaluated at: those of its children and of the children it did not run -- a point
        stopped at its first corner was still evaluated at all of them; ``nominal`` for a corner-less child."""
        return {c.corner or "nominal" for c in self.children.values()} | {key.split("/", 1)[1] for key in self.not_run}

    def infeasibility_key(self, violation: float) -> tuple[int, float]:
        """Where the point ranks among infeasible points, smallest first (T17.8): the one that got furthest -- fewest
        children not run, a complete point none -- then the smallest ``violation`` over the constraints it was judged on.
        A point stopped early is not less infeasible for having had fewer of its constraints judged."""
        return len(self.not_run), violation


class Observations(list[Observation]):
    """A list with the few queries every block needs."""

    def feasible(self) -> Observations:
        return Observations(o for o in self if o.feasible)

    def best(self, k: int = 1) -> Observations:
        ranked = sorted(self.feasible(), key=lambda o: o.objective if o.objective is not None else float("inf"))
        return Observations(ranked[:k])

    def by_step(self, step: str) -> Observations:
        return Observations(o for o in self if o.step == step)

    def keys(self) -> set[str]:
        return {o.key for o in self}

    def points(self) -> list[Point]:
        return [o.point for o in self]

    @classmethod
    def of(cls, items: Iterable[Observation]) -> Observations:
        return cls(items)
