#!/bin/sh
# Shared body for the pre-commit and pre-push hooks: find a Python, run verify.py.
#
# Not itself a hook -- git only invokes files named after a hook, so the leading underscore
# keeps it out of the way while letting both hooks share one copy of this logic.
#
# Source of truth: engineering-standards/engineering_standards/hooks/_verify.sh

set -e

REPO_ROOT=$(git rev-parse --show-toplevel)
VERIFY="$REPO_ROOT/engineering_standards/verify.py"

if [ ! -f "$VERIFY" ]; then
    # Genuinely not a standards repo -- a hook installed somewhere unrelated must be a silent
    # no-op, or it becomes a reason to unset core.hooksPath everywhere.
    #
    # But hooks being WIRED and finding no scanner is a different thing: somebody configured a
    # gate that cannot run, and every commit since has reported nothing. That is the exact
    # skip-reads-as-pass failure this pack exists to end, so it gets a word -- on stderr, and
    # still exit 0, because refusing to commit would punish the misconfiguration rather than
    # report it. (Copied from the B3D pack, which found its own gate in this state, 2026-09-05.)
    if [ -d "$REPO_ROOT/engineering_standards/hooks" ]; then
        echo "standards: hooks are wired here, but engineering_standards/verify.py was not found --" >&2
        echo "  so NO GATES RAN. Run /apply-standards in this repo, or unset core.hooksPath." >&2
    fi
    exit 0
fi

PYTHON=$("$(dirname "$0")/_python.sh")

if [ -z "$PYTHON" ]; then
    # Fail open, but LOUDLY. A GUI git client can carry a different PATH from the terminal,
    # and refusing the commit would be a confusing dead end; silently passing would be the
    # failure this pack exists to prevent. CI is the authoritative backstop either way.
    echo "standards: no python on PATH, so the gate did NOT run. CI still enforces it." >&2
    exit 0
fi

exec "$PYTHON" "$VERIFY" "$@"
