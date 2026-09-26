from pathlib import Path

import pytest

from ic_opt import deck as deck_module
from ic_opt.deck import Deck
from ic_opt.eval.stage import StageContext, StageFailure
from ic_opt.localpath import literal
from ic_opt.space import Point
from ic_opt.stages import spectre_chain, spectre_pipeline
from ic_opt.stages.spectre_chain import Spectre
from ic_opt.store import RunStore
from tests.ic_opt.fakes import FakeSpectreExecutor, make_spec, minimal_spec

TEMPLATE = "simulator lang=spectre\nparameters temperature=27 F={{F}} W={{W}}\ntran tran stop=10n\n"


def run_chain(tmp_path: Path, executor, corner=None, spec=None, deck=None):
    spec = spec or make_spec()
    store = RunStore(tmp_path)
    deck = deck if deck is not None else Deck(templates={("tb", corner): TEMPLATE})
    workdir = store.sim_dir("obs_0001", "tb", corner)
    ctx = StageContext(
        spec=spec, executor=executor, store=store, obs_id="obs_0001", workdir=workdir,
        remote_dir=executor.scratch(f"obs_0001/tb/{corner or 'nominal'}"), unit="tb", corner=corner, cshrc="/env.csh",
    )
    value: object = Point({"F": "24", "W": "0.8u"}, "user")
    for stage in spectre_pipeline(spec, deck):
        value = stage.run(value, ctx)
    return value, ctx


def test_chain_produces_child_result_from_point(tmp_path):
    executor = FakeSpectreExecutor(tmp_path / ".icopt" / "sims", lambda p, tb, c: {"NF": 8.0 + int(p["F"]) / 100})
    child, ctx = run_chain(tmp_path, executor)

    assert child.status == "ok" and child.metrics == {"NF": 8.24} and child.unit == "tb"
    assert [c["label"] for c in ctx.trace] == ["spectre#1", "ocean#1"]
    assert (ctx.workdir / "netlist" / "input.scs").read_text().splitlines()[1] == "parameters temperature=27 F=24 W=0.8u"
    assert executor.commands[0].startswith("spectre -64 input.scs +escchars +preset=ax +mt=2")    # the fixture's threads_per_run
    assert (ctx.workdir / "metrics" / "probe.ocn").exists()


def test_a_bundle_with_names_windows_would_change_is_saved_and_rendered_through_literal_paths(tmp_path, monkeypatch):
    """N-21: every Maestro export carries amap/__dspf_information__. (a trailing dot, which ordinary Win32 paths strip),
    and a name may end in a space. Deck.save and render_netlist copy such a bundle through localpath.literal and keep
    every byte. On Linux these are ordinary names, so this guards the plumbing only; the Windows re-run checks Windows."""
    taken: list[Path] = []

    def spy(path, **kwargs):
        taken.append(Path(path))
        return literal(path, **kwargs)

    monkeypatch.setattr(deck_module, "literal", spy)
    monkeypatch.setattr(spectre_chain, "literal", spy)
    files = {".modelFiles": b"/pdk/models.scs\n", "amap/__dspf_information__.": bytes(range(38)), "trailing space ": b"x\n"}
    export = tmp_path / "export"
    for name, data in files.items():
        (export / name).parent.mkdir(parents=True, exist_ok=True)
        (export / name).write_bytes(data)
    saved = Deck(templates={("tb", None): TEMPLATE}, bundles={"tb": export}).save(RunStore(tmp_path).root / "decks")

    executor = FakeSpectreExecutor(tmp_path / ".icopt" / "sims", lambda p, tb, c: {"NF": 8.0})
    child, ctx = run_chain(tmp_path, executor, deck=Deck.load(saved))
    assert child.status == "ok"
    for name, data in files.items():
        assert (saved / "tb" / "bundle" / name).read_bytes() == data and (ctx.workdir / "netlist" / name).read_bytes() == data
    assert {export, saved / "tb" / "bundle", ctx.workdir / "netlist"} <= set(taken)


def test_the_license_queue_wait_is_passed_only_when_the_spec_states_it(tmp_path):
    """R-17: ``+lqtimeout`` is simulator.license_queue_timeout_s. Unset, the flag stays off and Spectre waits in its license
    queue as it does by itself: no lab's wait is assumed. It is how the problem is run, not the problem (fingerprint)."""
    unset = spectre_pipeline(make_spec(), Deck())[1].argv()
    assert "+lqtimeout" not in unset and "900" not in unset
    stated_spec = make_spec(simulator={**minimal_spec()["simulator"], "license_queue_timeout_s": 600})
    stated = spectre_pipeline(stated_spec, Deck())[1].argv()
    at = stated.index("+lqtimeout")
    assert stated[at:at + 2] == ["+lqtimeout", "600"] and stated[:at] + stated[at + 2:] == unset
    assert Spectre(preset="ax", threads=1, timeout_s=1, license_queue_timeout_s=0).argv()[6:8] == ["+lqtimeout", "0"]
    assert stated_spec.fingerprint() == make_spec().fingerprint()

    executor = FakeSpectreExecutor(tmp_path / ".icopt" / "sims", lambda p, tb, c: {"NF": 8.0})
    child, _ = run_chain(tmp_path, executor, spec=stated_spec)
    assert child.status == "ok" and " +mt=2 +lqtimeout 600 -maxw 5 " in executor.commands[0]


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


def test_spectre_retries_once_on_a_transient_socket_failure(tmp_path):
    from ic_opt.executor import CommandResult
    from ic_opt.stages.spectre_chain import TRANSIENT_SOCKET_FAILURE

    class FlakySocket(FakeSpectreExecutor):
        calls = 0

        def run(self, command, *, cwd=None, timeout_s=None, cshrc=None):
            if command.startswith("spectre") and self.calls == 0:
                self.calls += 1
                return CommandResult(1, "", f"spectre: {TRANSIENT_SOCKET_FAILURE}", ["spectre"], 0.01)
            return super().run(command, cwd=cwd, timeout_s=timeout_s, cshrc=cshrc)

    executor = FlakySocket(tmp_path / ".icopt" / "sims", lambda p, tb, c: {"NF": 8.0})
    child, ctx = run_chain(tmp_path, executor)
    assert child.status == "ok" and [c["label"] for c in ctx.trace] == ["spectre#1", "spectre#2", "ocean#1"]
