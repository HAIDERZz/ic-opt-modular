"""N-99: the deck is part of the process identity. A changed netlist, support file or export list never reuses an old
observation; the old one stays in the store as the problem's history (``docs/refactor/N99_DECK_IDENTITY_SPEC.md``)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from ic_opt import digest as dg
from ic_opt import migrate_store
from ic_opt.blocks.doctor import doctor
from ic_opt.blocks.evaluate import evaluate
from ic_opt.blocks.netlist import import_netlists
from ic_opt.deck import Deck, tree_digests
from ic_opt.eval.stage import pipeline_fingerprint
from ic_opt.recipe import PLAN_MODE
from ic_opt.sim.ocean import WaveformExport
from ic_opt.space import Point
from ic_opt.stages.spectre_chain import spectre_pipeline
from ic_opt.store import RunStore
from tests.ic_opt.fakes import FAKE_HOST, FakeSpectreExecutor, make_spec

POINT = Point({"F": "22", "W": "0.8u"}, "user")


def export(root: Path, *, offset: str = "0", model: str = "1") -> Path:
    """A Maestro export of testbench ``tb``: the netlist with a literal ``OFFSET`` (not a spec variable: it stays in the
    template) and a support file ``model.scs`` the netlist includes."""
    netlist = root / "tb" / "netlist"
    netlist.mkdir(parents=True, exist_ok=True)
    (netlist / "input.scs").write_text(
        f'simulator lang=spectre\ninclude "model.scs"\nparameters temperature=27 F=20 W=0.6u OFFSET={offset}\ntran tran stop=10n\n')
    (netlist / "model.scs").write_text(f"simulator lang=spectre\nparameters model_marker={model}\n")
    (netlist / ".modelFiles").write_text("model.scs\n")
    return root / "tb"


def spec_for(maestro: Path, **overrides):
    return make_spec(testbenches=[{"id": "tb", "maestro_point_root": str(maestro / "tb"), "virtuoso_library": "l", "cell": "c",
                                   "test_name": "t"}], **overrides)


def nf(params, tb, corner, cwd):
    """What the fake simulation of the point gives: the netlist's OFFSET and the support file's marker both count."""
    marker = (Path(cwd) / "netlist" / "model.scs").read_text().split("model_marker=")[1].strip()
    return {"NF": 8.0 + int(params["F"]) / 100 + float(params["OFFSET"]) + float(marker) / 10}


def last_step(store: RunStore) -> dict:
    return json.loads((store.root / "steps.jsonl").read_text().splitlines()[-1])


@pytest.fixture
def project(tmp_path):
    maestro = tmp_path / "maestro"
    export(maestro)
    store = RunStore(tmp_path / "proj")
    host = FakeSpectreExecutor(store.root / "sims", nf)
    return maestro, store, host


def run_once(maestro, store, host, **kw):
    spec = spec_for(maestro)
    deck = import_netlists(spec, host, store)
    return deck, evaluate(spec, [POINT], host, store, deck=deck, limits=FAKE_HOST, **kw)[0]


def spectre_runs(host) -> int:
    return sum(c.startswith("spectre") for c in host.commands)


def test_the_same_input_twice_is_reused(project):
    maestro, store, host = project
    _, first = run_once(maestro, store, host)
    _, again = run_once(maestro, store, host)
    assert again.obs_id == first.obs_id and spectre_runs(host) == 1
    assert (last_step(store)["new"], last_step(store)["reused"]) == (0, 1)


def test_a_changed_netlist_is_evaluated_again_and_the_old_observation_kept(project):
    maestro, store, host = project
    old_deck, first = run_once(maestro, store, host)
    export(maestro, offset="0.5")                                   # the netlist fixed in Virtuoso and exported again
    new_deck, second = run_once(maestro, store, host)
    assert new_deck.fingerprint() != old_deck.fingerprint()
    assert second.obs_id != first.obs_id and spectre_runs(host) == 2
    assert (first.metrics["NF"], second.metrics["NF"]) == (pytest.approx(8.32), pytest.approx(8.82))
    step = last_step(store)
    assert (step["new"], step["reused"], step["simulations"]) == (1, 0, 1)
    assert [o.obs_id for o in store.observations()] == [first.obs_id, second.obs_id]      # the old one stays: history
    assert first.spec_fingerprint == second.spec_fingerprint                               # the same problem
    assert first.pipeline_fingerprint != second.pipeline_fingerprint


def test_a_changed_support_file_is_another_deck_and_the_old_snapshot_stays(project):
    maestro, store, host = project
    old_deck, first = run_once(maestro, store, host)
    old_dir = store.deck_dir(old_deck.fingerprint())
    snapshot = {p.relative_to(old_dir): p.read_bytes() for p in old_dir.rglob("*") if p.is_file()}
    export(maestro, model="2")                                      # the template is the same: only model.scs changed
    new_deck, second = run_once(maestro, store, host)
    assert new_deck.templates == old_deck.templates and new_deck.fingerprint() != old_deck.fingerprint()
    assert store.deck_dir(new_deck.fingerprint()).is_dir() and store.deck_dir(new_deck.fingerprint()) != old_dir
    assert {p.relative_to(old_dir): p.read_bytes() for p in old_dir.rglob("*") if p.is_file()} == snapshot   # byte for byte
    assert (old_dir / "tb" / "bundle" / "model.scs").read_text().endswith("model_marker=1\n")
    assert second.obs_id != first.obs_id and second.metrics["NF"] == pytest.approx(first.metrics["NF"] + 0.1)


def test_a_saved_deck_loads_to_its_fingerprint(tmp_path):
    bundle = tmp_path / "export"
    (bundle / "amap").mkdir(parents=True)
    (bundle / "input.scs").write_text("simulator lang=spectre\n")
    (bundle / "amap" / "__dspf_information__.").write_text("dspf\n")
    (tmp_path / "target.txt").write_text("linked content")
    try:
        (bundle / "link").symlink_to(tmp_path / "target.txt")
    except OSError as exc:                                          # Windows without the symlink privilege
        pytest.skip(f"this account cannot create symlinks: {exc}")
    deck = Deck(templates={("tb", None): "t", ("tb", "ss"): "s"}, source={"tb": "host:/x"}, bundles={"tb": bundle})
    saved = deck.save(tmp_path / "decks")
    assert saved.name == deck.fingerprint() == Deck.load(saved).fingerprint()
    assert tree_digests(saved / "tb" / "bundle") == tree_digests(bundle)          # the symlink by its content, as copied
    assert set(tree_digests(bundle)) == {"amap/__dspf_information__.", "input.scs", "link"}
    (tmp_path / "target.txt").write_text("other content")             # what the link points to is the content
    assert deck.fingerprint() != saved.name
    assert Deck(templates=dict(deck.templates)).fingerprint() == Deck(templates=dict(deck.templates)).fingerprint()
    snapshot = Deck.load(saved).snapshot()
    assert snapshot.fingerprint() == saved.name and not snapshot.bundles
    with pytest.raises(ValueError, match="cannot be saved"):
        snapshot.save(tmp_path / "decks")


def test_modes_and_times_are_not_part_of_the_deck(tmp_path):
    bundle = tmp_path / "export"
    bundle.mkdir()
    (bundle / "input.scs").write_text("x\n")
    deck = Deck(templates={("tb", None): "t"}, bundles={"tb": bundle})
    before = deck.fingerprint()
    (bundle / "input.scs").chmod(0o600)
    os.utime(bundle / "input.scs", (1, 1))
    assert deck.fingerprint() == before


def test_a_changed_waveform_request_is_evaluated_again(project):
    maestro, store, host = project
    vout = [WaveformExport(name="vout", expression='v("out")')]
    _, plain = run_once(maestro, store, host)
    _, exported = run_once(maestro, store, host, waveforms=vout)
    assert exported.obs_id != plain.obs_id and spectre_runs(host) == 2      # the old one lacks the export
    _, again = run_once(maestro, store, host, waveforms=vout)
    assert again.obs_id == exported.obs_id and spectre_runs(host) == 2
    other = [WaveformExport(name="vout", expression='v("out2")')]
    _, changed = run_once(maestro, store, host, waveforms=other)
    assert changed.obs_id not in (plain.obs_id, exported.obs_id)


def test_the_operating_point_setting_is_part_of_the_pipeline(tmp_path):
    deck = Deck(templates={("tb", None): "t"})
    on = spec_for(tmp_path, simulator={"parallel_jobs": 1, "threads_per_run": 1, "timeout_s": 60, "operating_points": True})
    off = spec_for(tmp_path, simulator={"parallel_jobs": 1, "threads_per_run": 1, "timeout_s": 60, "operating_points": False})
    assert on.fingerprint() == off.fingerprint()                                # one problem
    assert pipeline_fingerprint(spectre_pipeline(on, deck)) != pipeline_fingerprint(spectre_pipeline(off, deck))


def test_the_formulas_before_n99_see_the_render_and_extract_stages_without_identity(tmp_path):
    """migrate-store's frozen formulas reproduce the stamps of the versions before: the deck and the exports were not in them."""
    spec = spec_for(tmp_path)
    a, b = Deck(templates={("tb", None): "a"}), Deck(templates={("tb", None): "b"})
    vout = [WaveformExport(name="vout", expression='v("out")')]
    assert pipeline_fingerprint(spectre_pipeline(spec, a)) != pipeline_fingerprint(spectre_pipeline(spec, b))
    assert (migrate_store.legacy_pipeline_fingerprint(spectre_pipeline(spec, a))
            == migrate_store.legacy_pipeline_fingerprint(spectre_pipeline(spec, b, waveforms=vout))
            == migrate_store.legacy_pipeline_fingerprint([type("S", (), {"name": n})() for n in
                                                          ("render", "spectre", "ocean", "extract")]))


