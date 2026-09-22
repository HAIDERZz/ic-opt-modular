"""Count how many times the full project config set is reloaded from disk while evaluating ONE
candidate through the real evaluation chain (evaluate_real_candidate -> default adapter ->
aggregation -> check_real_run -> check_metric_results -> record_real_result -> retention).
The Spectre/OCEAN sub-adapter is faked in-process (it writes the same child artifacts the real
tool writes), everything else runs for real.
"""
from __future__ import annotations

import collections
import inspect
import sys
import tempfile
from pathlib import Path

import yaml

import hermes_workflow.execution_adapters.spectre_ocean as adapter_module
import hermes_workflow.validate as validate_module
from hermes_workflow.native_turbo import evaluate_real_candidate
from tests.test_multi_testbench_aggregation import _write_corner_child_handoff
from tests.test_native_turbo import _create_ready_multi_corner_single_testbench_project

RUN_ID = "real_007"


def main(corner_ids: list[str]) -> None:
    tmp = Path(tempfile.mkdtemp(prefix="icopt_reload_count_"))
    project_dir = _create_ready_multi_corner_single_testbench_project(tmp, corner_ids=corner_ids)
    variables = yaml.safe_load((project_dir / "config" / "variables.yaml").read_text())["variables"]
    params = {v["name"]: str(v["lower"]) for v in variables}
    metrics = yaml.safe_load((project_dir / "config" / "metrics.yaml").read_text())["metrics"]
    metric_name = metrics[0]["name"]

    def fake_run_spectre_ocean_adapter(project, *, run_id, testbench_id=None, corner_id=None):
        _write_corner_child_handoff(
            project, testbench_id=testbench_id, corner_id=corner_id,
            metric_name=metric_name, run_id=run_id, value=7.5,
        )
        return type("R", (), {"status": "succeeded", "issues": []})()

    adapter_module.run_spectre_ocean_adapter = fake_run_spectre_ocean_adapter

    counter: collections.Counter = collections.Counter()
    original = validate_module._load_config_models

    def counting(project_dir, issues):
        for frame in inspect.stack()[1:]:
            name = Path(frame.filename).name
            if "hermes_workflow" in frame.filename and name != "validate.py":
                counter[(name, frame.function)] += 1
                break
        return original(project_dir, issues)

    validate_module._load_config_models = counting

    observation = evaluate_real_candidate(
        project_dir, candidate_id=RUN_ID, parameters=params, run_id=RUN_ID, cadence_cshrc=None,
    )
    total = sum(counter.values())
    print(f"corners={corner_ids}  observation.status={observation.status}  issues={observation.issues}")
    print(f"full config reloads (_load_config_models calls) for ONE candidate: {total}")
    for (fname, func), n in sorted(counter.items(), key=lambda kv: -kv[1]):
        print(f"   {n:3d}  {fname}:{func}")


if __name__ == "__main__":
    main(sys.argv[1].split(",") if len(sys.argv) > 1 else ["tt", "ss", "ff"])
