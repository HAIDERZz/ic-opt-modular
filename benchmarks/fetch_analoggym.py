#!/usr/bin/env python3
"""Fetches AnalogGym (BSD-3-Clause, https://github.com/CODA-Team/AnalogGym) to the pinned commit this benchmark
was built against, and unpacks its SKY130 process models. Standard library only -- everything else here treats
AnalogGym as read-only, never vendors it into the repo.

    python benchmarks/fetch_analoggym.py [--dest benchmarks/.data]

The destination (``$ICOPT_BENCH_DATA`` when set, else ``benchmarks/.data`` next to this file, else ``--dest``) is
a *parent* directory: the clone lands at ``<dest>/AnalogGym``. Re-running when that clone already sits at the
pinned commit with the models unpacked does nothing but say so.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import zipfile
from pathlib import Path

REPO_URL = "https://github.com/CODA-Team/AnalogGym.git"
PINNED_COMMIT = "0a9d1390ade361e2b4a2d33181e22367edbb8afc"


def default_dest() -> Path:
    env = os.environ.get("ICOPT_BENCH_DATA")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent / ".data"


def current_commit(repo: Path) -> str | None:
    if not (repo / ".git").is_dir():
        return None
    result = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=False)
    return result.stdout.strip() if result.returncode == 0 else None


def models_unpacked(repo: Path) -> bool:
    return (repo / "PDK" / "sky130_pdk" / "libs.tech" / "ngspice" / "corners" / "tt.spice").exists()


def fetch(dest: Path) -> None:
    repo = dest / "AnalogGym"
    if current_commit(repo) == PINNED_COMMIT and models_unpacked(repo):
        print(f"{repo} is already at {PINNED_COMMIT} with the SKY130 models unpacked -- nothing to do.")
        return

    if not repo.exists():
        dest.mkdir(parents=True, exist_ok=True)
        print(f"cloning {REPO_URL} into {repo} ...")
        subprocess.run(["git", "clone", REPO_URL, str(repo)], check=True)
    else:
        print(f"{repo} exists; fetching {PINNED_COMMIT} ...")
        subprocess.run(["git", "-C", str(repo), "fetch", "origin", PINNED_COMMIT], check=True)

    print(f"checking out {PINNED_COMMIT} ...")
    subprocess.run(["git", "-C", str(repo), "checkout", PINNED_COMMIT], check=True)

    zip_path = repo / "PDK" / "sky130_pdk.zip"
    if not models_unpacked(repo):
        if not zip_path.exists():
            raise FileNotFoundError(f"{zip_path} is missing -- cannot unpack the SKY130 models")
        print(f"unpacking {zip_path} ...")
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(zip_path.parent)
    print(f"done: {repo} is at {current_commit(repo)} with the SKY130 models unpacked.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dest", type=Path, default=None, help="parent directory for the AnalogGym clone")
    args = parser.parse_args(argv)
    dest = args.dest if args.dest is not None else default_dest()
    fetch(dest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
