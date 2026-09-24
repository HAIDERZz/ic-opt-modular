"""migrate-store on a store the 0.2.0 release wrote.

``fixtures/store_v020`` was made by that release's own code (tag v0.2.0: ``sim.evaluate`` on three
points with its tests' fake Spectre host); its spec.yaml is upgraded as README says
(``threads_per_run: 10``, 0.2.0's default, written in). 0.2.0 stamped by formulas of its own: the
spec in its schema, which had no EM fields yet, and the pipeline by its stage names alone -- neither
of today's formulas reproduces them.
"""

import json
import shutil
from pathlib import Path

import pytest
import yaml

from ic_opt import migrate_store
from ic_opt.blocks.evaluate import evaluate
from ic_opt.deck import Deck
from ic_opt.eval.engine import BudgetExceeded
from ic_opt.eval.stage import pipeline_fingerprint
from ic_opt.space import Point
from ic_opt.spec import Budget, Spec, load_spec
from ic_opt.stages import spectre_pipeline
from ic_opt.store import RunStore
from tests.ic_opt.fakes import FAKE_HOST, FakeSpectreExecutor

FIXTURE = Path(__file__).parent / "fixtures" / "store_v020"
V020_SPEC = "4acfade5952668ab"  # the fixture rows' stamps, as the 0.2.0 code wrote them
V020_SPECTRE = "224cfefcae4bf414"  # 0.2.0's hash of "render|spectre|ocean|extract"
TEMPLATE = "simulator lang=spectre\nparameters temperature=27 F={{F}} W={{W}}\ntran tran stop=10n\n"
POINTS = [Point({"F": f, "W": w}) for f, w in [("20", "0.6u"), ("24", "0.8u"), ("28", "1u")]]

# Spec.fingerprint at tag v0.2.0 gave these two specs the values in PINNED. RICH sets nearly every
# field of every model of the 0.2.0 schema; BARE is waveform only (no metrics, constraints or
# objective) and left threads_per_run out: 0.2.0's default is written in here.
RICH = """
project: rich
description: every model of the 0.2.0 schema, most fields set
testbenches:
  - {id: cg, maestro_point_root: /a, virtuoso_library: l, cell: c, test_name: t,
     design_view: schematic_lvs, maestro_view: maestro_nf, corner: C0}
  - {id: iip3, maestro_point_root: /b, virtuoso_library: l, cell: c, test_name: t}
corners:
  - {id: tt, model_section: tt}
  - {id: ss, model_section: ss, model_file: /pdk/models.scs, variables: {vdd: "0.9"}, description: slow}
corner_policy: {objective: nominal, constraints: nominal}
variables:
  - {name: F, kind: integer, lower: 20, upper: 30, step: 2}
  - {name: W, kind: continuous_step, lower: 0.6u, upper: 1.2u, step: 0.2u}
metrics:
  - {name: NF, unit: dB, expression: nf(), testbench: cg, result: noise, required_signals: [/out]}
  - {name: IIP3, unit: dBm, expression: iip3(), testbench: iip3}
constraints:
  - {metric: NF, op: lt, value: 9}
  - {metric: IIP3, op: gt, value: "0"}
objective: {direction: minimize, expression: NF - IIP3}
simulator: {preset: mx, threads_per_run: 4, parallel_jobs: 3, timeout_s: 900, license_check: false,
            keep_failed_runs: false, keep_successful_runs: true}
budget: {max_simulations: 50}
"""
BARE = """
project: bare
testbenches: [{id: tb, maestro_point_root: /x, virtuoso_library: lib, cell: c, test_name: t}]
variables: [{name: F, kind: integer, lower: "20", upper: "30", step: "2"}]
simulator: {parallel_jobs: 1, threads_per_run: 10, timeout_s: 60}
budget: {max_simulations: 5}
"""
PINNED = {"rich": (RICH, "e120700d72c4219b"), "bare": (BARE, "4123cad87b525de8")}

LATER = """
em: {process_file: /site/demo.proc, frequencies: [1.0e+9], threads: 1, memory_gb: 1, timeout_s: 60}
devices: [{id: ind, generator: clean_port_ind_sym, profile: demo_6m, ports: [P1, N1]}]
"""


def project(tmp_path: Path) -> Path:
    """A copy of the fixture project (migrating writes to it)."""
    return Path(shutil.copytree(FIXTURE, tmp_path / "lna_v020"))


def deck(spec: Spec) -> Deck:
    return Deck(templates={(tb, c): TEMPLATE for tb in spec.testbench_ids for c in spec.corner_ids})


def nf(params, testbench, corner):
    """The 0.2.0 run's metric."""
    return {"NF": 8.0 + int(params["F"]) / 100}


