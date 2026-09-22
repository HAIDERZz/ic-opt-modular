"""V4: replay recorded em-opt EM optimizer runs through the em_circuit pipeline.

IC_OPT_EM_RECORDED_RUNS lists em-opt project directories (colon-separated) that hold
``em_opt_requirement.md``, ``circuit/imported/<tb>/input.scs``, ``runs/em_optimizer/<candidate>/``
and ``runs/real/<run>/``. The fake host hands back the recorded sNp for each point and the
recorded OCEAN scalars for each testbench, so pcell (real), the EMX command, the nport
binding, the render and the aggregation are exercised for real and must agree with what
em-opt recorded. Needs the private process profile the projects were drawn on.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from decimal import Decimal
from pathlib import Path

import pytest

from ic_opt import space
from ic_opt.blocks.evaluate import evaluate
from ic_opt.blocks.netlist import import_netlists
from ic_opt.em import nport
from ic_opt.migrate import spec_from_requirement
from ic_opt.space import Point
from ic_opt.store import RunStore
from tests.ic_opt.fakes import FakeSpectreExecutor

pytest.importorskip("klayout.db")

RECORDED = [Path(p) for p in os.environ.get("IC_OPT_EM_RECORDED_RUNS", "").split(":") if p]
STATUS_MAP = {"feasible": {"ok"}, "constraint_failed": {"constraint_failed"}, "metric_check_failed": {"metric_failed", "failed:extract", "failed:ocean"}}


def rows_of(project: Path) -> list[dict]:
    for name in ("optimizer_evaluations.jsonl", "native_turbo_optimizer_evaluations.jsonl"):
        path = project / "reports" / name
        if path.exists():
            return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    raise FileNotFoundError(project / "reports")


def recorded_snp_path(project: Path, run_id: str, device: str, single: bool) -> Path:
    """The sNp em-opt used for this run: follow the run's evaluation manifest to its candidate directory."""
    manifest = json.loads((project / "runs" / "real" / run_id / "em_circuit_evaluation_manifest.json").read_text())
    candidate = project / "runs" / "em_optimizer" / manifest["candidate_id"]
    folder = candidate / "em" if single else candidate / "devices" / device / "em"
    files = sorted(folder.glob("*.s[0-9]p"))
    assert len(files) == 1, f"{folder}: {files}"
    return files[0]


@pytest.mark.skipif(not RECORDED, reason="set IC_OPT_EM_RECORDED_RUNS")
@pytest.mark.parametrize("project", RECORDED, ids=lambda p: p.name)
def test_nport_kernel_reproduces_recorded_patches(project):
    """Our patch on the imported netlist must give em-opt's recorded patched sha256, instance by instance."""
    checked = 0
    for manifest in sorted(project.glob("runs/em_optimizer/*/circuit/*/nport_patch_manifest.json")):
        m = json.loads(manifest.read_text())
        text = (project / "circuit" / "imported" / m["testbench_id"] / "input.scs").read_text(encoding="utf-8")
        assert hashlib.sha256(text.encode()).hexdigest() == m["original_input_sha256"]
        for patch in m["patches"]:
            out = nport.patch(text, instance=patch["instance"], replacement=patch["replacement_path"], n_ports=patch["expected_ports"])
            assert hashlib.sha256(out.text.encode()).hexdigest() == patch["patched_input_sha256"], f"{manifest}: {patch['instance']}"
            assert out.signal_nodes == patch["terminal_order"]
            text = out.text
            checked += 1
    assert checked > 0


