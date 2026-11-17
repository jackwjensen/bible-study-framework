#!/usr/bin/env python3
"""Which exemption markers actually do something, and a rule that reports the ones that do not.

`standards_exemptions.py` owns the marker SYNTAX -- one parser, one reason floor, two scopes.
This module owns the other half of the contract: which tag a rule will actually read, and at
which scope. Nothing enforced that before, and the gap ran the wrong way.

THE DEFECT THIS CLOSES IS THE INVERSE OF THE ONE `collect_exemptions` WAS WRITTEN TO CLOSE.
That report exists because a marker could silence a rule invisibly, so it names every
relaxation the scanner honours. But `header_exemptions` reads whatever tag it FINDS -- which
is right, and is what stopped a hand-kept list of three rules from hiding the other
seventeen -- and nothing then checked that any rule would read that tag back. So a run could
print

    exempt: engine/config.py [const-environment-literal] -- <a reason somebody wrote>

and report the finding two lines later, because `check_environment_literal` consulted no
exemption helper at all. Found on sourcetext.ai, 2026-09-09, on three files. A report that
claims a decision the scanner ignores is worse than no report: the claim is the thing people
rely on, and this one was reassuring in exactly the runs where it was wrong.

WHY A VIOLATION RATHER THAN A QUIETER REPORT. The alternatives were to label such markers
inert in the summary, or to drop them from it. Both leave the marker sitting in the file and
its author still believing it works -- which is the whole failure. Jack's call, 2026-09-09:
a marker that does nothing is a defect in the file, so somebody deletes it or fixes the tag.
It catches a misspelled tag too, which no other detector in the pack could see at all.

TWO INPUTS DECIDE, AND NEITHER IS A SECOND IMPLEMENTATION OF THE FIRST. The registry below
says which tags are real at which scope; the READERS themselves say whether a marker in this
position would be found. Asking `exemption_reason` and `line_exemption_reason` rather than
re-deriving where a marker carries is the point: a rule that disagreed with the readers about
what is honoured would be the very defect this module exists to end. It also settles the case
that looks like a contradiction -- a marker in a `#` block directly above the first code line
is inside the header AND within the line reader's lookbehind, so it is live at both scopes,
and neither answer alone is right.

THE TWO SETS ARE HAND-WRITTEN AND MACHINE-CHECKED. A hand-kept list of what the code does is
precisely the claim this pack refuses elsewhere, so `test_standards_exemption_scope.py`
derives both from the source and fails if either drifts. Adding a hatch to a rule means
adding its tag here, in the same commit, or the suite says so.

Source of truth: engineering-standards/engineering_standards/standards_exemption_scope.py
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional

from standards_assets import RULE as ASSET_UNVERSIONED_RULE
from standards_client_address import CLIENT_ADDRESS_RULE
from standards_concurrency import BULK_RULE, RETRY_RULE
from standards_constants import DUPLICATED_LITERAL_RULE, ENVIRONMENT_LITERAL_RULE
from standards_core import Violation, iter_code_lines
from standards_cors import CORS_RULE
from standards_debugflag import RULE as DEBUG_FLAG_RULE
from standards_deps_pinning import UNPINNED_RULE
from standards_disclosure import DISCLOSURE_RULE, SQL_DUMP_RULE
from standards_dotnet_images import DOTNET_CONSISTENCY_RULE
from standards_ef_provider import EF_CORE_RULE, EF_PROVIDER_RULE
from standards_encoding import RULE as MOJIBAKE_RULE
from standards_exemptions import (
    EXEMPTION_MARKER,
    HEADER_SCAN_LINES,
    MAX_LINE_MARKER_LOOKBEHIND,
    MIN_EXEMPTION_REASON_LENGTH,
    exemption_reason,
    file_header_end,
    line_marker_starts,
    wrapped_reason,
)
from standards_injection import PHP_EVAL_RULE
from standards_injection import RULE as JS_EVAL_RULE
from standards_jobs import SWALLOWS_RULE
from standards_node_support import NODE_CONSISTENCY_RULE, NODE_RULE
from standards_packages import WILDCARD_RULE
from standards_paging import PAGING_RULE
from standards_php_support import PHP_CONSISTENCY_RULE, PHP_RULE
from standards_python_consistency import CONSISTENCY_RULE as PYTHON_CONSISTENCY_RULE
from standards_python_support import PYTHON_RULE
from standards_rules import LENGTH_RULE as RULES_FILE_LENGTH_RULE
from standards_runner import RUNNER_RULE
from standards_secrets import SECRET_RULE
from standards_seed_credentials import SEEDED_RULE
from standards_sqlinjection import RULE as SQL_INTERPOLATION_RULE
from standards_tests import (
    TEST_ALWAYS_PASSES_RULE,
    TEST_SILENTLY_SKIPPED_RULE,
    TEST_WITHOUT_ASSERTION_RULE,
    TESTS_UNCOVERED_MODULE_RULE,
)
from standards_tls import TLS_RULE
from standards_user_errors import TECHNICAL_ERROR_RULE
from standards_versions import RULE as ACTION_VERSION_RULE
from standards_versions import RUNTIME_RULE, TOOLCHAIN_RULE
from standards_write_results import RULE as WRITE_RESULT_RULE

INERT_RULE = "exemption-inert"

# Tags read from the file HEADER, via `exemption_reason`. The rule's subject is the file --
# its length, its whole purpose -- or the file is a detector quoting the very shapes it
# detects, which is why so many of these are the pack's own modules' escape hatch.
#
# EACH TAG IS ITS RULE'S OWN CONSTANT WHEREVER THE RULE DECLARES ONE, never a second copy of
# the string. That is `const-duplicated-literal` applied to this file -- it fired on the first
# draft, which spelled all 46 out as literals, and it was right: a registry that repeats a
# tag is a second home for it, so renaming `CORS_RULE` would leave this file silently naming
# a rule that no longer exists. Referencing makes the rename an import error instead. The
# seventeen written as plain strings have no constant anywhere; the helper call site is their
# only home, and inventing one here just to import it back would be the same duplication in
# the other direction.
FILE_SCOPED_TAGS = frozenset(
    {
        BULK_RULE,
        CLIENT_ADDRESS_RULE,
        CORS_RULE,
        # This module's own rule, honoured at file scope for the reason every detector in the
        # pack needs one: a file whose subject IS the marker syntax quotes inert markers as
        # fixtures. Without it, this module and its suite would report themselves.
        INERT_RULE,
        PAGING_RULE,
        RETRY_RULE,
        RULES_FILE_LENGTH_RULE,
        SQL_DUMP_RULE,
        SQL_INTERPOLATION_RULE,
        SWALLOWS_RULE,
        TESTS_UNCOVERED_MODULE_RULE,
        TEST_ALWAYS_PASSES_RULE,
        TEST_SILENTLY_SKIPPED_RULE,
        TEST_WITHOUT_ASSERTION_RULE,
        TLS_RULE,
        WRITE_RESULT_RULE,
        "claude-md-length",
        "docs-orphan-page",
        # Reached through `exemption_reason(read_lines(command), ...)`, whose nested call the
        # drift test's first extractor stopped reading at the inner `)`. It then declared the
        # tag unread, and this rule duly reported the one file that uses it -- a registry
        # under-reporting is not a quiet failure, it calls working markers dead.
        "docs-uncovered-command",
        "docs-plan-page",
        "file-length",
        "filter-in-memory",
        "generic-filename",
        "query-shape",
        "userdocs-unlinked-page",
    }
)

# Tags read beside the flagged LINE, via `line_exemption_reason`. These rules fire per
# expression, and a file routinely holds both a genuine defect and a legitimate exception --
# `line_exemption_reason` states that case in full.
#
# EIGHT TAGS ARE IN BOTH SETS, and that is not an error: `client-address`, `cors-wildcard`,
# `tls-verification-disabled`, `sql-string-interpolation`, `job-swallows-failure`,
# concurrency's two and `write-result-discarded` take a marker at EITHER scope, because a
# detector quoting its own patterns needs the file-wide form while ordinary code needs the line. The first draft of this
# registry subtracted one set from the other and lost all seven; the drift test caught it
# before the rule could call seven working markers dead.
LINE_SCOPED_TAGS = frozenset(
    {
        ACTION_VERSION_RULE,
        ASSET_UNVERSIONED_RULE,
        BULK_RULE,
        CLIENT_ADDRESS_RULE,
        CORS_RULE,
        DEBUG_FLAG_RULE,
        DISCLOSURE_RULE,
        DUPLICATED_LITERAL_RULE,
        EF_CORE_RULE,
        EF_PROVIDER_RULE,
        ENVIRONMENT_LITERAL_RULE,
        JS_EVAL_RULE,
        MOJIBAKE_RULE,
        NODE_RULE,
        PHP_EVAL_RULE,
        PHP_RULE,
        PYTHON_RULE,
        RETRY_RULE,
        RUNNER_RULE,
        RUNTIME_RULE,
        SECRET_RULE,
        SEEDED_RULE,
        SQL_INTERPOLATION_RULE,
        SWALLOWS_RULE,
        TECHNICAL_ERROR_RULE,
        TEST_ALWAYS_PASSES_RULE,
        TEST_SILENTLY_SKIPPED_RULE,
        TEST_WITHOUT_ASSERTION_RULE,
        TLS_RULE,
        TOOLCHAIN_RULE,
        UNPINNED_RULE,
        WILDCARD_RULE,
        WRITE_RESULT_RULE,
        # The four toolchain-consistency rules reach the reader as `toolchain.rule`, an
        # attribute of the Toolchain each of them declares. Nothing textual could resolve
        # that, which is why `INDIRECT_DISPATCH` below names the site rather than letting the
        # drift test shrug at an argument it cannot follow.
        DOTNET_CONSISTENCY_RULE,
        NODE_CONSISTENCY_RULE,
        PHP_CONSISTENCY_RULE,
        PYTHON_CONSISTENCY_RULE,
        "compose-read-only",
        "container-root-user",
        "deploy-no-hostkey",
        "deploy-no-pull",
        "deploy-root-ssh",
        "docs-stale-symbol",
        "docs-uncovered-route",
        "unset-not-zero",
        "userdocs-dangling-slug",
        "userdocs-orphan-topic",
    }
)


def _is_quoted_text(line: str, position: int) -> bool:
    """Whether the marker at `position` is inside a string literal rather than a comment.

    EVERY RULE WITH A HATCH NAMES IT IN ITS OWN MESSAGE -- "or mark 'standards: <tag> exempt
    -- <why>' in its header" -- and those sentences sit on code lines, where the line
    reader's trailing-marker case reaches them. Five of the pack's own rules were reported by
    their own instructions before this test existed.

    An unclosed quote before the marker is what separates the two. Applied ONLY on code
    lines, because in a comment an apostrophe is prose -- "the file's owner" would read as an
    open quote and put the false positives back the other way round.
    """
    before = line[:position]
    return before.count('"') % 2 == 1 or before.count("'") % 2 == 1


def _reachable_at_line_scope(lines: list[str], index: int, code_lines: list[int]) -> bool:
    """Whether a marker at `index` sits where a LINE-scoped read would find it.

    Asks `line_marker_starts` -- the readers' own walk -- from each line a rule could
    actually FLAG, rather than re-deriving where a comment block ends. Registry-blind and
    floor-blind on purpose: this answers "would anybody look here?", and a marker whose tag
    is wrong or whose reason is a shrug is exactly what still has to be reported.

    `code_lines` is what makes this precise. A rule flags a line of CODE, so probing from the
    marker's own line would read it straight back off itself and call every marker live --
    including the ones sitting in prose. `iter_code_lines` already skips comments and
    docstrings for the same reason this rule needs it: "prose explaining a rule necessarily
    contains an example of the thing the rule forbids".
    """
    return any(
        index in line_marker_starts(lines, probe - 1)
        for probe in code_lines
        if index <= probe - 1 <= index + MAX_LINE_MARKER_LOOKBEHIND
    )


def _why_inert(lines: list[str], index: int, tag: str, reachable: tuple[bool, bool], reason: str) -> Optional[str]:
    """What is wrong with this marker, or None when it is fine as written.

    `reachable` is (header, line) -- whether either reader's window covers this position.
    NEITHER MEANS THIS IS NOT A MARKER AT ALL, and that test comes first. The modules and
    rules pages that document the marker syntax quote it constantly, and sixty-six of this
    rule's first seventy-eight findings on the pack were prose of that kind: the canonical
    `file-length` example in `exemption_reason`'s docstring, the SQL form in
    `standards_disclosure`, a `<!-- ... -->` case study at line 210. A rule that noisy gets
    baselined wholesale and then means nothing. Nobody wrote those expecting a rule to read
    them, and no rule was ever going to.

    Past that, four outcomes needing four different fixes: lengthen the reason, move the
    marker up, move it down, or correct the tag.
    """
    reachable_at_file, reachable_at_line = reachable
    if not reachable_at_file and not reachable_at_line:
        return None

    if len(reason) < MIN_EXEMPTION_REASON_LENGTH:
        return (
            f"the reason is {len(reason)} characters and the floor is {MIN_EXEMPTION_REASON_LENGTH}, "
            f"so every reader in the pack ignores it and '{tag}' is not switched off. Write a "
            f"reason you would defend in review, or delete the marker and fix the finding."
        )

    if reachable_at_file and tag in FILE_SCOPED_TAGS:
        return None
    if reachable_at_line and tag in LINE_SCOPED_TAGS:
        return None

    return _wrong_place_message(tag)


def _wrong_place_message(tag: str) -> str:
    """Where this tag IS read, for a marker that is not there -- or that no rule reads at all.

    Its own function because the three sentences are the actionable half of the rule and
    `_why_inert` was one return statement over the pack's limit with them inline. Splitting
    on the seam between "is this wrong" and "what do I do about it" is the answer that limit
    exists to push for; trimming a message to fit would have been the answer it exists to
    prevent.
    """
    if tag in FILE_SCOPED_TAGS:
        return (
            f"'{tag}' is read only from the file header, so this marker excuses nothing where "
            f"it sits. Write it in the file header instead -- the module docstring or the top "
            f"comment block, above the first line of code."
        )

    if tag in LINE_SCOPED_TAGS:
        return (
            f"'{tag}' is read only beside the line it excuses, so a header marker for it "
            f"excuses nothing. Write it beside the line instead -- as a trailing comment on "
            f"it, or on the line immediately above -- which also stops it covering the next "
            f"defect of the same shape in this file."
        )

    return (
        f"no rule reads an exemption tagged '{tag}', so this marker does nothing. Check the "
        f"spelling against the rule name in the finding you meant to excuse; some rules "
        f"deliberately have no exemption at all, and for those the answer is to fix the code."
    )


def check_inert_exemption(path: Path, lines: list[str]) -> Iterable[Violation]:
    """An exemption marker no rule will read, at the position it was written.

    RUNS ON EVERY FILE TYPE, above the language dispatch, because the marker syntax is
    language-agnostic by design -- `#`, `//`, `--`, `/* */`, `<!-- -->`, `@* *@` -- and the
    file types most likely to carry a stale one are the compose files, workflows and markdown
    pages that reach none of the language branches.

    Walks the whole file rather than the header alone: a marker below the header is a
    LINE-scoped marker, which is a valid thing to write and must be judged against the line
    scope rather than ignored. That is also what lets a header-only tag be caught when
    somebody writes it beside a query instead.
    """
    if not lines or exemption_reason(lines, INERT_RULE) is not None:
        return

    # The FILE reader's exact window: the documentation region, capped at the scan limit.
    # Using either bound alone would misjudge the two shapes that actually occur -- a marker
    # under a long module docstring is past the cap though still in the header, and one below
    # the first line of code is inside the cap though out of the header.
    header_window = min(HEADER_SCAN_LINES, file_header_end(lines))
    code_lines = [number for number, _ in iter_code_lines(lines, path.suffix)]

    for index, line in enumerate(lines):
        match = EXEMPTION_MARKER.search(line)
        if match is None:
            continue
        if index + 1 in code_lines and _is_quoted_text(line, match.start()):
            continue

        # Measured with the same walker the readers use, so the length reported here is the
        # length they applied. A reason judged by a second implementation would put this rule
        # in exactly the position it exists to end.
        why = _why_inert(
            lines,
            index,
            match.group("tag").casefold(),
            (index < header_window, _reachable_at_line_scope(lines, index, code_lines)),
            wrapped_reason(lines, index, match),
        )
        if why is None:
            continue

        yield Violation(
            path=path,
            line=index + 1,
            rule=INERT_RULE,
            message=f"This exemption marker does nothing: {why}",
        )