def fake_host(project: Path) -> FakeSpectreExecutor:
    return FakeSpectreExecutor(project / ".icopt" / "sims", nf)


def spectre_runs(host: FakeSpectreExecutor) -> int:
    return sum(c.startswith("spectre") for c in host.commands)


def spectre_now(spec: Spec) -> str:
    return pipeline_fingerprint(spectre_pipeline(spec, Deck()))


def test_the_0_2_0_formulas_reproduce_what_that_release_stamped():
    spec = load_spec(FIXTURE / "spec.yaml")
    lines = (FIXTURE / ".icopt" / "observations.jsonl").read_text().splitlines()
    stamps = {(r["spec_fingerprint"], r["pipeline_fingerprint"]) for r in map(json.loads, lines)}
    assert len(lines) == 3 and stamps == {(V020_SPEC, V020_SPECTRE)}
    assert migrate_store.v020_fingerprint(spec) == V020_SPEC
    assert migrate_store.v020_pipeline_fingerprint(spectre_pipeline(spec, Deck())) == V020_SPECTRE
    assert V020_SPEC not in {spec.fingerprint(), spec._legacy_fingerprint()}  # why it is needed
    assert spectre_now(spec) != V020_SPECTRE
    raw = yaml.safe_load((FIXTURE / "spec.yaml").read_text())  # the resources were in the stamp:
    raw["simulator"]["threads_per_run"] = 8  # a value other than 0.2.0's matches nothing
    assert migrate_store.v020_fingerprint(Spec.model_validate(raw)) != V020_SPEC


@pytest.mark.parametrize("name", PINNED)
def test_the_0_2_0_spec_fingerprint_is_pinned_against_that_code(name):
    """If this moves, the 0.2.0 schema listed in migrate_store lost or gained a field."""
    text, stamped = PINNED[name]
    assert migrate_store.v020_fingerprint(Spec.model_validate(yaml.safe_load(text))) == stamped


def test_a_spec_0_2_0_could_not_state_has_no_0_2_0_fingerprint():
    """Leaving out a field 0.2.0 did not have would give the spec the fingerprint of one without it."""
    base = yaml.safe_load((FIXTURE / "spec.yaml").read_text())
    later = yaml.safe_load(LATER)
    frequency = {**base["metrics"][0], "frequency_hz": 1e9}  # an OCEAN metric may carry one today
    queue = {**base["simulator"], "license_queue_timeout_s": 600}  # R-17
    for fields in ({"em": later["em"]}, later, {"metrics": [frequency]}, {"simulator": queue}):
        spec = Spec.model_validate({**base, **fields})
        assert migrate_store.v020_fingerprint(spec) is None, fields


def test_before_migrating_the_0_2_0_rows_load_and_count_but_are_not_reused(tmp_path):
    """A child's unit, spelled ``testbench`` by 0.2.0, reads as it is; the stamps match neither the
    problem nor the pipeline until migrate-store restamps them."""
    proj = project(tmp_path)
    spec = load_spec(proj / "spec.yaml")
    assert [o.children["tb/nominal"].unit for o in RunStore(proj).observations()] == ["tb"] * 3
    host = fake_host(proj)
    tight = spec.model_copy(update={"budget": Budget(max_simulations=3)})
    with pytest.raises(BudgetExceeded, match="3 simulations used"):
        evaluate(tight, POINTS[:1], host, RunStore(proj), deck=deck(spec), limits=FAKE_HOST)
    evaluate(spec, POINTS[:1], host, RunStore(proj), deck=deck(spec), limits=FAKE_HOST)
    assert spectre_runs(host) == 1