@pytest.mark.skipif(not RECORDED or not os.environ.get("IC_OPT_PROFILE_DIRS"), reason="set IC_OPT_EM_RECORDED_RUNS and IC_OPT_PROFILE_DIRS")
@pytest.mark.parametrize("project", RECORDED, ids=lambda p: p.name)
def test_em_circuit_pipeline_matches_recorded_run(tmp_path, project):
    spec, _ = spec_from_requirement(project / "em_opt_requirement.md")
    rows = rows_of(project)

    def key_of(params: dict) -> str:          # a device's own variables, canonical text: "102" and 102.0 are the same value
        return json.dumps({k: space.format_value(Decimal(str(v)).normalize(), "") for k, v in sorted(params.items())})

    by_key = {key_of(row["parameters"]): row for row in rows}

    def row_for(obs_dir: Path) -> dict:
        """The recorded row for the point evaluated under ``obs_dir``: read back from every device's generator config."""
        geometry = json.loads((obs_dir / "em" / "geometry.json").read_text())
        params = {variable: geometry[d.id]["config"][field] for d in spec.devices for field, variable in spec.device_fields(d).items()}
        return by_key[key_of(params)]

    def recorded_snp(argv, n_ports, z0, cwd):
        device = Path(cwd).name
        row = row_for(Path(cwd).parent.parent)
        return recorded_snp_path(project, row["run_id"], device, single=len(spec.devices) == 1).read_text()

    def recorded_metrics(cwd, tb):
        row = row_for(Path(cwd).parent.parent)
        path = project / "runs" / "real" / row["run_id"] / "testbenches" / tb / "metrics" / "metric_result_manifest.json"
        if not path.exists():
            return {}
        return {m["name"]: (m["value"] if m["status"] == "succeeded" else None) for m in json.loads(path.read_text())["metrics"]}

    store = RunStore(tmp_path)
    executor = ReplayHost(store.root / "sims", recorded_metrics, recorded_snp)
    for tb in spec.testbenches:                       # the recorded projects' Maestro exports were copied into circuit/imported/<tb>/
        tb.maestro_point_root = str(tmp_path / "exports" / tb.id)
        shutil.copytree(project / "circuit" / "imported" / tb.id, tmp_path / "exports" / tb.id / "netlist")
    deck = import_netlists(spec, executor, store)
    spec.budget.max_simulations = 10_000
    points = [Point(row["parameters"], "replay") for row in rows]
    observations = evaluate(spec, points, executor, store, deck=deck, parallel_jobs=4)

    mismatches = []
    for row, obs in zip(rows, observations, strict=True):
        if obs.status not in STATUS_MAP.get(row["status"], {row["status"]}):
            mismatches.append(f"{row['run_id']}: status {row['status']} -> {obs.status} ({obs.issues[:2]})")
            continue
        if row["status"] in ("feasible", "constraint_failed"):
            if abs(row["fom"] - obs.fom) > 1e-9 * max(1.0, abs(row["fom"])):
                mismatches.append(f"{row['run_id']}: fom {row['fom']} -> {obs.fom}")
            if abs(row["constraint_penalty"] - obs.constraint_penalty) > 1e-9 * max(1.0, row["constraint_penalty"]):
                mismatches.append(f"{row['run_id']}: penalty {row['constraint_penalty']} -> {obs.constraint_penalty}")
            if any(abs(row["metrics"][k] - obs.metrics[k]) > 1e-12 for k in row["metrics"]):
                mismatches.append(f"{row['run_id']}: metrics differ")
        if row["status"] == "feasible" and abs(row["objective"] - obs.objective) > 1e-9 * max(1.0, abs(row["objective"])):
            mismatches.append(f"{row['run_id']}: objective {row['objective']} -> {obs.objective}")
    assert not mismatches, "\n".join(mismatches[:20])
    assert all("emx:" + d.id in o.cache for o in observations for d in spec.devices)
    print(f"\nreplayed {len(rows)} points from {project.name}: statuses / fom / penalties / metrics agree")


class ReplayHost(FakeSpectreExecutor):
    """Fake host whose emx and ocean hand back the recording for the point being evaluated (found through the working directory).

    Points run in parallel, so the lookups take the working directory as an argument instead of being swapped into shared attributes."""

    def __init__(self, scratch_root, metrics_for_cwd, snp_for_cwd):
        super().__init__(scratch_root)
        self.metrics_for_cwd = metrics_for_cwd
        self.snp_for_cwd = snp_for_cwd
        self.snp_fn = lambda argv, n, z0, cwd: self.snp_for_cwd(argv, n, z0, cwd)
        self.metric_fn = lambda params, tb, corner, cwd: self.metrics_for_cwd(cwd, tb)
