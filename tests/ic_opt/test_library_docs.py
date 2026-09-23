"""T13.8: docs/em/library.md says what the code does -- its recipe runs, its manifest loads, its query arguments parse."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

from ic_opt import blocks
from ic_opt.library import manifest, suggest
from ic_opt.recipe import BUILTIN_RECIPES, load_recipe
from tests.ic_opt.test_library_signoff import make_run

DOC = Path(__file__).resolve().parents[2] / "docs" / "em" / "library.md"


def blocks_of(lang: str) -> list[str]:
    return re.findall(rf"```{lang}\n(.*?)```", DOC.read_text(encoding="utf-8"), flags=re.DOTALL)


def test_the_manifest_example_is_a_valid_library_yaml():
    (example,) = blocks_of("yaml")
    lib = manifest.Library.model_validate(yaml.safe_load(example))
    stratum = lib.strata["ind_sym_top"]
    assert stratum.columns() == ["Lp_lf", "Lp_res", "Qp_peak", "SRF_p", "Lp@10", "Lp@28", "Qp@10", "Qp@28"]


def test_the_sweep_recipe_runs(tmp_path):
    pytest.importorskip("klayout.db")
    (recipe,) = blocks_of("python")
    (tmp_path / "sweep.py").write_text(recipe, encoding="utf-8")
    run, ex = make_run(tmp_path, "part")
    load_recipe(str(tmp_path / "sweep.py"))(run, n=4, seed=1)
    obs = run.store.observations()
    assert len(obs) == 4 and ex.emx_runs == 4 and {o.step for o in obs} == {"sweep"} and {o.status for o in obs} == {"ok"}


def test_every_command_names_a_real_block_or_recipe_with_parsable_arguments():
    text = "\n".join(blocks_of("bash"))
    for name in re.findall(r"ic-opt call (\S+)", text):
        assert name in blocks.REGISTRY, name
    for name in re.findall(r"ic-opt run (\S+)", text):
        assert name.endswith(".py") or name in BUILTIN_RECIPES, name
    (example,) = blocks_of("yaml")
    dims = manifest.Library.model_validate(yaml.safe_load(example)).strata["ind_sym_top"].dims
    params = json.loads(re.search(r"'params=(\{.*?\})'", text).group(1))
    assert sorted(params) == sorted(dims)
    targets = suggest.parse_targets(json.loads(re.search(r"'targets=(\{.*?\}\})'", text).group(1)))
    assert {t.quantity for t in targets} == {"Lp_lf", "SRF_p"}
    assert suggest.parse_objective(re.search(r"objective=(\S+)", text).group(1)) == ("max", "Qp_peak")
