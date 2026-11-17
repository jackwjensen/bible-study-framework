#!/usr/bin/env python3
"""The repository's own shape: what its files are CALLED, and what never enters it.

Every other rule in this scanner reads source and judges the code inside it. These two
judge the tree itself, which is why they share a module: they change for the same reason
(the estate picked up a new framework or a new stack), and neither has anything to say
about a line of code.

    generic-filename        a file named after nothing -- utils.ts, Service.cs
    gitignore-build-output  generated output, or a secret, that version control will take
    gitattributes-eol       a shell-read file a checkout may turn CRLF (standards_gitattributes.py)

WHY A FILENAME IS A STANDARD AND NOT A PREFERENCE. naming.md's thesis is that a
declaration must answer "what is this?" without the reader navigating away, and a path is
a declaration read far more often than the code in it -- it is what a grep result, a stack
trace, a diff header, a file tree and an agent's search all show first. `utils.ts` answers
nothing: the reader has to open it to learn whether it holds date maths or a Stripe client,
and the next person with a homeless function will add theirs to it, because a name that
means nothing excludes nothing. That is the actual mechanism by which junk-drawer files
grow, and it is a naming problem before it is a refactoring one.

The rule is deliberately QUIET, in the same way every other rule here errs toward silence.
It fires only when the WHOLE name is generic -- `stringUtils.ts` and `InvoiceService.cs`
are qualified and pass. A framework-mandated name (`models.py`, `page.tsx`, `Program.cs`)
is never a finding: Django's loader and Next's router resolve those by name, so the author
had no choice, and flagging a name somebody could not have changed is how a gate teaches
people it is noise.

WHY GITIGNORE IS IN A STANDARDS SCANNER. Two reasons, and the second is the one that made
it worth a rule. Generated output in the tree is ordinary hygiene -- it bloats clones,
produces diffs nobody can review, and merges badly. But `.env` is the sharp case: an
unignored one is a live credential leak the moment the repo is pushed, and it is the single
most common way a secret reaches a remote. Both are fully decidable, so per the pack's
single-source rule neither gets prose anywhere.

Source of truth: engineering-standards/engineering_standards/standards_layout.py
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional

from standards_core import Violation
from standards_exemptions import exemption_reason

# The junk drawer. A file with one of these names is not named after its contents, because
# these words have no contents -- they describe the author's uncertainty, not the subject.
GENERIC_STEMS = frozenset(
    {
        "util",
        "utils",
        "utility",
        "utilities",
        "helper",
        "helpers",
        "misc",
        "miscellaneous",
        "common",
        "commons",
        "shared",
        "stuff",
        "things",
        "various",
        "other",
        "others",
        "extra",
        "extras",
        "general",
        "functions",
        "lib",
        "libs",
        "temp",
        "tmp",
        "new",
        "old",
        "backup",
        "copy",
        "final",
        "untitled",
        "file",
    }
)

# Named after its LAYER rather than its subject -- and, unlike the junk drawer above, this
# family only fires when the DIRECTORY does not supply the subject either. See
# `generic_name_kind` for why, which is the single most important calibration in this file.
#
# Contested names are deliberately ABSENT: `api`, `base`, `core`, `main`, `store`, `hooks`,
# `types`, `config` and `constants` all have a real convention behind them somewhere in this
# estate (`settings/base.py`, `store.ts`, `main.py`), and a rule that argues with a
# convention loses. The rule only claims the names nobody defends.
BARE_ROLE_STEMS = frozenset(
    {
        "service",
        "services",
        "endpoint",
        "endpoints",
        "controller",
        "controllers",
        "repository",
        "repositories",
        "handler",
        "handlers",
        "manager",
        "managers",
        "provider",
        "providers",
        "factory",
        "factories",
        "mapper",
        "mappers",
        "validator",
        "validators",
        "adapter",
        "adapters",
        "builder",
        "builders",
        "wrapper",
        "wrappers",
        "dto",
        "dtos",
        "model",
        "models",
        "entity",
        "entities",
        "interfaces",
        "impl",
        "implementation",
    }
)

# Names a framework RESOLVES BY NAME, so the author had no say. Matched as the whole
# filename including its extension, not as a stem, because the mandate is per-language:
# Django resolves `models.py`, and nothing whatsoever resolves `models.ts`.
#
# Over-exempting here is the safe direction. A false negative costs one badly-named file;
# a false positive on a name the framework requires makes the rule un-fixable, and an
# un-fixable rule gets switched off.
FRAMEWORK_FILENAMES = frozenset(
    {
        # Python, Django, pytest
        "__init__.py",
        "__main__.py",
        "main.py",
        "manage.py",
        "setup.py",
        "conftest.py",
        "settings.py",
        "base.py",
        "urls.py",
        "wsgi.py",
        "asgi.py",
        "admin.py",
        "apps.py",
        "models.py",
        "views.py",
        "forms.py",
        "serializers.py",
        "signals.py",
        "tests.py",
        "middleware.py",
        "permissions.py",
        "managers.py",
        "tasks.py",
        "filters.py",
        "routers.py",
        "validators.py",
        "utils.py",
        # JavaScript / TypeScript module resolution, Next.js file routing, Vite entry points
        "index.ts",
        "index.tsx",
        "index.js",
        "index.jsx",
        "index.mjs",
        "index.cjs",
        "main.ts",
        "main.tsx",
        "main.js",
        "main.jsx",
        "app.ts",
        "app.tsx",
        "app.js",
        "app.jsx",
        "page.tsx",
        "page.ts",
        "layout.tsx",
        "layout.ts",
        "route.ts",
        "template.tsx",
        "loading.tsx",
        "error.tsx",
        "not-found.tsx",
        "default.tsx",
        "head.tsx",
        "middleware.ts",
        "store.ts",
        "types.ts",
        # .NET and Blazor
        "program.cs",
        "startup.cs",
        "globalusings.cs",
        "assemblyinfo.cs",
        "globalsuppressions.cs",
        "index.razor",
        "app.razor",
        "error.razor",
        "_imports.razor",
        "mainlayout.razor",
        # PHP front controllers
        "index.php",
        "bootstrap.php",
    }
)

# `utils.py` is in the list above and that is not a typo. Django and Flask projects across
# this estate use it as a settled convention, and the app-level `utils.py` is understood by
# every Python developer who will ever open one. The rule keeps its teeth where the name is
# genuinely a choice -- `utils.ts`, `Utils.cs`, `utils.php` all still fire.

# A test is named after its subject by convention, so it inherits whatever the subject is
# called. Flagging `utils.test.ts` beside `utils.ts` reports one naming decision twice, and
# renaming the subject renames the test for free.
TEST_NAME_MARKERS = (".test.", ".spec.", "_test.", "-test.")
TEST_PATH_FRAGMENTS = ("/test/", "/tests/", "/__tests__/", "/spec/", "/testing/")

# Directory names that carry no subject of their own -- containers, not domains. A file
# sitting directly in one of these has nothing above it to inherit meaning from, which is
# what makes `src/service.ts` different from `accounts/service.py`.
GENERIC_DIRECTORIES = GENERIC_STEMS | frozenset(
    {
        "src",
        "source",
        "app",
        "apps",
        "packages",
        "modules",
        "internal",
        "pkg",
        "backend",
        "frontend",
        "web",
        "server",
        "client",
        "components",
        "pages",
        "routes",
        "features",
        "views",
        "scripts",
    }
)


def leading_name_segment(filename: str) -> str:
    """The name before any extension, however many the file carries.

    `settings.component.tsx` is `settings`, `utils.test.ts` is `utils`, `__init__.py` is
    `__init__`. Taking `Path.stem` instead would leave `settings.component`, which matches
    nothing and would make the whole rule silently inert on Angular-style filenames.
    """
    return filename.split(".", maxsplit=1)[0]


def is_test_file(path: Path) -> bool:
    lowered = path.name.casefold()
    if lowered.startswith("test_") or any(marker in lowered for marker in TEST_NAME_MARKERS):
        return True
    posix = "/" + path.as_posix().casefold() + "/"
    return any(fragment in posix for fragment in TEST_PATH_FRAGMENTS)


def directory_supplies_subject(path: Path, repo_root: Path) -> bool:
    """Whether the containing directory names a subject the file can inherit.

    `accounts/service.py` is legible; `src/service.ts` is not, and the only difference is
    one directory name. A file directly in the repo root inherits nothing -- the root is
    the repository, not a domain.
    """
    parent = path.parent
    if parent == repo_root:
        return False
    return parent.name.casefold() not in GENERIC_DIRECTORIES


def generic_name_kind(path: Path, repo_root: Path) -> Optional[str]:
    """Why this filename says nothing, or None when it says something.

    Returns the phrasing used in the message, so the two families stay distinguishable in
    a report: a junk drawer and a bare layer noun are different mistakes with different
    fixes.

    THE TWO FAMILIES TREAT THE DIRECTORY DIFFERENTLY, and getting that wrong was the
    rule's first calibration error. Run across the estate it reported 13 filenames, of
    which 8 were `apps/legal/services.py`, `apps/ai/adapters.py`, `engine/corpus/
    repository.py` and their siblings -- a domain directory holding a role-named file,
    which is the Django app layout and the package-per-domain layout that sourcetext.ai
    and allegro-it-services are deliberately written in. The path already reads as a
    coordinate: the directory says WHAT, the file says WHICH LAYER, and nothing is
    ambiguous. Flagging those is a rule arguing with a convention, which it loses.

    So a bare role noun fires only where the directory ALSO says nothing. A junk drawer
    fires everywhere, because "helpers" survives any amount of context: `acl/helpers.py`
    still tells you nothing about what is inside, and still accepts anything ACL-adjacent
    that nobody wanted to name. That asymmetry is the rule.

    This is a deliberate disagreement with the source the rule came from, which prefers
    `CreateItem/CreateItemEndpoint.cs` over `CreateItem/Endpoint.cs`. In a vertical-slice
    layout the directory is already the qualifier; repeating it in the filename is a
    defensible style, not a defect, and a gate has no business enforcing the difference.
    """
    if path.name.casefold() in FRAMEWORK_FILENAMES:
        return None
    if is_test_file(path):
        return None

    stem = leading_name_segment(path.name).casefold()
    if stem in GENERIC_STEMS:
        return "a junk drawer: the name describes no subject, so nothing is out of place in it"
    if stem in BARE_ROLE_STEMS and not directory_supplies_subject(path, repo_root):
        return "named after its layer, in a directory that names no subject either"
    return None


def check_generic_filename(path: Path, lines: list[str], repo_root: Path) -> Iterable[Violation]:
    """Flag a file whose name identifies nothing.

    Reported at line 1 and quoting the FILENAME, which is what the baseline keys on
    (`file::rule::first-quoted-token`). One finding per file and one key per file, so
    grandfathering one name cannot grandfather anything else -- the trap `razor-var` fell
    into in draft by quoting the constant keyword `var`.
    """
    kind = generic_name_kind(path, repo_root)
    if kind is None:
        return

    if exemption_reason(lines, "generic-filename") is not None:
        return

    yield Violation(
        path=path,
        line=1,
        rule="generic-filename",
        message=(
            f"'{path.name}' is {kind}. Name it after what it holds, so a grep hit, a stack "
            f"frame and a diff header each identify it without opening it -- "
            f"see .claude/rules/naming.md."
        ),
    )