def test_the_plan_names_the_deck_and_the_history_it_will_not_reuse(project, capsys):
    maestro, store, host = project
    old_deck, first = run_once(maestro, store, host)
    export(maestro, offset="0.5")
    spec = spec_for(maestro)
    token = PLAN_MODE.set(True)
    try:
        planned = import_netlists(spec, host, store)                 # the preview's deck: a snapshot, nothing stored
        evaluate(spec, [POINT], host, store, deck=planned, limits=FAKE_HOST)
    finally:
        PLAN_MODE.reset(token)
    out = capsys.readouterr().out
    assert planned.fingerprint() != old_deck.fingerprint() and not store.deck_dir(planned.fingerprint()).exists()
    assert f"[plan] sim.evaluate step='evaluate': deck {planned.fingerprint()} (1 testbenches, 3 support files)" in out
    assert ("[plan] sim.evaluate step='evaluate': 1 of the store's 1 observations were evaluated with another deck or "
            "pipeline: they are history, not reused") in out
    deck = import_netlists(spec, host, store)                        # the run imports the same deck the plan named
    assert deck.fingerprint() == planned.fingerprint()
    capsys.readouterr()
    token = PLAN_MODE.set(True)
    try:
        evaluate(spec, [POINT], host, store, deck=import_netlists(spec, host, store), limits=FAKE_HOST)
    finally:
        PLAN_MODE.reset(token)
    assert "history, not reused" in capsys.readouterr().out          # still: the old deck's observation
    evaluate(spec, [POINT], host, store, deck=deck, limits=FAKE_HOST)
    assert len(store.observations()) == 2 and first.obs_id == store.observations()[0].obs_id


