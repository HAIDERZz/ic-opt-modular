"""Observation: one evaluated point. The observations table is the only fact table."""

from __future__ import annotations

from collections.abc import Iterable

from pydantic import BaseModel, Field

from ic_opt.space import Point


class ChildResult(BaseModel):
    """Outcome of one child: a testbench × corner simulation or an EM device's measurement."""

    unit: str                                     # testbench id or device id
    corner: str | None = None
    status: str                                   # "ok" | "failed:<stage>"
    metrics: dict[str, float] = Field(default_factory=dict)
    issues: list[str] = Field(default_factory=list)
    sim_dir: str | None = None                    # relative to the project root
    seconds: float | None = None


class Observation(BaseModel):
    obs_id: str
    params: dict[str, str]
    origin: str
    children: dict[str, ChildResult] = Field(default_factory=dict)   # "<unit>/<corner>" -> result
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

    @property
    def point(self) -> Point:
        return Point(self.params, self.origin)

    @property
    def key(self) -> str:
        return self.point.key


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
