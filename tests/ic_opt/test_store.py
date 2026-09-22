import pytest

from ic_opt.observation import ChildResult, Observation
from ic_opt.store import RunStore


def make_obs(obs_id: str, f: str, objective: float | None, feasible: bool, step: str = "opt") -> Observation:
    return Observation(
        obs_id=obs_id, params={"F": f, "W": "0.6u"}, origin="suggest:turbo",
        children={"tb/nominal": ChildResult(testbench="tb", corner=None, status="ok", metrics={"NF": 8.0})},
        metrics={"NF": 8.0}, fom=objective, objective=objective, feasible=feasible,
        status="ok" if feasible else "constraint_failed", spec_fingerprint="s", pipeline_fingerprint="p",
        step=step, started_at="t0", finished_at="t1",
    )


def test_store_appends_reloads_and_queries(tmp_path):
    store = RunStore(tmp_path)
    assert store.next_obs_id() == "obs_0001"
    store.append(make_obs("obs_0001", "20", 3.0, True))
    store.append(make_obs("obs_0002", "22", 1.0, True, step="signoff"))
    store.append(make_obs("obs_0003", "24", None, False))

    fresh = RunStore(tmp_path).observations()
    assert [o.obs_id for o in fresh] == ["obs_0001", "obs_0002", "obs_0003"]
    assert [o.obs_id for o in fresh.best(2)] == ["obs_0002", "obs_0001"]
    assert fresh.by_step("signoff")[0].params["F"] == "22"
    assert fresh.keys() == {o.key for o in fresh}
    assert RunStore(tmp_path).next_obs_id() == "obs_0004"

    sim = store.sim_dir("obs_0001", "tb", None)
    assert sim.is_dir() and store.relative(sim) == ".icopt/sims/obs_0001/tb/nominal"


def test_lock_is_exclusive(tmp_path):
    store = RunStore(tmp_path)
    with store.lock(), pytest.raises(RuntimeError, match="locked"), RunStore(tmp_path).lock():
        pass
    with RunStore(tmp_path).lock():
        pass