def test_doctor_names_the_deck_last_imported(project):
    maestro, store, host = project
    spec = spec_for(maestro)
    line = next(c for c in doctor(spec, host, store=store, limits=FAKE_HOST).checks if c.name == "deck")
    assert line.ok and line.detail == "none imported yet: the run imports the exports"
    deck = import_netlists(spec, host, store)
    line = next(c for c in doctor(spec, host, store=store, limits=FAKE_HOST).checks if c.name == "deck")
    assert line.ok and line.detail.startswith(f"{deck.fingerprint()} (1 testbenches, 3 support files), as last imported")


def test_the_digest_says_when_the_store_mixes_pipelines(project):
    maestro, store, host = project
    run_once(maestro, store, host)
    spec = spec_for(maestro)
    assert dg.digest(spec, store.observations())["progress"]["pipelines"] is None
    assert "pipelines; the current one" not in dg.markdown(dg.digest(spec, store.observations()))
    export(maestro, offset="0.5")
    run_once(maestro, store, host)
    evaluate(spec, [Point({"F": "24", "W": "0.8u"}, "user")], host, store, deck=import_netlists(spec, host, store),
             limits=FAKE_HOST)
    d = dg.digest(spec, store.observations())
    assert d["progress"]["pipelines"] == {"pipelines": 2, "current": 2}
    assert "- observations from 2 pipelines; the current one holds 2" in dg.markdown(d).splitlines()


def test_a_deck_without_bundles_fingerprints_as_before():
    """Pinned on main 925cf72 (before N-99): a deck of templates alone keeps its fingerprint and its decks/<fp>/."""
    assert Deck(templates={("tb", None): "simulator lang=spectre\n", ("tb", "ss"): "x"}).fingerprint() == "acc85b226bd9babe"


def test_a_preview_deck_cannot_run(project):
    maestro, store, host = project
    spec = spec_for(maestro)
    token = PLAN_MODE.set(True)
    try:
        planned = import_netlists(spec, host, store)
    finally:
        PLAN_MODE.reset(token)
    obs = evaluate(spec, [POINT], host, store, deck=planned, limits=FAKE_HOST)[0]
    assert obs.status == "failed:render" and "snapshot cannot run" in obs.children["tb/nominal"].issues[0]
    assert spectre_runs(host) == 0
