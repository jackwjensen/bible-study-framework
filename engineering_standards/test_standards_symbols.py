"""docs-stale-symbol: whether a backtick-quoted name is one this repository has.

Its own module because the resolver is its own module, and because the interesting cases
are not "is the name spelled right" but "which tree are we asking". The rule consults git
rather than the filesystem, so a developer with a built, installed tree gets the same
answer as CI's clean checkout — a split there is worse than a strict rule, because the
local run is the one people trust and the deploy is where they find out.
"""

from __future__ import annotations

import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

sys.path.insert(0, str(Path(__file__).resolve().parent))

from standards_core import CheckConfig, Violation  # noqa: E402
from standards_docs import check_documentation  # noqa: E402
from standards_exemptions import MIN_EXEMPTION_REASON_LENGTH  # noqa: E402

FRONT = "---\naudience: dev\ntype: reference\n---\n"

ORPHAN_REASON = "kept unlinked on purpose; reached from the app's in-product help only"
assert len(ORPHAN_REASON) >= MIN_EXEMPTION_REASON_LENGTH, "fixture must clear the reason floor"


@contextmanager
def repo() -> Iterator[Path]:
    with tempfile.TemporaryDirectory() as tree:
        yield Path(tree)


def write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def findings(root: Path, config: CheckConfig | None = None) -> list[Violation]:
    return list(check_documentation(root, config or CheckConfig()))


def rules_fired(root: Path) -> set[str]:
    return {violation.rule for violation in findings(root)}


def quoted(violation: Violation) -> str:
    return violation.message.split("'")[1]


# ---- docs-stale-symbol ---------------------------------------------------------------------

SYMBOL_REASON = "historical name, kept deliberately for the migration story it explains"
assert len(SYMBOL_REASON) >= MIN_EXEMPTION_REASON_LENGTH, "fixture must clear the reason floor"


def test_backtick_identifier_absent_from_source_fires() -> None:
    with repo() as root:
        write(root, "src/app.py", "def send_invoice():\n    return 1\n")
        write(root, "docs/index.md", FRONT + "Call `NoSuchHelperAsync` to start.\n")
        found = [v for v in findings(root) if v.rule == "docs-stale-symbol"]
        assert len(found) == 1 and quoted(found[0]) == "NoSuchHelperAsync"
        assert found[0].line == 5


def test_backtick_identifier_present_in_source_passes() -> None:
    with repo() as root:
        write(root, "src/app.py", "def send_invoice():\n    return 1\n")
        write(root, "docs/index.md", FRONT + "Call `send_invoice` to start.\n")
        assert "docs-stale-symbol" not in rules_fired(root)


def test_lowercase_word_in_backticks_is_ignored() -> None:
    with repo() as root:
        write(root, "src/app.py", "x = 1\n")
        write(root, "docs/index.md", FRONT + "This is `important` and `docker` related.\n")
        assert "docs-stale-symbol" not in rules_fired(root)


def test_all_caps_word_in_backticks_is_ignored() -> None:
    with repo() as root:
        write(root, "src/app.py", "x = 1\n")
        write(root, "docs/index.md", FRONT + "Set `GDPR` mode via `HTTP`.\n")
        assert "docs-stale-symbol" not in rules_fired(root)


def test_fenced_code_blocks_are_ignored_for_symbols() -> None:
    with repo() as root:
        write(root, "src/app.py", "x = 1\n")
        write(root, "docs/index.md", FRONT + "```python\nInventedExample()\n```\n")
        assert "docs-stale-symbol" not in rules_fired(root)


def test_path_like_token_missing_fires() -> None:
    with repo() as root:
        write(root, "docs/index.md", FRONT + "Run `scripts/nothere.py` first.\n")
        found = [v for v in findings(root) if v.rule == "docs-stale-symbol"]
        assert len(found) == 1 and quoted(found[0]) == "scripts/nothere.py"


def test_path_like_token_existing_passes() -> None:
    with repo() as root:
        write(root, "scripts/here.py", "x = 1\n")
        write(root, "docs/index.md", FRONT + "Run `scripts/here.py` first.\n")
        assert "docs-stale-symbol" not in rules_fired(root)


def test_path_relative_to_a_package_root_passes() -> None:
    """A monorepo's docs write source paths relative to the PACKAGE, not the repo root --
    `apps/growth/constants.py` for a file at `packages/backend/apps/growth/constants.py`.
    Resolving only from the repo root reports every one of them as stale."""
    with repo() as root:
        write(root, "packages/backend/apps/growth/constants.py", "OPEN = False\n")
        write(root, "docs/index.md", FRONT + "The switch is in `apps/growth/constants.py`.\n")
        assert "docs-stale-symbol" not in rules_fired(root)


def test_package_relative_directory_passes() -> None:
    """The same relief applies to a directory, not just a file."""
    with repo() as root:
        write(root, "packages/backend/apps/growth/constants.py", "OPEN = False\n")
        write(root, "docs/index.md", FRONT + "Attribution lives in `apps/growth`.\n")
        assert "docs-stale-symbol" not in rules_fired(root)


def test_partial_segment_match_does_not_rescue_a_stale_path() -> None:
    """The relief is whole path SEGMENTS. `pps/growth` is not a suffix of
    `apps/growth` in any sense a reader would accept, so it must still fire."""
    with repo() as root:
        write(root, "packages/backend/apps/growth/constants.py", "OPEN = False\n")
        write(root, "docs/index.md", FRONT + "Broken ref to `pps/growth/constants.py`.\n")
        found = [v for v in findings(root) if v.rule == "docs-stale-symbol"]
        assert len(found) == 1 and quoted(found[0]) == "pps/growth/constants.py"


