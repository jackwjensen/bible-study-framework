#!/bin/sh
# Which Python the hooks should run. Prints the interpreter name, or nothing if there is none.
#
# Split out of _verify.sh when pre-push gained a SECOND Python step. The ordering below is a
# decision with a reason, and a second copy of a reasoned decision is a second thing to get
# wrong -- the same single-source rule this pack applies to everything else.
#
# Callers handle "nothing" themselves, because the right answer differs: _verify.sh fails
# open loudly (refusing the commit would be a confusing dead end), while the skill sync just
# does not happen.
#
# `py` last: it is the Windows launcher and works, but `python` from an active virtualenv
# should win when there is one.
#
# The candidate is EXECUTED, not merely located. On Windows the first `python` on PATH is
# very often the Microsoft Store alias stub: `command -v` finds it and reports success, but
# running it prints an install advert and exits non-zero. Selecting it made the hook fail
# with a Store advertisement instead of a verification result -- and `python` is checked
# first here precisely because a virtualenv should win, which is the same name the stub uses.
# `-c 'pass'` is the cheapest question that only a working interpreter answers.
#
# Source of truth: engineering-standards/engineering_standards/hooks/_python.sh

for candidate in python python3 py; do
    if "$candidate" -c 'pass' >/dev/null 2>&1; then
        printf '%s\n' "$candidate"
        exit 0
    fi
done

exit 0
