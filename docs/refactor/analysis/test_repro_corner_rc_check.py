"""Differential reproduction for the suspected indentation defect at
hermes_workflow/native_turbo.py:1765 (`_run_multi_testbench_default_adapter`, cshrc branch).

Scenario: one testbench, corners [tt, ss, ff]; the MIDDLE corner (ss) fails, the LAST corner (ff)
succeeds. The in-process branch (cadence_cshrc=None) and the csh branch (cadence_cshrc=Path)
should behave identically: raise RuntimeError naming "ss".
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import hermes_workflow.execution_adapters.spectre_ocean as adapter_module
import hermes_workflow.multi_testbench_aggregation as aggregation_module
import hermes_workflow.native_turbo as native_turbo_module
from tests.test_native_turbo import _create_ready_multi_corner_single_testbench_project

CORNERS = ["tt", "ss", "ff"]


def _patch_aggregate(monkeypatch: pytest.MonkeyPatch, calls: list[str]) -> None:
    def fake_aggregate(project: Path, *, run_id: str):
        calls.append(run_id)
        return object()

    monkeypatch.setattr(aggregation_module, "aggregate_multi_testbench_run", fake_aggregate)


def _run(project_dir: Path, cadence_cshrc: Path | None) -> None:
    native_turbo_module._run_default_adapter(
        project_dir, run_id="real_007", cadence_cshrc=cadence_cshrc
    )


def test_inprocess_branch_reports_middle_corner_failure(tmp_path, monkeypatch) -> None:
    project_dir = _create_ready_multi_corner_single_testbench_project(tmp_path, corner_ids=CORNERS)
    seen: list[str | None] = []

    def fake_adapter(project, *, run_id, testbench_id=None, corner_id=None):
        seen.append(corner_id)
        failed = corner_id == "ss"
        return type("R", (), {"status": "failed" if failed else "succeeded",
                              "issues": ["spectre exploded"] if failed else []})()

    monkeypatch.setattr(adapter_module, "run_spectre_ocean_adapter", fake_adapter)
    agg: list[str] = []
    _patch_aggregate(monkeypatch, agg)

    with pytest.raises(RuntimeError, match="ss"):
        _run(project_dir, cadence_cshrc=None)
    assert seen == CORNERS and agg == ["real_007"]


@pytest.mark.parametrize("failing_corner", ["ss", "ff"])
def test_csh_branch_reports_corner_failure(tmp_path, monkeypatch, failing_corner) -> None:
    project_dir = _create_ready_multi_corner_single_testbench_project(tmp_path, corner_ids=CORNERS)
    commands: list[str] = []

    def fake_subprocess_run(argv, **kwargs):
        command = argv[-1]
        commands.append(command)
        failed = f"--corner-id {failing_corner}" in command
        return subprocess.CompletedProcess(
            argv, returncode=1 if failed else 0,
            stdout=f"issue: spectre exploded in {failing_corner}\n" if failed else "succeeded\n",
            stderr="",
        )

    monkeypatch.setattr(native_turbo_module.subprocess, "run", fake_subprocess_run)
    agg: list[str] = []
    _patch_aggregate(monkeypatch, agg)

    with pytest.raises(RuntimeError, match=failing_corner):
        _run(project_dir, cadence_cshrc=Path("/tmp/fake.csh"))
    assert len(commands) == len(CORNERS), "all corners must still be executed"
    assert agg == ["real_007"]
