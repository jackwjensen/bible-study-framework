#!/usr/bin/env python3
"""Which toolchains exist, and which rules a baseline may never grandfather.

Split out of check-source-limits.py on 2026-08-27, when adding the `--paydown` gate put that
file at 511 of its own 500 lines. The seam is the subject: this module changes when a
toolchain is added or a rule is judged un-deferrable, while the driver changes when walking,
reporting or the CLI does -- two different reasons, two different readers. Shaving a comment
back under the limit is what the pack's own CLAUDE.md names as the wrong answer every time.

It also removes an oddity the split made obvious: the registry sat WEDGED between two blocks
of imports in the driver, because it needed the rule constants and the later imports needed
nothing from it. As its own module the imports are simply at the top, where they belong.

Re-exported by check-source-limits.py, so `driver.NEVER_BASELINED` keeps working -- that is
the name test_standards_packages.py reaches for.

Source of truth: engineering-standards/engineering_standards/standards_registry.py
"""

from __future__ import annotations

from standards_deps_pinning import UNPINNED_RULE
from standards_dotnet_images import DOTNET_TOOLCHAIN
from standards_ef_provider import EF_PROVIDER_RULE
from standards_node_support import NODE_TOOLCHAIN
from standards_packages import HOLDBACK_RULE, WILDCARD_RULE
from standards_php_support import PHP_RULE, PHP_TOOLCHAIN
from standards_python_consistency import PYTHON_TOOLCHAIN
from standards_python_support import PYTHON_RULE
from standards_rules import BUDGET_RULE
from standards_secrets import SECRET_RULE
from standards_user_errors import TECHNICAL_ERROR_RULE
from standards_versions import RUNTIME_RULE

# The registry lives HERE rather than in the engine, so the engine depends on no toolchain and
# a toolchain depends on no registry. Assembling it inside standards_toolchain_consistency.py
# would make that module import all four, and each of those import it back for `Toolchain`.
TOOLCHAINS = (PYTHON_TOOLCHAIN, NODE_TOOLCHAIN, PHP_TOOLCHAIN, DOTNET_TOOLCHAIN)

# Rules --write-baseline must never grandfather. See the note at the baseline write in the
# driver for why a support deadline is categorically not debt; the short version is that the
# date arrives regardless, so baselining one silences the warning without deferring anything.
#
# PYTHON_RULE and PHP_RULE join them for exactly that reason -- each is `runtime-support` for a
# different toolchain, and a release that has left bugfix support does not un-leave it because a
# baseline file recorded the fact. The whole *-consistency family joins them for a different
# one: a baselined "CI and production run different versions" is not deferred debt, it is a live
# bug filed as accepted, and the reason it needs enforcing at all is that nothing else about the
# repo looks wrong. All of them remain line-exemptable, which is the difference that matters --
# an exemption states a reason a human can read and disagree with; a baseline entry states
# nothing. Derived from TOOLCHAINS rather than listed, so a fifth toolchain cannot be added
# with this protection quietly missing.
#
# BUDGET_RULE joins them for a third reason, and it is the sharpest of the three: the always-
# load budget is a measurement of what every session pays, so a baseline entry saying "this
# repo is 300 lines over" does not defer the cost -- the cost is charged on every run
# regardless, invisibly, which is the exact failure the rule was written to end. Its two
# siblings, rules-scope-declared and rules-file-length, are ordinary debt and stay baselineable;
# they name a file somebody can go and fix. The budget names a decision, and `.standards.json`
# is where a decision is recorded with its reason.
#
# UNPINNED_RULE joins them for package-wildcard's reason: a floating version is not reproducible
# today, and "blocking is the pack doing its job" (Jack, 2026-10-02) -- a repo pins, it does not
# grandfather. Line-exemptable like the rest.
#
# TECHNICAL_ERROR_RULE joins them because each finding is a defect a user meets today -- an
# English stack-trace sentence where an explanation belongs -- and a baselined one is that bug
# filed as accepted (Jack, 2026-10-06, after InvoTrack's seventy). A repo on its next pull fixes
# the site, declares its carrier in `userFacingExceptions`, or marks the line with a reason.
NEVER_BASELINED: frozenset[str] = frozenset(
    {
        TECHNICAL_ERROR_RULE,
        RUNTIME_RULE,
        EF_PROVIDER_RULE,
        PYTHON_RULE,
        PHP_RULE,
        WILDCARD_RULE,
        SECRET_RULE,
        HOLDBACK_RULE,
        BUDGET_RULE,
        UNPINNED_RULE,
    }
    | {toolchain.rule for toolchain in TOOLCHAINS}
)
