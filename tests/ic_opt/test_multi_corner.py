"""T17.9, step 2 of the evaluation schedule (``docs/refactor/T17_9_MULTI_CORNER_SPEC.md``, section 6): the stop's default
follows the run's corners, and ``metric_gp`` takes a run at several corners, each metric at its worst."""

from __future__ import annotations

import pytest

from ic_opt.blocks.evaluate import plan_shape
from ic_opt.executor import LocalExecutor
from tests.ic_opt.fakes import FAKE_HOST, minimal_spec
from tests.ic_opt.test_schedule import Prepared, run_batch, two_by_two


def switched(value: bool | None):
    """``two_by_two`` with ``simulator.stop_at_first_failure`` as given; None: a spec that does not name it."""
    if value is None:
        return two_by_two()
    return two_by_two(simulator={**minimal_spec()["simulator"], "stop_at_first_failure": value})


# -- 1. the default ------------------------------------------------------------------------------------------------------------


# (the spec's switch, the run's corners, the recipe's override, the points stopped): at both corners F=20, 24 and 26 stop
# (test_schedule's ``prepared``), at tt only F=20 (F=24 fails at its last child there, F=26 fails at ss)
CASES = [(None, "all", None, 3), (None, ["tt"], None, 0), (True, ["tt"], None, 1), (False, "all", None, 0),
         (None, "all", False, 0), (None, ["tt"], True, 1), (False, "all", True, 3), (True, ["tt"], False, 0)]


@pytest.mark.parametrize(("value", "corners", "override", "stopped"), CASES)
def test_the_stop_is_on_at_several_corners_and_off_at_one_unless_the_spec_or_the_recipe_says(tmp_path, value, corners,
                                                                                            override, stopped):
    spec = switched(value)
    kwargs = {} if override is None else {"stop_at_first_failure": override}
    _, stage, obs = run_batch(tmp_path, spec, corners=corners, **kwargs)
    assert sum(1 for o in obs if o.not_run) == stopped
    children = 4 if corners == "all" else 2
    assert len(stage.ran) == 5 * children - sum(len(o.not_run) for o in obs)
    line = plan_shape(spec, [Prepared()], corners, LocalExecutor(tmp_path), None, FAKE_HOST, override)
    assert line.startswith(f"({children} testbench sims) = up to {children} simulations per point (a point stops at the "
                           "first simulation that fails it) on local" if stopped else
                           f"({children} testbench sims) = {children} simulations per point on local")


# -- 2. the dump ---------------------------------------------------------------------------------------------------------------


def test_the_switch_is_written_only_when_set_and_is_never_the_problem():
    unnamed = two_by_two()
    for value in (None, True, False):
        spec = switched(value)
        simulator = spec.model_dump(mode="json")["simulator"]
        assert ("stop_at_first_failure" in simulator) is (value is not None), value
        assert simulator.get("stop_at_first_failure") is value and spec.fingerprint() == unnamed.fingerprint()
    assert switched(None).model_dump(mode="json") == unnamed.model_dump(mode="json")
    assert unnamed.simulator.stop_at_first_failure is None
