"""Geometry-library tests moved from em-opt.

The private process profiles (n28_1p10m, n65_1p9m) are never in this repository:
point ``IC_OPT_PROFILE_DIRS`` at a directory holding ``<profile>/rule.yaml`` to run
the tests that need them; without it they skip and only the packaged ``demo_6m``
profile is exercised.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from ic_opt.em.pcell.process_rules import PROFILE_DIRS_ENV_VAR

PACKAGE_DIR = Path(__file__).resolve().parents[3] / "src" / "ic_opt" / "em" / "pcell"


def profile_path(profile_id: str) -> Path | None:
    for directory in os.environ.get(PROFILE_DIRS_ENV_VAR, "").split(os.pathsep):
        if directory and (Path(directory).expanduser() / profile_id / "rule.yaml").is_file():
            return Path(directory).expanduser() / profile_id / "rule.yaml"
    packaged = PACKAGE_DIR / "profiles" / profile_id / "rule.yaml"
    return packaged if packaged.is_file() else None


def requires_profile(profile_id: str):
    return pytest.mark.skipif(profile_path(profile_id) is None, reason=f"process profile {profile_id} not available (set {PROFILE_DIRS_ENV_VAR})")
