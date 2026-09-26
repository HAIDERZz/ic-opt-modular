"""T15.5: the package declares what it imports (audit row 9).

Every module-level third-party import under ``src/ic_opt`` resolves to a distribution in
``[project].dependencies`` -- or, under ``ic_opt/em``, the ``em`` extra (klayout). Imports inside functions
are optional features (OpenBox, TuRBO, torch, SHAP) and are not checked here; ``scripts/check_clean_install.sh``
installs the package into fresh environments and runs it. OpenBox and TuRBO are vendored and installed from the
checkout (T15.5b): a missing one says how to install it, and nothing reaches them by editing ``sys.path``.
"""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from ic_opt import suggesters
from ic_opt.observation import Observations
from ic_opt.suggesters.openbox import OpenBoxSuggester
from tests.ic_opt.fakes import make_spec

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "src" / "ic_opt"
DISTRIBUTION = {"sklearn": "scikit-learn", "yaml": "pyyaml"}      # import name -> distribution name, where they differ
TURBO_INSTALL = 'uv pip install -e ".[turbo]" -e vendor/TuRBO'     # what a missing TuRBO asks for, from the checkout

# Run in a fresh interpreter (this one imported ic_opt.suggesters at collection): import the suggesters, use both
# TuRBO-backed ones, report sys.path before / after and where `turbo` came from (None: not importable here).
FRESH_TURBO_USE = """
import json, sys
before = list(sys.path)
from ic_opt import suggesters
from ic_opt.observation import Observations
from tests.ic_opt.fakes import make_spec
for strategy in ("latin_hypercube", "turbo"):
    try:
        suggesters.make(strategy).propose(make_spec(), Observations(), 2, seed=0)
    except ImportError:
        pass
print(json.dumps({"before": before, "after": sys.path, "turbo": getattr(sys.modules.get("turbo"), "__file__", None)}))
"""


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


@pytest.mark.parametrize("strategy", ["turbo", "latin_hypercube"])
def test_a_turbo_strategy_without_turbo_says_how_to_install_it(monkeypatch, strategy):
    monkeypatch.setitem(sys.modules, "turbo", None)                  # `import turbo` now raises ImportError,
    monkeypatch.setitem(sys.modules, "turbo.utils", None)            # and so does `turbo.utils` if a test loaded it
    with pytest.raises(ImportError, match=re.escape(TURBO_INSTALL)) as raised:
        suggesters.make(strategy).propose(make_spec(), Observations(), 2, seed=0)
    assert "non-commercial" in str(raised.value)


def test_the_suggesters_import_turbo_without_touching_sys_path():
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(PACKAGE.parent), str(ROOT)])}
    done = subprocess.run([sys.executable, "-c", FRESH_TURBO_USE], cwd=ROOT, env=env, capture_output=True, text=True,
                          check=False)
    assert done.returncode == 0, done.stderr
    fresh = json.loads(done.stdout.splitlines()[-1])
    assert fresh["after"] == fresh["before"]
    if fresh["turbo"] is not None:                                   # installed: its own editable finder or site-packages
        where = Path(fresh["turbo"]).resolve()
        assert where.parts[-4:-2] == ("vendor", "TuRBO") or "site-packages" in where.parts, where


def test_windows_torch_stays_below_the_msvc_runtime_scikit_learn_loads():
    """On Windows scikit-learn < 1.4 (OpenBox's pin) loads its own msvcp140.dll 14.32 at import; torch 2.9+ is built with
    MSVC 14.42 and fails to initialise on it (WinError 1114, 2026-09-27 Windows acceptance). The turbo extra keeps
    Windows below 2.9 and leaves the other platforms alone; lifting the scikit-learn pin is the moment to move the bound."""
    from packaging.requirements import Requirement

    turbo = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["optional-dependencies"]["turbo"]
    torch = [Requirement(r) for r in turbo if r.startswith("torch")]

    def allowed(platform, version):
        return any(r.specifier.contains(version) for r in torch if r.marker is None or r.marker.evaluate({"sys_platform": platform}))

    assert allowed("win32", "2.8.0") and not allowed("win32", "2.9.0")
    assert allowed("linux", "2.12.0") and allowed("darwin", "2.12.0")
    openbox = (ROOT / "vendor" / "open-box" / "requirements" / "main.txt").read_text()
    assert "scikit-learn>=0.24.0,<1.4.0" in openbox, "the scikit-learn pin moved: revisit the Windows torch bound"
