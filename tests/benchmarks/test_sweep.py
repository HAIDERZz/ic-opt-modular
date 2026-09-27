import json
import os
from pathlib import Path

import pytest
from icopt_bench import registry, sweep

_WORKTREE = Path(__file__).resolve().parents[2]
_PYTHONPATH = os.pathsep.join([str(_WORKTREE / "src"), str(_WORKTREE / "benchmarks"), str(_WORKTREE / "tests" / "benchmarks")])


def _set_pythonpath(monkeypatch: pytest.MonkeyPatch) -> None:
    """The subprocesses ``sweep.py`` spawns need PYTHONPATH themselves -- pytest's own ``pythonpath`` ini setting only
    reaches this process's ``sys.path``, not a child's environment (module docstring of ``fixtures/slow_problem.py``:
    the same reason that fixture is reached through an environment variable, not a monkeypatched registry)."""
    monkeypatch.setenv("PYTHONPATH", _PYTHONPATH)


def test_parse_seeds():
    assert sweep.parse_seeds("0-3") == [0, 1, 2, 3]
    assert sweep.parse_seeds("0,2,5") == [0, 2, 5]
    assert sweep.parse_seeds("0-1,5") == [0, 1, 5]


def test_sweep_runs_all_jobs_writes_progress_and_done(tmp_path, monkeypatch):
    _set_pythonpath(monkeypatch)
    out_dir = tmp_path / "sweep"
    code = sweep.main(["--problems", "syn_small_tight,syn_multimodal_small", "--methods", "random,sobol",
                        "--seeds", "0-1", "--budget", "20", "--batch", "10", "--jobs", "2", "--out", str(out_dir)])
    assert code == 0
    files = sorted(p.name for p in out_dir.glob("*__*__*.json"))
    assert files == [
        "syn_multimodal_small__random__0.json", "syn_multimodal_small__random__1.json",
        "syn_multimodal_small__sobol__0.json", "syn_multimodal_small__sobol__1.json",
        "syn_small_tight__random__0.json", "syn_small_tight__random__1.json",
        "syn_small_tight__sobol__0.json", "syn_small_tight__sobol__1.json",
    ]
    progress = json.loads((out_dir / "progress.json").read_text(encoding="utf-8"))
    assert progress["total"] == 8
    assert progress["done"] == 8
    assert progress["failed"] == 0
    assert progress["running"] == 0
    assert (out_dir / "DONE").exists()
    for f in files:
        data = json.loads((out_dir / f).read_text(encoding="utf-8"))
        assert data["error"] is None
        assert len(data["points"]) == 20


def test_sweep_resume_skips_finished_jobs(tmp_path, monkeypatch):
    _set_pythonpath(monkeypatch)
    out_dir = tmp_path / "sweep"
    args = ["--problems", "syn_small_tight", "--methods", "random", "--seeds", "0", "--budget", "10", "--batch", "10",
            "--jobs", "1", "--out", str(out_dir)]
    assert sweep.main(args) == 0
    result_path = out_dir / "syn_small_tight__random__0.json"
    first_mtime = result_path.stat().st_mtime_ns

    assert sweep.main(args) == 0                                      # a second run should not touch the finished job
    assert result_path.stat().st_mtime_ns == first_mtime
    progress = json.loads((out_dir / "progress.json").read_text(encoding="utf-8"))
    assert progress["done"] == 1


def test_sweep_kills_a_job_past_its_timeout(tmp_path, monkeypatch):
    _set_pythonpath(monkeypatch)
    monkeypatch.setenv("ICOPT_BENCH_EXTRA_PROBLEMS", "fixtures.slow_problem")
    monkeypatch.setenv("ICOPT_BENCH_TEST_SLEEP_S", "10")
    out_dir = tmp_path / "sweep"
    code = sweep.main(["--problems", "test_slow", "--methods", "random", "--seeds", "0", "--budget", "1", "--batch", "1",
                        "--jobs", "1", "--out", str(out_dir), "--timeout", "1"])
    assert code == 1
    data = json.loads((out_dir / "test_slow__random__0.json").read_text(encoding="utf-8"))
    assert data["error"] == "timeout"
    progress = json.loads((out_dir / "progress.json").read_text(encoding="utf-8"))
    assert progress["failed"] == 1
    assert (out_dir / "DONE").exists()


def test_heldout_guard_refuses_without_the_flag(tmp_path, monkeypatch):
    split_path = tmp_path / "split.json"
    split_path.write_text(json.dumps({"seed": 1, "development": [], "heldout": ["syn_ackley10_c2"]}), encoding="utf-8")
    monkeypatch.setattr(registry, "_SPLIT_PATH", split_path)
    with pytest.raises(SystemExit):
        sweep.resolve_problems(["syn_ackley10_c2"], allow_heldout=False, reason=None, tool="sweep", methods=["random"])


def test_heldout_guard_logs_the_look_when_allowed(tmp_path, monkeypatch):
    split_path = tmp_path / "split.json"
    split_path.write_text(json.dumps({"seed": 1, "development": [], "heldout": ["syn_ackley10_c2"]}), encoding="utf-8")
    monkeypatch.setattr(registry, "_SPLIT_PATH", split_path)
    log_path = tmp_path / "HELDOUT_LOG.md"
    monkeypatch.setattr(sweep, "_HELDOUT_LOG", log_path)
    sweep.resolve_problems(["syn_ackley10_c2"], allow_heldout=True, reason="unit test", tool="sweep", methods=["random"])
    text = log_path.read_text(encoding="utf-8")
    assert "syn_ackley10_c2" in text
    assert "unit test" in text
