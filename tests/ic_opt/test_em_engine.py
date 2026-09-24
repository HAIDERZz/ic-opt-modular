"""T9.1: the engine's generic EM-ready extensions — device child chain, both chains at once, point-stage cache, host slots — and the spec's EM sections."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from ic_opt import migrate_store
from ic_opt.em import emx
from ic_opt.eval import engine
from ic_opt.eval.stage import Resources, StageContext, StageFailure
from ic_opt.executor import LocalExecutor
from ic_opt.observation import ChildResult
from ic_opt.site import EnvelopeError, HostLimits
from ic_opt.space import Point
from ic_opt.spec import Spec
from ic_opt.stages.em_chain import Measure as EmMeasure
from ic_opt.stages.em_chain import emx_stages
from ic_opt.store import RunStore
from tests.ic_opt.fakes import FAKE_HOST, minimal_spec

EM_RESOURCES = {"threads": 4, "memory_gb": 32, "timeout_s": 600}      # required in every em section

# -- a tiny fake EM pipeline: build (point, cached) -> measure (device child) ------------


@dataclass
class Built:
    values: dict[str, float]          # device id -> "size"


class Build:
    """Point-level, cacheable: computes one number per device; counts real runs."""

    name = "build"
    level = "point"
    resources = Resources(threads=4, memory_gb=32.0)

    def __init__(self, cacheable: bool = True) -> None:
        self.cacheable = cacheable
        self.runs = 0

    def fingerprint(self, point: Point, ctx: StageContext) -> str | None:
        return f"fp-{point.params['d.od']}" if self.cacheable else None

    def run(self, point: Point, ctx: StageContext) -> Built:
        self.runs += 1
        return Built({d.id: float(point.params["d.od"]) * 2 for d in ctx.spec.devices})

    def save(self, out: Built, directory: Path) -> None:
        (directory / "built.json").write_text(json.dumps(out.values))

    def load(self, directory: Path, point: Point, ctx: StageContext) -> Built:
        return Built(json.loads((directory / "built.json").read_text()))


class Measure:
    name = "measure"
    level = "child"
    unit = "device"
    resources = Resources()

    def fingerprint(self, inp, ctx):
        return None

    def run(self, built: Built, ctx: StageContext) -> ChildResult:
        if built.values[ctx.unit] > 100:
            raise StageFailure("too big")
        return ChildResult(unit=ctx.unit, corner=None, status="ok", metrics={"Q": built.values[ctx.unit]})


class CircuitChild:
    """Stands in for the Spectre chain: one testbench metric that also sees the device value."""

    name = "circuit"
    level = "child"
    unit = "testbench"
    resources = Resources(threads=10)

    def fingerprint(self, inp, ctx):
        return None

    def run(self, built: Built, ctx: StageContext) -> ChildResult:
        bonus = {"ss": 1.0}.get(ctx.corner or "", 0.0)
        return ChildResult(unit=ctx.unit, corner=ctx.corner, status="ok", metrics={"NF": 6.0 + bonus})


def em_spec(*, testbenches=True, corners=()) -> Spec:
    d = minimal_spec()
    d["devices"] = [{"id": "d", "generator": "demo", "profile": "demo_6m", "ports": ["P1", "N1"], "fixed": {"turns": 1}}]
    d["variables"] = [{"name": "d.od", "kind": "integer", "lower": "20", "upper": "60", "step": "10"},
                      {"name": "F", "kind": "integer", "lower": "20", "upper": "30", "step": "2"}]
    d["metrics"] = [{"name": "Q", "unit": "ratio", "device": "d", "quantity": "Qp_peak"}]
    d["constraints"] = [{"metric": "Q", "op": "gt", "value": "50"}]
    d["objective"] = {"direction": "maximize", "expression": "Q"}
    if testbenches:
        d["metrics"].append({"name": "NF", "unit": "dB", "expression": "nf()", "testbench": "tb"})
        d["objective"] = {"direction": "minimize", "expression": "NF - Q/100"}
    else:
        d["testbenches"] = []
    d["corners"] = [{"id": c} for c in corners]
    d["budget"] = {"max_simulations": 50}
    return Spec.model_validate(d)


def run(spec, pipeline, points, tmp_path, **kw):
    store = RunStore(tmp_path)
    return engine.run(spec, pipeline, points, LocalExecutor(store.root / "sims"), store, **{"limits": FAKE_HOST, **kw}), store


# -- engine ---------------------------------------------------------------------------


def test_device_chain_alone_yields_one_child_per_device(tmp_path):
    spec = em_spec(testbenches=False)
    build = Build()
    obs, _ = run(spec, [build, Measure()], [Point({"d.od": "30", "F": "20"}, "user")], tmp_path)
    o = obs[0]
    assert set(o.children) == {"d/nominal"} and o.children["d/nominal"].unit == "d"
    assert o.status == "ok" and o.metrics == {"Q": 60.0} and o.fom == 60.0 and o.cache == {"build": "miss"}


def test_both_chains_share_the_point_output_and_device_metrics_reach_every_corner(tmp_path):
    spec = em_spec(corners=("tt", "ss"))
    obs, _ = run(spec, [Build(), Measure(), CircuitChild()], [Point({"d.od": "30", "F": "20"}, "user")], tmp_path)
    o = obs[0]
    assert set(o.children) == {"tb/tt", "tb/ss", "d/nominal"}
    assert o.status == "ok" and o.metrics == {"Q": 60.0, "NF": 7.0}          # worst case: ss (NF 7.0) still carries the device's Q
    assert o.fom == pytest.approx(7.0 - 0.6)


def test_point_stage_failure_marks_every_child_of_both_chains(tmp_path):
    spec = em_spec()

    class Boom(Build):
        def run(self, point, ctx):
            raise StageFailure("bad geometry")

    obs, _ = run(spec, [Boom(cacheable=False), Measure(), CircuitChild()], [Point({"d.od": "30", "F": "20"}, "user")], tmp_path)
    o = obs[0]
    assert o.status == "failed:build" and {c.status for c in o.children.values()} == {"failed:build"}
    assert set(o.children) == {"tb/nominal", "d/nominal"}


def test_device_child_failure_fails_the_point_but_runs_the_other_chain(tmp_path):
    spec = em_spec()
    obs, _ = run(spec, [Build(), Measure(), CircuitChild()], [Point({"d.od": "60", "F": "20"}, "user")], tmp_path)
    o = obs[0]
    assert o.children["d/nominal"].status == "failed:measure" and o.children["tb/nominal"].status == "ok"
    assert o.status == "failed:measure" and o.issues == ["d/nominal: too big"]


def test_point_stage_cache_hits_across_points_and_survives_processes(tmp_path):
    spec = em_spec(testbenches=False)
    build = Build()
    same_od = [Point({"d.od": "30", "F": "20"}, "user"), Point({"d.od": "30", "F": "22"}, "user"), Point({"d.od": "40", "F": "20"}, "user")]
    obs, store = run(spec, [build, Measure()], same_od, tmp_path, parallel_jobs=1)
    assert build.runs == 2 and [o.cache["build"] for o in obs] == ["miss", "hit", "miss"]
    assert (store.root / "cache" / "build" / "fp-30" / ".complete").exists()

    fresh = Build()                                            # a new process: the cache directory alone serves the hit
    again, _ = run(spec, [fresh, Measure()], [Point({"d.od": "40", "F": "24"}, "user")], tmp_path)
    assert fresh.runs == 0 and again[0].cache == {"build": "hit"} and again[0].metrics == {"Q": 80.0}

    uncached = Build(cacheable=False)
    run(spec, [uncached, Measure()], [Point({"d.od": "40", "F": "26"}, "user")], tmp_path)
    assert uncached.runs == 1


def test_workers_are_capped_by_the_host_entry_of_the_heaviest_stage():
    spec = em_spec()
    pipeline = [Build(), Measure(), CircuitChild()]              # threads: circuit 10; memory: build 32 GB
    assert engine.workers_for(spec, pipeline, 10, HostLimits(max_threads=128, max_memory_gb=128)) == 4     # memory-bound: 128/32
    assert engine.workers_for(spec, pipeline, 10, HostLimits(max_threads=40, max_memory_gb=1024)) == 4     # thread-bound: 40/10
    assert engine.workers_for(spec, [CircuitChild()], 3, HostLimits(max_threads=128, max_memory_gb=1)) == 3


def test_a_stage_bigger_than_the_host_is_refused_not_run_alone():
    spec = em_spec()
    pipeline = [Build(), Measure(), CircuitChild()]
    with pytest.raises(EnvelopeError, match="stage circuit needs 10 threads / 0 GB but the executor host allows max_threads 8 / max_memory_gb 1024"):
        engine.workers_for(spec, pipeline, 10, HostLimits(max_threads=8, max_memory_gb=1024))     # was: one worker anyway
    with pytest.raises(EnvelopeError, match="stage build needs 4 threads / 32 GB .* max_memory_gb 16"):
        engine.workers_for(spec, pipeline, 10, HostLimits(max_threads=128, max_memory_gb=16))


def test_the_engine_refuses_before_anything_runs(tmp_path):
    build = Build()
    with pytest.raises(EnvelopeError, match="stage build"):
        run(em_spec(testbenches=False), [build, Measure()], [Point({"d.od": "30", "F": "20"}, "user")], tmp_path,
            limits=HostLimits(max_threads=64, max_memory_gb=16))
    assert build.runs == 0 and RunStore(tmp_path).observations() == []


def test_children_of_follows_the_pipeline_not_the_spec(tmp_path):
    spec = em_spec(corners=("tt", "ss"))
    keys = [c.key for c in engine.children_of(spec, [Build(), CircuitChild()], spec.corner_ids)]
    assert keys == ["tb/tt", "tb/ss"]
    keys = [c.key for c in engine.children_of(spec, [Build(), Measure()], spec.corner_ids)]
    assert keys == ["d/nominal"]
    with pytest.raises(ValueError, match="no children"):
        run(em_spec(testbenches=False), [Build(), CircuitChild()], [Point({"d.od": "30", "F": "20"}, "user")], tmp_path)


def test_budget_counts_children_of_both_chains(tmp_path):
    spec = em_spec(corners=("tt", "ss"))
    spec.budget.max_simulations = 5                            # 3 per point: 2 testbench sims + 1 device
    with pytest.raises(engine.BudgetExceeded, match="3 needed per point"):
        run(spec, [Build(), Measure(), CircuitChild()], [Point({"d.od": "30", "F": "20"}, "user"), Point({"d.od": "40", "F": "20"}, "user")], tmp_path)


# -- spec ------------------------------------------------------------------------------


def test_spec_routes_variables_to_devices_and_circuit():
    spec = em_spec()
    assert spec.device_fields(spec.device("d")) == {"od": "d.od"} and spec.circuit_variables == ["F"]
    assert spec.metrics_for_device("d")[0].quantity == "Qp_peak" and spec.metrics_for("tb")[0].name == "NF"

    d = em_spec(testbenches=False).model_dump(mode="json")      # single device, bare names: every variable is a generator field
    d["variables"] = [{"name": "od", "kind": "integer", "lower": "20", "upper": "60", "step": "10"},
                      {"name": "w", "kind": "continuous_step", "lower": "2", "upper": "4", "step": "0.5"}]
    bare = Spec.model_validate(d)
    assert bare.device_fields(bare.device("d")) == {"od": "od", "w": "w"} and bare.circuit_variables == []

    d["devices"][0]["variables"] = {"outer_diameter_um": "od"}   # explicit mapping wins; unmapped names stay circuit variables
    explicit = Spec.model_validate(d)
    assert explicit.device_fields(explicit.device("d")) == {"outer_diameter_um": "od"} and explicit.circuit_variables == ["w"]


def test_spec_rejects_bad_em_sections():
    base = em_spec().model_dump(mode="json")
    bad = dict(base); bad["metrics"] = [{"name": "Q", "unit": "x", "device": "d"}]
    with pytest.raises(ValueError, match="either expression"):
        Spec.model_validate(bad)
    bad = dict(base); bad["metrics"] = [{"name": "Q", "unit": "x", "quantity": "Qp", "device": "nope"}]
    with pytest.raises(ValueError, match="unknown device"):
        Spec.model_validate(bad)
    bad = dict(base); bad["bindings"] = [{"testbench": "tb", "instance": "NPORT0", "device": "d", "terminals": ["P1", "P1"]}]
    with pytest.raises(ValueError, match="permutation"):
        Spec.model_validate(bad)
    bad = dict(base); bad["devices"][0]["variables"] = {"od": "missing"}
    with pytest.raises(ValueError, match="unknown variable"):
        Spec.model_validate(bad)
    bad = dict(base); bad["testbenches"] = []; bad["devices"] = []
    with pytest.raises(ValueError, match="at least one testbench or one device"):
        Spec.model_validate(bad)
    bad = dict(base); bad["em"] = {"process_file": "relative.proc", "frequencies": [1e9], **EM_RESOURCES}
    with pytest.raises(ValueError, match="absolute"):
        Spec.model_validate(bad)
    bad = dict(base); bad["em"] = {"process_file": "/p/x.proc", "frequencies": [1e9], "extra_args": ["--max-memory=1G"], **EM_RESOURCES}
    with pytest.raises(ValueError, match="dedicated field"):
        Spec.model_validate(bad)


def test_default_topology_and_em_settings():
    spec = em_spec()
    assert spec.device("d").topology.drives == [("P1", "N1")] and spec.device("d").topology.grounded == []
    d = spec.model_dump(mode="json")
    d["devices"][0]["ports"] = ["P1", "N1", "P2", "N2", "CTP"]
    d["devices"][0]["topology"] = None
    d["em"] = {"process_file": "/site/n28.proc", "frequencies": {"start_hz": 0, "stop_hz": 200e9, "step_hz": 1e9}, "three_d_metals": ["M9", "M8"],
               **EM_RESOURCES}
    four = Spec.model_validate(d)
    assert four.device("d").topology.drives == [("P1", "N1"), ("N2", "P2")] and four.device("d").topology.grounded == ["CTP"]
    assert four.em.threads == 4 and four.em.memory_gb == 32.0 and four.em.simultaneous_frequencies == 0 and four.em.accuracy == "standard"
    assert four.fingerprint() != spec.fingerprint()


GOLDEN_EM = {              # every field spelled out: the pinned fingerprints below belong to exactly this spec
    "project": "golden_em",
    "devices": [{"id": "ind", "generator": "clean_port_ind_sym", "profile": "demo_6m", "ports": ["P1", "N1"],
                 "fixed": {"opening_um": 8.0, "lead_length_um": 20.0, "metal": "6"}, "variables": {"outer_diameter_um": "outer_diameter_um"}}],
    "em": {"process_file": "/site/demo.proc", "mode": "full_wave", "frequencies": {"start_hz": 0, "stop_hz": 6e10, "step_hz": 1e9},
           "three_d_metals": ["M6", "M5"], "threads": 4, "memory_gb": 16, "timeout_s": 3600},
    "variables": [{"name": "outer_diameter_um", "kind": "continuous_step", "lower": "80", "upper": "160", "step": "1"}],
    "metrics": [{"name": "L", "unit": "H", "device": "ind", "quantity": "Lp_lf"}],
    "objective": {"direction": "maximize", "expression": "L"},
    "simulator": {"parallel_jobs": 4, "threads_per_run": 1, "timeout_s": 600},
    "budget": {"max_simulations": 100},
}


def test_emx_resources_are_not_part_of_the_problem():
    base, em = Spec.model_validate(GOLDEN_EM), GOLDEN_EM["em"]
    for how in ({"threads": 16}, {"memory_gb": 200}, {"timeout_s": 60}, {"verbose": None}, {"binary": "/opt/emx/bin/emx"}):
        assert Spec.model_validate({**GOLDEN_EM, "em": {**em, **how}}).fingerprint() == base.fingerprint(), how
    for what in ({"three_d_metals": ["M6"]}, {"accuracy": "high"}, {"frequencies": {**em["frequencies"], "stop_hz": 8e10}}):
        assert Spec.model_validate({**GOLDEN_EM, "em": {**em, **what}}).fingerprint() != base.fingerprint(), what
    assert "em" in base.problem() and not {"threads", "memory_gb", "timeout_s", "verbose", "binary"} & set(base.problem()["em"])
    # pinned (see test_engine.test_fingerprints_are_pinned): the legacy value is what the code before T15.2 wrote
    assert base._legacy_fingerprint() == "db88575a8bf11aa2" and base.fingerprint() == "6774ae91076aa65d"


def test_the_legacy_emx_identities_reproduce_what_the_old_code_stamped():
    """migrate-store recognises pre-T15.2 stamps with these frozen formulas; the values were computed by that code. If they
    move, a field added to EmSettings reached the dump: keep it out while unset (as ``Topology._dump`` does)."""
    spec = Spec.model_validate(GOLDEN_EM)
    assert migrate_store.legacy_emx_identity(spec.em) == (
        '{"accuracy":"standard","extra_args":[],"frequencies":{"num_steps":null,"start_hz":0.0,"step_hz":1000000000.0,'
        '"stop_hz":60000000000.0},"mode":"full_wave","modes":[],"process_file":"/site/demo.proc","s_impedance":50.0,'
        '"simultaneous_frequencies":0,"three_d_metals":["M6","M5"],"via_inductance":[],"via_separation_um":null,"via_sidewalls":[]}')
    assert migrate_store.legacy_pipeline_fingerprint([*emx_stages(spec), EmMeasure()]) == "400ef5c50050138b"
    ports = emx.numbered_ports(["P1", "N1"], {"P1": "G01", "N1": "G02"})
    key = {"gds_sha256": "g" * 64, "ports": ports, "proc_sha256": "p" * 64}
    assert migrate_store.legacy_emx_cache_key(spec.em, **key) == "45305ad609148c03175e6f28"
    assert emx.fingerprint(spec.em, **key) == "d5165498e1c35f69953b46d2"                # and the new key, pinned too
