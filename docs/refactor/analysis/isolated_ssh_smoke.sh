#!/bin/sh
# Honest remote-mode acceptance on a self-SSH topology (ADR-0001).
#
# The controller runs inside a bubblewrap sandbox where every remote-owned path is
# hidden: the Maestro exports, the Cadence installation, the cshrc file and the
# remote scratch. The only way to reach them is the SshExecutor. Any controller
# code path that reads a remote path off the local disk (the 0.1.8 / 0.1.9
# "fake remote" defect) fails inside the sandbox instead of accidentally working.
# Of ~/.ic-opt the controller keeps only its own site.yaml, bound read-only: since
# 0.3.0 nothing starts without the entry of the host a command runs on (hosts.local
# for the local doctor, hosts.SSH_PROFILE for the remote steps).
#
#   sh docs/refactor/analysis/isolated_ssh_smoke.sh PROJECT_DIR SSH_PROFILE CSHRC_ON_HOST
#
# PROJECT_DIR must live outside the hidden trees (it is bind-mounted read-write).
# HIDE_TREES overrides the remote-owned trees to hide (default: $HOME/simulation /opt/eda $HOME/.ic-opt).
set -eu
PROJECT=$(cd "$1" && pwd)
PROFILE=$2
CSHRC=$3
REPO=$(cd "$(dirname "$0")/../../.." && pwd)
SITE=$HOME/.ic-opt/site.yaml
if [ ! -f "$SITE" ]; then
    echo "$SITE not found: the controller's site.yaml, with entries for local and $PROFILE, is required" >&2
    exit 2
fi

EMPTY=$(mktemp)      # an empty regular file bound over the cshrc (a /dev/null bind is unreadable under bwrap's nodev)
# /etc/ssh/ssh_config.d is hidden because ssh rejects root-owned includes inside the user namespace
TREES=${HIDE_TREES:-"$HOME/simulation /opt/eda $HOME/.ic-opt"}
HIDE="--ro-bind $EMPTY $CSHRC --tmpfs /etc/ssh/ssh_config.d"
for tree in $TREES; do HIDE="$HIDE --tmpfs $tree"; done

sandbox() {
    # bwrap mounts in argument order and reads a bind's source on the host, so binding site.yaml after the tmpfs
    # over ~/.ic-opt leaves that one file, read-only, in an otherwise empty directory
    # shellcheck disable=SC2086
    bwrap --ro-bind / / --dev /dev --proc /proc --tmpfs /tmp $HIDE --ro-bind "$SITE" "$SITE" \
        --bind "$PROJECT" "$PROJECT" --chdir "$REPO" "$@"
}

echo "== sandbox check: remote-owned paths must be invisible to the controller; its site.yaml visible, read-only"
sandbox /bin/sh -c 'for t in '"$TREES"'; do echo "$t: $(ls -A "$t" | wc -l) entries" $(ls -A "$t"); done;
    test ! -s '"$CSHRC"' && echo "cshrc empty locally" &&
    test -s '"$SITE"' && ! (: >> '"$SITE"') 2>/dev/null && echo "site.yaml readable, not writable"'

echo "== local doctor inside the sandbox must FAIL on tools and exports (nothing Cadence is visible locally)"
sandbox "$REPO/.venv/bin/ic-opt" doctor "$PROJECT" --cshrc "$CSHRC" || true

echo "== remote doctor + plan"
sandbox "$REPO/.venv/bin/ic-opt" run fix_run "$PROJECT" points=points.json --plan --ssh-profile "$PROFILE" --cshrc "$CSHRC"

echo "== remote run"
sandbox "$REPO/.venv/bin/ic-opt" run fix_run "$PROJECT" points=points.json --ssh-profile "$PROFILE" --cshrc "$CSHRC"