def test_filename_named_in_source_strings_passes() -> None:
    """A marker file of ANOTHER stack (`manage.py` in a pack with no Django) is a name the
    source itself uses; docs repeating it are correct, not stale."""
    with repo() as root:
        write(root, "scripts/detect.py", 'MARKER = "manage.py"\n')
        write(root, "docs/index.md", FRONT + "Django is detected by `manage.py`.\n")
        assert "docs-stale-symbol" not in rules_fired(root)


def test_symbol_line_exemption_suppresses() -> None:
    with repo() as root:
        write(root, "src/app.py", "x = 1\n")
        write(
            root,
            "docs/index.md",
            FRONT
            + f"<!-- standards: docs-stale-symbol exempt -- {SYMBOL_REASON} -->\n"
            + "The old `RetiredHelper` did this.\n",
        )
        assert "docs-stale-symbol" not in rules_fired(root)


def test_a_migration_cited_by_its_class_name_passes() -> None:
    """An EF Core migration is named by its class -- `RegistrationsShowTheirOwnPrice` -- and
    that name lives only in its own file, which the source corpus skips as generated code.
    Measured on InvoTrack (2026-11-17): eight accurately cited migrations read as stale."""
    with repo() as root:
        write(
            root,
            "App/Migrations/20261030215207_RegistrationsShowTheirOwnPrice.cs",
            "public partial class RegistrationsShowTheirOwnPrice : Migration { }\n",
        )
        write(root, "docs/index.md", FRONT + "Fixed by `RegistrationsShowTheirOwnPrice`.\n")
        assert "docs-stale-symbol" not in rules_fired(root)


def test_a_migration_name_with_no_migration_file_still_fires() -> None:
    """A squashed or deleted migration is a stale name like any other."""
    with repo() as root:
        write(root, "App/Migrations/20261030215207_RegistrationsShowTheirOwnPrice.cs", "x\n")
        write(root, "docs/index.md", FRONT + "Added by `AddEntityLastUsedAt`.\n")
        found = [v for v in findings(root) if v.rule == "docs-stale-symbol"]
        assert [quoted(v) for v in found] == ["AddEntityLastUsedAt"]


def test_a_member_cited_on_its_type_passes() -> None:
    """`Registration.Kind` names a member on a type. A short member reads like a file
    extension, so the token was judged as a filename -- and a filename it is not."""
    with repo() as root:
        write(root, "App/Entities/Registration.cs", "public RegistrationKind Kind { get; set; }\n")
        write(root, "docs/index.md", FRONT + "Dispatch on `Registration.Kind`.\n")
        assert "docs-stale-symbol" not in rules_fired(root)


def test_a_member_the_type_no_longer_declares_fires() -> None:
    """The relief is the type's own file declaring the member as a whole word, so a rename
    on the type is still caught."""
    with repo() as root:
        write(root, "App/Entities/Registration.cs", "public RegistrationKind KindCode { get; set; }\n")
        write(root, "docs/index.md", FRONT + "Dispatch on `Registration.Kind`.\n")
        found = [v for v in findings(root) if v.rule == "docs-stale-symbol"]
        assert [quoted(v) for v in found] == ["Registration.Kind"]


def test_symbols_only_checked_in_docs_tree() -> None:
    with repo() as root:
        write(root, "docs/index.md", FRONT + "# Map\n")
        write(root, "README.md", "Mentions `PreToolUse` and `SomeToolName` freely.\n")
        assert "docs-stale-symbol" not in rules_fired(root)


# ------------------------------------------- resolution must not depend on a built tree


def _git_repo(root: Path) -> Path:
    """A real repository, because the resolver asks git what the tree contains."""
    import subprocess  # noqa: PLC0415  (local by design: imported after this test builds its tree)

    (root / "docs").mkdir(parents=True, exist_ok=True)
    (root / ".gitignore").write_text("build/\n", encoding="utf-8")
    for command in (["init"], ["add", "-A"]):
        subprocess.run(["git", *command], cwd=root, capture_output=True, check=False)
    return root


def test_a_gitignored_build_artifact_does_not_resolve(tmp_path: Path) -> None:
    """The failure this exists to prevent: `packages/webapp/build` is real on a machine
    that has run a build and absent from a clean checkout, so documenting it passed
    locally and failed the deploy. The local run is the one people trust, which is what
    makes a split answer worse than a strict one."""
    repo = _git_repo(tmp_path)
    (repo / "build").mkdir()
    (repo / "build" / "app.js").write_text("//\n", encoding="utf-8")
    (repo / "docs" / "index.md").write_text(
        "---\naudience: dev\ntype: reference\n---\n\n# Index\n\nServed from `build/app.js`.\n",
        encoding="utf-8",
    )

    found = [v.rule for v in findings(repo)]

    assert "docs-stale-symbol" in found


def test_a_file_added_but_not_yet_committed_still_resolves(tmp_path: Path) -> None:
    """The other direction, and why `--others --exclude-standard` is the right query: a
    file created in the same commit as the docs describing it must not read as stale."""
    repo = _git_repo(tmp_path)
    (repo / "brand_new.py").write_text("x = 1\n", encoding="utf-8")
    (repo / "docs" / "index.md").write_text(
        "---\naudience: dev\ntype: reference\n---\n\n# Index\n\nSee `brand_new.py`.\n",
        encoding="utf-8",
    )

    found = [v.rule for v in findings(repo)]

    assert "docs-stale-symbol" not in found
