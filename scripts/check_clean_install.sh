#!/usr/bin/env bash
# Clean-install check: install IC-Opt from this checkout into throwaway virtual environments and run it.
#
#   bash scripts/check_clean_install.sh                   # prints PASS or FAIL, exits 0 / 1
#   OFFLINE=1 bash scripts/check_clean_install.sh         # uv only: resolve from uv's cache, no package index
#   KEEP=1 bash scripts/check_clean_install.sh            # keep the environments for a look afterwards
#   PYTHON=3.11 (the default) picks the interpreter; OpenBox's pins have wheels only there.
#
# A  `-e ".[em]"` alone. OpenBox and gpytorch both pull scikit-learn in, so only an environment without
#    them shows whether IC-Opt declares everything it imports: `ic-opt --help`, every ic_opt module, and
#    the packaging / library / blocks tests must run.
# B  the README install, `-e ".[em,turbo]" -e vendor/open-box` in one resolver call (OpenBox pins
#    numpy < 2): the same checks plus the optimizer tests (sobol, turbo and openbox strategies).
#
# pytest is installed next to the package in both environments; nothing else is added. Uses uv when it is
# on PATH (torch from its CPU build unless UV_TORCH_BACKEND says otherwise), else `python -m venv` + pip.
# Linux / macOS (bin/ layout).

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-3.11}"
export UV_TORCH_BACKEND="${UV_TORCH_BACKEND:-cpu}"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/ic-opt-clean-install.XXXXXX")"
FAILED=""
TESTS="tests/ic_opt/test_packaging.py tests/ic_opt/test_library_query.py tests/ic_opt/test_blocks.py"
IMPORT_ALL='import importlib, pkgutil, ic_opt
names = [m.name for m in pkgutil.walk_packages(ic_opt.__path__, "ic_opt.")]
for name in names:
    importlib.import_module(name)
print(f"{len(names)} ic_opt modules imported")'

finish() {
    if [[ "${KEEP:-0}" == 1 ]]; then echo "environments kept under $WORK"; else rm -rf "$WORK"; fi
}
trap finish EXIT

check() {                   # check NAME COMMAND...: run it, print ok / FAIL, remember a failure, carry on
    local name="$1"
    shift
    echo "---- $name"
    if "$@"; then
        echo "ok   $name"
    else
        echo "FAIL $name"
        FAILED="$FAILED; $name"
        return 1
    fi
}

new_env() {                 # new_env DIR: an empty virtual environment
    if command -v uv >/dev/null 2>&1; then
        uv venv --python "$PYTHON" "$1"
    else
        "$(command -v "python$PYTHON" || command -v python3)" -m venv "$1"
    fi
}

install() {                 # install DIR REQUIREMENT...: everything in one resolver call
    local env="$1"
    shift
    if command -v uv >/dev/null 2>&1; then
        local offline=""
        [[ "${OFFLINE:-0}" == 1 ]] && offline="--offline"
        uv pip install --quiet --python "$env/bin/python" $offline "$@"
    else
        "$env/bin/python" -m pip install --quiet "$@"
    fi
}

run_in() {                  # run_in DIR PROGRAM ARG...: DIR/bin/PROGRAM from the checkout root, no PYTHONPATH
    local env="$1"
    shift
    (cd "$ROOT" && env -u PYTHONPATH PYTHONDONTWRITEBYTECODE=1 OMP_NUM_THREADS="${OMP_NUM_THREADS:-4}" \
        "$env/bin/$1" "${@:2}")
}

quiet() { "$@" >/dev/null; }

versions() {                # versions DIR: what the resolver picked ("-" = not installed)
    run_in "$1" python -c '
import importlib.metadata as m, platform
names = ["numpy", "scipy", "scikit-learn", "threadpoolctl", "klayout", "torch", "gpytorch", "openbox"]
def version(name):
    try:
        return m.version(name)
    except m.PackageNotFoundError:
        return "-"
print("python " + platform.python_version() + ", " + ", ".join(f"{n} {version(n)}" for n in names))'
}

A="$WORK/declared"
B="$WORK/documented"

echo "== A: -e \".[em]\" alone (no OpenBox, no torch): does IC-Opt declare what it imports?"
if check "A: venv" new_env "$A" && check "A: install" install "$A" -e "$ROOT[em]" "pytest>=8.0"; then
    versions "$A"
    check "A: ic-opt --help" quiet run_in "$A" ic-opt --help
    check "A: imports" run_in "$A" python -c "$IMPORT_ALL"
    # shellcheck disable=SC2086
    check "A: pytest" run_in "$A" python -m pytest -q -rs -p no:cacheprovider $TESTS
fi

echo
echo "== B: -e \".[em,turbo]\" -e vendor/open-box in one resolver call (the README install)"
if check "B: venv" new_env "$B" &&
    check "B: install" install "$B" -e "$ROOT[em,turbo]" -e "$ROOT/vendor/open-box" "pytest>=8.0"; then
    versions "$B"
    check "B: ic-opt --help" quiet run_in "$B" ic-opt --help
    check "B: imports" run_in "$B" python -c "$IMPORT_ALL
import openbox, torch, gpytorch"
    # shellcheck disable=SC2086
    check "B: pytest" run_in "$B" python -m pytest -q -rs -p no:cacheprovider $TESTS tests/ic_opt/test_optimize.py
fi

echo
if [[ -n "$FAILED" ]]; then
    echo "FAIL:${FAILED#;}"
    exit 1
fi
echo "PASS"
