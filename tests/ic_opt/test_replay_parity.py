"""Replay a recorded real run through the new engine and compare with the legacy outcome.

The legacy run supplies, per candidate, the OCEAN scalars each testbench × corner
produced; the fake executor hands exactly those back, so everything between the
scalars and the observation (render, extract, corner aggregation, objective,
feasibility) is exercised for real and must agree with what 0.1.10 recorded.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from ic_opt.blocks.evaluate import evaluate
from ic_opt.deck import Deck
from ic_opt.migrate import spec_from_config_dir, spec_from_requirement
from ic_opt.space import Point, point_key
from ic_opt.store import RunStore
from tests.ic_opt.fakes import FakeSpectreExecutor, host_for

# Recorded 0.1.10 project directories (config/ or opt_requirement.md + reports/optimizer_evaluations.jsonl),
# colon-separated in IC_OPT_RECORDED_RUNS; the parity tests skip when none are available.
RECORDED = [
    Path(p) for p in os.environ.get("IC_OPT_RECORDED_RUNS", "").split(":") if p
]
STATUS_MAP = {"feasible": {"ok"}, "constraint_failed": {"constraint_failed"},
              "metric_check_failed": {"metric_failed", "failed:extract", "failed:ocean"}}


def load_children(run_dir: Path, spec) -> dict:
    """(tb, corner) -> {metric: value|None} from the recorded child metric manifests."""
    children = {}
    for tb in spec.testbench_ids:
        for corner in spec.corner_ids:
            path = run_dir / "testbenches" / tb / "corners" / (corner or "nominal") / "metrics" / "metric_result_manifest.json"
            if path.exists():
                manifest = json.loads(path.read_text())
                children[(tb, corner or "nominal")] = {
                    m["name"]: (m["value"] if m["status"] == "succeeded" else None) for m in manifest["metrics"]
                }
    return children


@pytest.mark.parametrize("recorded", RECORDED, ids=lambda p: p.name)
def test_engine_matches_recorded_run(tmp_path, recorded):
    if not recorded.exists():
        pytest.skip(f"recorded run not available: {recorded}")
    if (recorded / "config" / "spectre.yaml").exists():
        spec = spec_from_config_dir(recorded / "config")
    else:   # a Remote project directory never carries config/*.yaml; only the requirement
        spec, _ = spec_from_requirement(recorded / "opt_requirement.md")
    rows = [json.loads(line) for line in (recorded / "reports" / "optimizer_evaluations.jsonl").read_text().splitlines() if line.strip()]
    assert rows, "recorded run has no evaluations"

    by_key = {point_key(row["parameters"]): load_children(recorded / "runs" / "real" / row["run_id"], spec) for row in rows}

    def metric_fn(params, tb, corner):
        return by_key[point_key(params)].get((tb, corner or "nominal"), {})

    store = RunStore(tmp_path)
    executor = FakeSpectreExecutor(store.root / "sims", metric_fn)   # a missing child manifest -> {} -> failed:extract
    template = "simulator lang=spectre\ninclude \"/p/top.scs\" section=tt\nparameters " + " ".join(
        f"{v.name}={{{{{v.name}}}}}" for v in spec.variables
    ) + "\ntran tran stop=1n\n"
    deck = Deck(templates={(tb, c): template for tb in spec.testbench_ids for c in spec.corner_ids})
    points = [Point(row["parameters"], "replay") for row in rows]
    observations = evaluate(spec, points, executor, store, deck=deck, parallel_jobs=8, limits=host_for(spec, 8))

    mismatches = []
    for row, obs in zip(rows, observations, strict=True):
        expected = STATUS_MAP[row["status"]]
        if obs.status not in expected:
            mismatches.append(f"{row['run_id']}: status {row['status']} -> {obs.status} ({obs.issues[:2]})")
            continue
        if row["status"] in ("feasible", "constraint_failed"):
            if row["fom"] is None or obs.fom is None or abs(row["fom"] - obs.fom) > 1e-9 * max(1.0, abs(row["fom"])):
                mismatches.append(f"{row['run_id']}: fom {row['fom']} -> {obs.fom}")
            if abs(row["constraint_penalty"] - obs.constraint_penalty) > 1e-9 * max(1.0, row["constraint_penalty"]):
                mismatches.append(f"{row['run_id']}: penalty {row['constraint_penalty']} -> {obs.constraint_penalty}")
            if row.get("metrics") and any(abs(row["metrics"][k] - obs.metrics[k]) > 1e-12 for k in row["metrics"]):
                mismatches.append(f"{row['run_id']}: metrics differ")
        if row["status"] == "feasible" and abs(row["objective"] - obs.objective) > 1e-9 * max(1.0, abs(row["objective"])):
            mismatches.append(f"{row['run_id']}: objective {row['objective']} -> {obs.objective}")
    assert not mismatches, "\n".join(mismatches[:20])
    print(f"\nreplayed {len(rows)} points from {recorded.name}: all statuses/fom/penalties agree")
