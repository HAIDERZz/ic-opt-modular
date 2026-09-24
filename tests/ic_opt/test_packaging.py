"""T15.5: the package declares what it imports (audit row 9).

Every module-level third-party import under ``src/ic_opt`` resolves to a distribution in
``[project].dependencies`` -- or, under ``ic_opt/em``, the ``em`` extra (klayout). Imports inside functions
are optional features (OpenBox, torch, SHAP) and are not checked here; ``scripts/check_clean_install.sh``
installs the package into fresh environments and runs it.
"""

from __future__ import annotations

import ast
import re
import sys
import tomllib
from pathlib import Path

import pytest

from ic_opt.observation import Observations
from ic_opt.suggesters.openbox import OpenBoxSuggester
from tests.ic_opt.fakes import make_spec

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "src" / "ic_opt"
DISTRIBUTION = {"sklearn": "scikit-learn", "yaml": "pyyaml"}      # import name -> distribution name, where they differ


def _normalized(name: str) -> str:
    return name.lower().replace("_", "-")


def _names(requirements: list[str]) -> set[str]:
    return {_normalized(re.split(r"[\s<>=!~;\[(]", r, maxsplit=1)[0]) for r in requirements}


def _module_level_imports(path: Path) -> set[str]:
    tops = set()
    for node in ast.parse(path.read_text(), filename=str(path)).body:
        if isinstance(node, ast.Import):
            tops |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            tops.add(node.module.split(".")[0])
    return tops - set(sys.stdlib_module_names) - {"__future__", "ic_opt"}


def test_every_module_level_import_is_a_declared_dependency():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    core = _names(project["dependencies"])
    em = core | _names(project["optional-dependencies"]["em"])
    undeclared = sorted(
        f"{path.relative_to(ROOT)}: {top}"
        for path in PACKAGE.rglob("*.py")
        for top in _module_level_imports(path)
        if _normalized(DISTRIBUTION.get(top, top)) not in (em if path.relative_to(PACKAGE).parts[0] == "em" else core)
    )
    assert not undeclared, undeclared


def test_an_openbox_strategy_without_openbox_says_how_to_install_it(monkeypatch):
    monkeypatch.setitem(sys.modules, "openbox", None)                # `import openbox` now raises ImportError
    with pytest.raises(ImportError, match=r"-e vendor/open-box"):
        OpenBoxSuggester().propose(make_spec(), Observations(), 1, seed=0)