def test_migrate_store_restamps_the_rows_0_2_0_wrote_and_the_engine_reuses_them(tmp_path):
    proj = project(tmp_path)
    spec = load_spec(proj / "spec.yaml")
    new_spec, new_pipe = spec.fingerprint(), spectre_now(spec)
    path = proj / ".icopt" / "observations.jsonl"
    before = path.read_bytes()
    after = before.replace(V020_SPEC.encode(), new_spec.encode())
    after = after.replace(V020_SPECTRE.encode(), new_pipe.encode())

    dry = migrate_store.migrate(proj, dry_run=True)
    counts = (dry.rows, dry.restamped, dry.spec_rows, dry.v020_rows, dry.pipeline_rows)
    assert counts == (3, 3, 0, 3, 3) and not dry.other_specs
    assert (dry.v020_old, dry.v020_pipeline) == (V020_SPEC, (V020_SPECTRE, new_pipe))
    assert path.read_bytes() == before and not list(path.parent.glob("observations.jsonl.bak-*"))
    lines = str(dry).splitlines()
    assert f"  0.2.0 fingerprint   {V020_SPEC} -> {new_spec}: 3 rows restamped" in lines
    assert f"  pipeline spectre    {V020_SPECTRE} -> {new_pipe} (0.2.0 rows of this spec)" in lines
    assert "  pipeline rows       3 restamped" in lines
    assert lines[-1] == "  dry run: nothing written"

    done = migrate_store.migrate(proj)
    assert done.restamped == 3 and done.backup.read_bytes() == before
    assert "done: 3 rows restamped" in str(done)
    assert path.read_bytes() == after  # the two stamps changed in place, nothing else
    assert (dict(done.before), dict(done.after)) == ({V020_SPECTRE: 3}, {new_pipe: 3})

    again = migrate_store.migrate(proj)
    assert not again.changed and "nothing to change" in str(again) and path.read_bytes() == after
    assert len(list(path.parent.glob("observations.jsonl.bak-*"))) == 1

    host = fake_host(proj)
    reused = evaluate(spec, POINTS, host, RunStore(proj), deck=deck(spec), limits=FAKE_HOST)
    assert spectre_runs(host) == 0
    assert [(o.obs_id, o.metrics["NF"]) for o in reused] == [
        ("obs_0001", 8.2),
        ("obs_0002", 8.24),
        ("obs_0003", 8.28),
    ]
    step = json.loads((proj / ".icopt" / "steps.jsonl").read_text().splitlines()[-1])
    assert (step["reused"], step["new"], step["simulations"]) == (3, 0, 0)


def test_a_0_2_spec_without_the_license_queue_wait_stays_without_it(tmp_path):
    """N-12: only ``ic-opt migrate`` (a 0.1 requirement -> spec.yaml) writes 0.1's +lqtimeout 900 out. A spec.yaml 0.2 wrote
    without the field keeps it unset: migrate-store does not rewrite the file, and its runs pass no +lqtimeout."""
    proj = project(tmp_path)
    before = (proj / "spec.yaml").read_bytes()
    assert b"license_queue_timeout_s" not in before
    migrate_store.migrate(proj)
    assert (proj / "spec.yaml").read_bytes() == before
    spec = load_spec(proj / "spec.yaml")
    assert spec.simulator.license_queue_timeout_s is None
    host = fake_host(proj)
    evaluate(spec, [Point({"F": "22", "W": "0.6u"}, "user")], host, RunStore(proj), deck=deck(spec), limits=FAKE_HOST)
    spectre = [c for c in host.commands if c.startswith("spectre")]
    assert len(spectre) == 1 and "+lqtimeout" not in spectre[0]


def test_rows_it_cannot_place_stay_as_they_are_and_are_reported(tmp_path):
    """A row of another spec keeps both stamps, its 0.2.0 pipeline stamp too. A 0.2.0 row of this
    spec from a stage list a recipe assembled itself (other names, another hash) gets the problem
    fingerprint and keeps its pipeline stamp: it is not taken for the Spectre pipeline."""
    proj = project(tmp_path)
    spec = load_spec(proj / "spec.yaml")
    path = proj / ".icopt" / "observations.jsonl"
    row = json.loads(path.read_text().splitlines()[0])
    other = {**row, "obs_id": "obs_0004", "params": {"F": "30", "W": "1.2u"}}
    other["spec_fingerprint"] = "0" * 16  # another spec
    custom = {**row, "obs_id": "obs_0005", "params": {"F": "22", "W": "1.2u"}}
    custom["pipeline_fingerprint"] = "f" * 16  # a 0.2.0 recipe's own stage list
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"{json.dumps(other)}\n{json.dumps(custom)}\n")

    done = migrate_store.migrate(proj)
    assert (done.rows, done.restamped, done.v020_rows, done.pipeline_rows) == (5, 4, 4, 3)
    assert dict(done.other_specs) == {"0" * 16: 1}
    assert f"  left as they are    1 rows of spec {'0' * 16} (" in str(done)
    assert dict(done.after) == {spectre_now(spec): 3, V020_SPECTRE: 1, "f" * 16: 1}
    lines = path.read_text().splitlines()
    assert lines[3] == json.dumps(other)
    assert json.loads(lines[4]) == {**custom, "spec_fingerprint": spec.fingerprint()}

    host = fake_host(proj)
    points = [POINTS[0], Point(other["params"], "user"), Point(custom["params"], "user")]
    evaluate(spec, points, host, RunStore(proj), deck=deck(spec), limits=FAKE_HOST)
    assert spectre_runs(host) == 2  # the first point is reused, the other two simulated again
