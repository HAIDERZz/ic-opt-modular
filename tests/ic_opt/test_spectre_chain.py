from pathlib import Path

import pytest

from ic_opt.deck import Deck
from ic_opt.eval.stage import StageContext, StageFailure
from ic_opt.space import Point
from ic_opt.stages import spectre_pipeline
from ic_opt.store import RunStore
from tests.ic_opt.fakes import FakeSpectreExecutor, make_spec

TEMPLATE = "simulator lang=spectre\nparameters temperature=27 F={{F}} W={{W}}\ntran tran stop=10n\n"


def run_chain(tmp_path: Path, executor, corner=None):
    spec = make_spec()
    store = RunStore(tmp_path)
    deck = Deck(templates={("tb", corner): TEMPLATE})
    workdir = store.sim_dir("obs_0001", "tb", corner)
    ctx = StageContext(
        spec=spec, executor=executor, store=store, obs_id="obs_0001", workdir=workdir,
        remote_dir=executor.scratch(f"obs_0001/tb/{corner or 'nominal'}"), testbench="tb", corner=corner, cshrc="/env.csh",
    )
    value: object = Point({"F": "24", "W": "0.8u"}, "user")
    for stage in spectre_pipeline(spec, deck):
        value = stage.run(value, ctx)
    return value, ctx


def test_chain_produces_child_result_from_point(tmp_path):
    executor = FakeSpectreExecutor(tmp_path / ".icopt" / "sims", lambda p, tb, c: {"NF": 8.0 + int(p["F"]) / 100})
    child, ctx = run_chain(tmp_path, executor)

    assert child.status == "ok" and child.metrics == {"NF": 8.24} and child.testbench == "tb"
    assert [c["label"] for c in ctx.trace] == ["spectre", "ocean#1"]
    assert (ctx.workdir / "netlist" / "input.scs").read_text().splitlines()[1] == "parameters temperature=27 F=24 W=0.8u"
    assert executor.commands[0].startswith("spectre -64 input.scs +escchars +preset=ax +mt=10")
    assert (ctx.workdir / "metrics" / "probe.ocn").exists()


def test_spectre_failure_stops_the_chain(tmp_path):
    executor = FakeSpectreExecutor(tmp_path / ".icopt" / "sims", lambda p, tb, c: {}, fail_spectre=lambda tb, c: True)
    with pytest.raises(StageFailure, match="spectre exited 1"):
        run_chain(tmp_path, executor)
    assert (tmp_path / ".icopt" / "sims" / "obs_0001" / "tb" / "nominal" / "spectre.stderr").read_text().startswith("spectre: license")


def test_ocean_retries_then_fails(tmp_path):
    executor = FakeSpectreExecutor(tmp_path / ".icopt" / "sims", lambda p, tb, c: {}, fail_ocean=lambda tb, c: True)
    with pytest.raises(StageFailure, match="no scalars after 3 attempt"):
        run_chain(tmp_path, executor)
    assert sum(c.startswith("ocean") for c in executor.commands) == 3


def test_missing_metric_is_an_extract_failure_not_an_exception(tmp_path):
    executor = FakeSpectreExecutor(tmp_path / ".icopt" / "sims", lambda p, tb, c: {"NF": None})
    child, _ = run_chain(tmp_path, executor)
    assert child.status == "failed:extract" and child.metrics == {} and "non_scalar" in child.issues[0]
