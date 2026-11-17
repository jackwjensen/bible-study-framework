#!/usr/bin/env python3
"""docs-stale-symbol: backtick-quoted names in docs/ that the codebase no longer contains.

The docs version of the code review's "confirm every helper the diff references actually
exists": a renamed helper leaves its old name behind in prose, and the prose goes on
teaching the wrong name. Split out of standards_docs when that module crossed the
file-length limit -- symbol resolution (the repository inventory, the monorepo package
fallback, the path-versus-identifier split) is its own subject with its own change
history, while the structural rules over there share nothing with it but the primitives
in standards_markdown.

Source of truth: engineering-standards/engineering_standards/standards_symbols.py
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Iterable, Optional

from standards_core import CheckConfig, Violation, warn_unreadable
from standards_exemptions import line_exemption_reason
from standards_markdown import (
    INLINE_CODE_SPAN,
    SKIP_DIRECTORIES,
    content_lines,
    read_lines,
)
from standards_scope import should_check
from standards_tree import walk_repository

# The character set a checkable token may use at all. Anything outside it (globs, shell
# fragments, spaces, leading dots) is prose in a costume this rule does not judge.
TOKEN = re.compile(r"^[A-Za-z_][A-Za-z0-9_.\\/-]*$")

# A bare identifier, eligible for the corpus test.
IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Path-shaped: contains a separator, or ends in a short extension.
PATHISH = re.compile(r"[/\\]|\.[A-Za-z0-9]{1,5}$")

MIN_SYMBOL_LENGTH = 4


def _token_is_known(
    token: str,
    inventory: tuple[set[str], list[str], set[str], set[str], bool],
    repo_root: Path,
) -> bool | None:
    """Does the repository contain this token? None means it is not a candidate at all.

    THREE ANSWERS, not two, and the third is what keeps the rule quiet: a token that is
    neither path-shaped nor identifier-shaped is prose in a costume, and prose must not
    be reported as a stale symbol. Collapsing None into False would flag every ordinary
    backticked word in the docs tree.
    """
    (filenames, source_texts, relative_paths, command_names, from_git) = inventory

    if PATHISH.search(token):
        normalised = token.replace("\\", "/")
        if "/" in normalised:
            return (
                # Docs write a directory with a trailing slash; the index does not.
                normalised.rstrip("/") in relative_paths
                # Only when git could not answer. Consulting the filesystem on
                # a built, installed tree resolves paths a checkout does not
                # have, so the local run passes and the deploy fails.
                or (not from_git and (repo_root / normalised).exists())
                or _resolves_under_a_package(normalised, relative_paths)
                or _resolves_as_migration(normalised, relative_paths)
                or _resolves_as_qualified_symbol(normalised, relative_paths, source_texts)
            )
        # A bare filename is known if the tree contains it -- OR if the
        # source names it as a string. `manage.py` in a repo with no
        # Django is still correct prose when the detection code itself
        # says "manage.py"; the docs repeat the source, not a ghost.
        return (
            normalised in filenames
            or any(normalised in text for text in source_texts)
            or _resolves_as_member(normalised, relative_paths, repo_root)
        )

    if (
        IDENTIFIER.match(token)
        and len(token) >= MIN_SYMBOL_LENGTH
        and ("_" in token or not (token.islower() or token.isupper()))
    ):
        return (
            token in command_names
            or any(token in text for text in source_texts)
            or _names_a_migration_file(token, filenames)
        )

    return None


# `20261030215207_RegistrationsShowTheirOwnPrice.cs`, `0007_add_field.py`: a number, an
# underscore, the migration's name, a source extension.
MIGRATION_FILENAME = re.compile(r"^\d+_(?P<name>[A-Za-z_][A-Za-z0-9_]*)\.(?:cs|py|php|ts|js)$")

# `Registration.Kind`: a type, then one member. Both halves identifiers, the type
# capitalised the way a type is.
MEMBER_ACCESS = re.compile(r"^(?P<type>[A-Z][A-Za-z0-9_]*)\.(?P<member>[A-Za-z_][A-Za-z0-9_]*)$")


def _names_a_migration_file(token: str, filenames: set[str]) -> bool:
    """Whether `RegistrationsShowTheirOwnPrice` is a migration this tree still carries.

    An EF Core migration is cited by its class name, and that name is written only in the
    migration's own file -- which the source corpus leaves out as generated code, so every
    accurately cited migration read as stale (eight of them on InvoTrack, 2026-11-17). The
    file NAME is the evidence instead: a squashed or deleted migration has no file, so its
    name still fires. Narrow on purpose: only the `<number>_<name>` shape both EF Core and
    Django give a migration, never any file's stem, which would let `utils` resolve anything.
    """
    return any(
        (match := MIGRATION_FILENAME.match(name)) is not None and match.group("name") == token for name in filenames
    )


def _resolves_as_member(normalised: str, relative_paths: set[str], repo_root: Path) -> bool:
    """Whether `Registration.Kind` names a member the type's own file declares.

    A member with a short name (`Kind`, `Email`, `Next`) looks like a file extension, so the
    token was judged as a filename and fired every time. The relief asks the file named
    after the type -- `Registration.cs` -- whether it contains the member as a whole word, so
    renaming the member on that type is still caught. A member the type inherits from a
    framework base is not declared there and still fires; say so with a line marker.
    """
    match = MEMBER_ACCESS.match(normalised)
    if match is None:
        return False
    member = re.compile(rf"\b{re.escape(match.group('member'))}\b")
    type_files = {f"{match.group('type')}{suffix}" for suffix in SOURCE_SUFFIXES}
    for relative in relative_paths:
        if relative.rsplit("/", 1)[-1] not in type_files:
            continue
        try:
            text = (repo_root / relative).read_text(encoding="utf-8", errors="replace")
        except OSError as error:
            warn_unreadable(repo_root / relative, error, "docs-stale-symbol may report a false finding")
            continue
        if member.search(text):
            return True
    return False


class _Lookups:
    """The two whole-tree scans this rule needs, each computed on first use.

    LAZY BY NECESSITY, not as an optimisation. Most docs pages contain no code-shaped token
    at all, and a repository walk per page -- or per run of a rule that then finds nothing --
    is the cost that gets a rule switched off. Held as an object rather than as two
    reassigned locals so the laziness survives being passed to a helper.
    """

    def __init__(self, repo_root: Path, config: CheckConfig) -> None:
        self._repo_root = repo_root
        self._config = config
        self._inventory: tuple[set[str], list[str], set[str], set[str], bool] | None = None
        self._foreign: tuple[set[str], tuple[str, ...]] | None = None

    @property
    def inventory(self) -> tuple[set[str], list[str], set[str], set[str], bool]:
        if self._inventory is None:
            self._inventory = _repository_inventory(self._repo_root, self._config)
        return self._inventory

    @property
    def foreign(self) -> tuple[set[str], tuple[str, ...]]:
        if self._foreign is None:
            self._foreign = external_vocabulary(self._repo_root)
        return self._foreign


def _stale_spans_on_line(
    page: Path,
    lines: list[str],
    line_number: int,
    line: str,
    repo_root: Path,
    lookups: _Lookups,
) -> Iterable[Violation]:
    """Every inline code span on one line that names something the repo no longer has."""
    for span in INLINE_CODE_SPAN.findall(line):
        token = span.strip().removesuffix("()")
        if not TOKEN.match(token):
            continue
        if _is_external(token, *lookups.foreign):
            continue
        known = _token_is_known(token, lookups.inventory, repo_root)
        if known is None or known:
            continue
        if line_exemption_reason(lines, line_number - 1, "docs-stale-symbol"):
            continue
        yield Violation(
            path=page,
            line=line_number,
            rule="docs-stale-symbol",
            message=(
                f"'{token}' exists nowhere in this repository's source. Docs "
                f"teaching a name the code no longer has are worse than no docs -- "
                f"update the name, or mark 'standards: docs-stale-symbol exempt "
                f"-- <why>' beside it if the old name is the point."
            ),
        )


def check_symbols(repo_root: Path, pages: list[Path], config: CheckConfig) -> Iterable[Violation]:
    """Backtick-quoted names in docs/ that the codebase no longer contains.

    Calibrated hard toward silence -- only docs/ pages (README and CLAUDE.md are full of
    tool names the repo never defines), only inline spans, only tokens that LOOK like code
    (snake_case or CamelCase, ≥4 chars; a plain or ALL-CAPS word is prose in a costume),
    and a `()` suffix is stripped so `send_invoice()` checks the name it names.

    A path-shaped token is judged by existence instead: from the repo root OR under any
    package root when it carries a separator, by basename anywhere in the tree when it does
    not -- docs legitimately say `verify.py` for a file that lives in engineering_standards/.
    """
    if not pages:
        return

    lookups = _Lookups(repo_root, config)
    for page in pages:
        lines = read_lines(page)
        for line_number, line in content_lines(lines):
            yield from _stale_spans_on_line(page, lines, line_number, line, repo_root, lookups)


# Extensions a module may be cited without. Prose names the module, the tree stores the file.
SOURCE_SUFFIXES = (".py", ".ts", ".tsx", ".js", ".jsx", ".cs", ".php")


EXTERNAL_VOCABULARY_KEY = "externalVocabulary"


def external_vocabulary(repo_root: Path) -> tuple[set[str], tuple[str, ...]]:
    """Names and namespace prefixes this repo legitimately cites but does not own.

    The rule asks "does the source contain this name?" and treats no as stale. That is the
    right question for a repo's own vocabulary and the wrong one for everybody else's:
    documentation that explains an integration has to name `communications/onlineMeetings/
    getAllTranscripts`, `ViewSet`, `text/markdown` and `acks_late`, and none of them will
    ever appear in this source. Flagging them does not find a defect — it pressures the
    writer to delete a true sentence.

    This is NOT a baseline. A baseline says "debt we intend to pay down"; a declaration
    here says "this belongs to Microsoft / DRF / HTTP and always will". Keep it to foreign
    namespaces: putting one of your own symbols in this list hides exactly the rename the
    rule exists to catch.
    """
    try:
        with (repo_root / ".standards.json").open(encoding="utf-8") as handle:
            raw = json.load(handle).get(EXTERNAL_VOCABULARY_KEY, {}) or {}
    except OSError, json.JSONDecodeError:
        return set(), ()
    return set(raw.get("names", [])), tuple(raw.get("prefixes", []))


def _is_external(token: str, names: set[str], prefixes: tuple[str, ...]) -> bool:
    return token in names or token.startswith(prefixes)


def _resolves_as_migration(normalised: str, relative_paths: set[str]) -> bool:
    """Whether `app/0007_name` names a real migration.

    Docs cite a migration the way the framework does — app label, then the numbered file —
    because that is how you run it and how it appears in `showmigrations`. The file itself
    lives at `<anything>/app/migrations/0007_name.py`, so the literal token never resolves
    as a path and every accurately-cited migration read as a stale symbol. A truncated
    prefix is accepted (`finances/0007`) because prose routinely shortens the number.
    """
    app, _, migration = normalised.rpartition("/")
    if not app or not migration[:1].isdigit():
        return False
    marker = f"/{app.rsplit('/', 1)[-1]}/migrations/{migration}"
    return any(marker in path or f"{marker}_" in path for path in relative_paths)


def _resolves_as_qualified_symbol(normalised: str, relative_paths: set[str], source_texts: list[str]) -> bool:
    """Whether `services/meeting.list_meeting_organizers` names a real module member.

    A module path plus a dotted member is the clearest way to write "this function, in that
    file", and both halves exist — but the combined string appears in no file, so the token
    resolved as neither a path nor an identifier. Both halves are checked: the module must
    exist, AND the member must appear in the source, so a real rename is still caught.
    """
    module, _, member = normalised.rpartition(".")
    if not module or not member or "/" not in module:
        return False
    module_exists = _resolves_under_a_package(module, relative_paths) or any(
        path.endswith(f"/{module}.py") or f"/{module}/" in path for path in relative_paths
    )
    return module_exists and any(member in text for text in source_texts)


def _version_controlled_paths(repo_root: Path) -> Optional[list[str]]:
    """Every path git would ship, or None where git cannot answer.

    Resolution used to consult the filesystem, which quietly asks a different question on
    a developer's machine than in CI: a built, installed tree contains
    `packages/webapp/build`, `public/sitemap.xml` and `node_modules/.bin`, and a fresh
    checkout contains none of them. Documentation naming one passed locally and failed the
    deploy — the worst split, because the local run is the one people trust.

    `--cached --others --exclude-standard` is the honest set: everything tracked, plus new
    files not yet added, minus everything `.gitignore` excludes. A file created in the same
    commit as the docs that describe it therefore still resolves, while a build artifact
    never does — on either machine.
    """
    try:
        completed = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],  # noqa: S607  (git must come from PATH)
            cwd=repo_root,
            capture_output=True,
            text=True,
            # This lists every tracked path, so it is the call most likely to meet a
            # non-ASCII filename. Under the locale codec one such file would raise and drop
            # the whole listing to None, and the fallback ("walk the tree") is a different,
            # more generous answer -- a decode error would silently change what resolves.
            encoding="utf-8",
            errors="replace",
            timeout=60,
            shell=False,
            check=False,  # a non-zero code means "not a git repo", handled below
        )
    except OSError, subprocess.SubprocessError:
        return None
    if completed.returncode != 0:
        # Not a git repository, or git is absent. Fall back to walking the tree: a slightly
        # generous answer beats refusing to resolve anything.
        return None
    return [line for line in completed.stdout.splitlines() if line]


def _resolves_under_a_package(normalised: str, relative_paths: set[str]) -> bool:
    """Whether a repo-root-relative miss resolves under some package root instead.

    A monorepo's docs write source paths relative to the PACKAGE they live in --
    `apps/growth/constants.py` for a file at `packages/backend/apps/growth/constants.py` --
    and that is the honest way to write them, because the package root is the reader's
    frame. Resolving only from the repo root reports every such path as stale: measured on
    allegro-it-services, 45 of 86 flagged tokens were this one gap, which buried the real
    findings under noise.

    Matched on a whole leading SEGMENT (`/` + the token), never a bare substring, so
    `pps/growth` cannot ride in on `apps/growth`.
    """
    suffix = "/" + normalised.rstrip("/")
    if any(path.endswith(suffix) for path in relative_paths):
        return True
    # A module cited without its extension. `services/graph_auth` is how you refer to a
    # module in prose — nobody writes `services/graph_auth.py` mid-sentence — but the file
    # on disk carries the suffix, so the honest citation failed to resolve.
    return any(path.endswith(suffix + extension) for path in relative_paths for extension in SOURCE_SUFFIXES)


def _inventory_from_git(
    repo_root: Path, config: CheckConfig, tracked: list[str]
) -> tuple[set[str], list[str], set[str], set[str], bool]:
    """The index's answer, which is the authoritative one where git can give it.

    Preferred over the walk because a built or installed tree holds files a checkout does
    not -- resolving a path against those makes the local run pass and the deploy fail.
    """
    filenames: set[str] = set()
    source_texts: list[str] = []
    relative_paths: set[str] = set()
    command_names: set[str] = set()

    for relative in tracked:
        path = repo_root / relative
        filenames.add(path.name)
        relative_paths.add(relative)
        parent = Path(relative).parent
        while parent != Path("."):
            relative_paths.add(parent.as_posix())
            parent = parent.parent
        if (
            path.parent.name == "commands"
            and path.parent.parent.name == "management"
            and path.suffix == ".py"
            and not path.name.startswith("_")
        ):
            command_names.add(path.stem)
        if should_check(path, repo_root, config):
            try:
                source_texts.append(path.read_text(encoding="utf-8", errors="replace"))
            except OSError as error:
                # This one fails LOUD rather than quiet -- a file missing from the corpus makes a
                # symbol defined in it look absent, so docs-stale-symbol over-reports. Named anyway:
                # a finding whose real cause is an unreadable file sends the reader to fix the
                # wrong thing.
                warn_unreadable(path, error, "docs-stale-symbol may report a false finding")
                continue
    return filenames, source_texts, relative_paths, command_names, True


def _inventory_from_walk(repo_root: Path, config: CheckConfig) -> tuple[set[str], list[str], set[str], set[str], bool]:
    """The fallback for a tree git cannot answer for. The final flag says so."""
    filenames: set[str] = set()
    source_texts: list[str] = []
    relative_paths: set[str] = set()
    command_names: set[str] = set()

    for directory, subdirectories, names in walk_repository(repo_root):
        subdirectories[:] = [name for name in subdirectories if name not in SKIP_DIRECTORIES]
        base = Path(directory)
        for name in subdirectories:
            relative_paths.add((base / name).relative_to(repo_root).as_posix())
        # A Django management command's NAME is its filename without the extension —
        # `manage.py seed_reference_data` is `seed_reference_data.py` and the string
        # appears in no file's contents. Documenting one correctly therefore looked like a
        # stale symbol, which is the worst kind of false positive: the natural response is
        # to delete the accurate line. The pack already enumerates exactly these files for
        # docs-uncovered-command, so the information was there and unused.
        #
        # Deliberately narrow. Adding every file's stem would let `utils` resolve any
        # mention of `utils`, which would gut the rule; this is the one convention where
        # the filename IS the public name.
        is_command_directory = base.name == "commands" and base.parent.name == "management"
        for name in names:
            filenames.add(name)
            if is_command_directory and name.endswith(".py") and not name.startswith("_"):
                command_names.add(name[: -len(".py")])
            path = base / name
            relative_paths.add(path.relative_to(repo_root).as_posix())
            if should_check(path, repo_root, config):
                try:
                    source_texts.append(path.read_text(encoding="utf-8", errors="replace"))
                except OSError as error:
                    # This one fails LOUD rather than quiet -- a file missing from the corpus makes a
                    # symbol defined in it look absent, so docs-stale-symbol over-reports. Named anyway:
                    # a finding whose real cause is an unreadable file sends the reader to fix the
                    # wrong thing.
                    warn_unreadable(path, error, "docs-stale-symbol may report a false finding")
                    continue

    return filenames, source_texts, relative_paths, command_names, False


def _repository_inventory(repo_root: Path, config: CheckConfig) -> tuple[set[str], list[str], set[str], set[str], bool]:
    """Every filename, the text of every in-scope source file, and every relative path.

    Built once per run, lazily -- a docs tree with no code-shaped tokens never pays for
    the walk. Filenames come from the full (pruned) tree because a docs page may name any
    real file (`SonarLint.xml`); symbol text comes only from files `should_check` accepts,
    which is the same definition of "the source" every other rule uses. Relative paths
    cover directories as well as files, because docs name a package directory
    (`apps/growth`) as readily as a module inside it.
    """
    tracked = _version_controlled_paths(repo_root)
    if tracked is not None:
        return _inventory_from_git(repo_root, config, tracked)
    return _inventory_from_walk(repo_root, config)
