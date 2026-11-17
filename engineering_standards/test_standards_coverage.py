"""The documentation-coverage rules: elements that ship with no documentation at all.

Every other documentation rule audits the docs that exist; these audit the gap — an
enumerable surface (a management command, an .env.example key, a declared public route)
that no documentation names. Each case builds a throwaway repo tree and runs
`check_coverage` directly, mirroring test_standards_docs.py.

Run: python test_standards_coverage.py   (or pytest)
"""

from __future__ import annotations

import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

sys.path.insert(0, str(Path(__file__).resolve().parent))

from standards_core import CheckConfig, Violation  # noqa: E402
from standards_coverage import check_coverage  # noqa: E402
from standards_exemptions import MIN_EXEMPTION_REASON_LENGTH  # noqa: E402
from standards_selftest import run_module_tests  # noqa: E402

FRONT = "---\naudience: dev\ntype: reference\n---\n"

EXEMPT_REASON = "operator-only backfill; runbook lives in the ops wiki, not this repo"
assert len(EXEMPT_REASON) >= MIN_EXEMPTION_REASON_LENGTH, "fixture must clear the reason floor"


@contextmanager
def repo() -> Iterator[Path]:
    with tempfile.TemporaryDirectory() as tree:
        yield Path(tree)


def write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def findings(root: Path, config: CheckConfig | None = None) -> list[Violation]:
    return list(check_coverage(root, config or CheckConfig()))


def rules_fired(root: Path, config: CheckConfig | None = None) -> set[str]:
    return {violation.rule for violation in findings(root, config)}


def quoted(violation: Violation) -> str:
    return violation.message.split("'")[1]


COMMAND = "apps/things/management/commands/seed_things.py"


# ---- docs-uncovered-command ------------------------------------------------------------------


def test_undocumented_command_fires_quoting_its_name() -> None:
    with repo() as root:
        write(root, COMMAND, "class Command:\n    pass\n")
        write(root, "docs/index.md", FRONT + "# Map\n")
        found = [v for v in findings(root) if v.rule == "docs-uncovered-command"]
        assert len(found) == 1 and quoted(found[0]) == "seed_things"


def test_command_named_in_a_docs_page_passes() -> None:
    with repo() as root:
        write(root, COMMAND, "class Command:\n    pass\n")
        write(root, "docs/index.md", FRONT + "Run `seed_things` after a fresh migrate.\n")
        assert "docs-uncovered-command" not in rules_fired(root)


def test_command_named_in_claude_md_passes() -> None:
    with repo() as root:
        write(root, COMMAND, "class Command:\n    pass\n")
        write(root, "CLAUDE.md", "Seed with `manage.py seed_things` before testing.\n")
        assert "docs-uncovered-command" not in rules_fired(root)


def test_command_named_in_readme_passes() -> None:
    with repo() as root:
        write(root, COMMAND, "class Command:\n    pass\n")
        write(root, "README.md", "Setup: run seed_things once.\n")
        assert "docs-uncovered-command" not in rules_fired(root)


def test_dunder_and_private_command_files_are_ignored() -> None:
    with repo() as root:
        write(root, "apps/things/management/commands/__init__.py", "")
        write(root, "apps/things/management/commands/_shared.py", "HELP = 1\n")
        assert "docs-uncovered-command" not in rules_fired(root)


def test_command_file_scoped_exemption_suppresses() -> None:
    with repo() as root:
        write(
            root,
            COMMAND,
            f'"""standards: docs-uncovered-command exempt -- {EXEMPT_REASON}"""\nclass Command:\n    pass\n',
        )
        assert "docs-uncovered-command" not in rules_fired(root)


def test_command_in_skipped_directory_is_ignored() -> None:
    with repo() as root:
        write(root, "node_modules/pkg/management/commands/ghost.py", "class Command: ...\n")
        assert "docs-uncovered-command" not in rules_fired(root)


# ---- docs-uncovered-env ------------------------------------------------------------------------


def test_bare_env_key_fires_quoting_the_key() -> None:
    with repo() as root:
        write(root, ".env.example", "MYSTERY_TOKEN=\n")
        found = [v for v in findings(root) if v.rule == "docs-uncovered-env"]
        assert len(found) == 1 and quoted(found[0]) == "MYSTERY_TOKEN"


def test_env_key_with_comment_above_is_self_documented() -> None:
    with repo() as root:
        write(root, ".env.example", "# The webhook signing secret from `stripe listen`.\nSTRIPE_WEBHOOK_SECRET=\n")
        assert "docs-uncovered-env" not in rules_fired(root)


def test_env_key_with_trailing_comment_is_self_documented() -> None:
    with repo() as root:
        write(root, ".env.example", "DEBUG=on  # dev only; prod sets off\n")
        assert "docs-uncovered-env" not in rules_fired(root)


def test_env_key_named_in_docs_passes() -> None:
    with repo() as root:
        write(root, ".env.example", "WEBHOOK_BASE_URL=\n")
        write(root, "docs/index.md", FRONT + "Set `WEBHOOK_BASE_URL` to the public origin.\n")
        assert "docs-uncovered-env" not in rules_fired(root)


def test_comment_headed_key_block_is_self_documented_throughout() -> None:
    """A section comment documents its whole contiguous block, not just the first key —
    measured on allegro-it-services, where an 8-line comment explaining the GOOGLE_ADS_*
    section credited only the first of ten keys and fired on the other nine."""
    with repo() as root:
        write(
            root,
            ".env.example",
            "# Google Ads uploader: mint a datamanager-scoped refresh token, then\n"
            "# create the two conversion actions and put their ids below.\n"
            "ADS_ENABLED=False\nADS_CLIENT_ID=\nADS_CLIENT_SECRET=\n",
        )
        assert "docs-uncovered-env" not in rules_fired(root)


def test_blank_line_ends_a_comment_headed_block() -> None:
    """A blank line is a section boundary — a key after one starts undocumented again."""
    with repo() as root:
        write(
            root,
            ".env.example",
            "# The ads section, fully explained here.\nADS_ENABLED=False\n\nORPHAN_KEY=\n",
        )
        found = [v for v in findings(root) if v.rule == "docs-uncovered-env"]
        assert len(found) == 1 and quoted(found[0]) == "ORPHAN_KEY"


def test_env_blank_lines_and_comments_are_not_keys() -> None:
    with repo() as root:
        write(root, ".env.example", "\n# section header\n\n")
        assert "docs-uncovered-env" not in rules_fired(root)


def test_repo_without_env_example_is_silent() -> None:
    with repo() as root:
        write(root, "docs/index.md", FRONT + "# Map\n")
        assert "docs-uncovered-env" not in rules_fired(root)


# ---- docs-uncovered-route ----------------------------------------------------------------------


ROUTE_CONFIG = CheckConfig(docs_route_inventories=(("scripts/pages.mjs", r"path:\s*'([^']+)'"),))


def test_route_in_inventory_but_not_in_docs_fires() -> None:
    with repo() as root:
        write(root, "scripts/pages.mjs", "const PAGES = [\n  { path: '/en/pricing' },\n];\n")
        write(root, "docs/index.md", FRONT + "# Map\n")
        found = [v for v in findings(root, ROUTE_CONFIG) if v.rule == "docs-uncovered-route"]
        assert len(found) == 1 and quoted(found[0]) == "/en/pricing"


def test_route_named_in_docs_passes() -> None:
    with repo() as root:
        write(root, "scripts/pages.mjs", "const PAGES = [\n  { path: '/en/pricing' },\n];\n")
        write(root, "docs/index.md", FRONT + "Pricing lives at `/en/pricing`.\n")
        assert "docs-uncovered-route" not in rules_fired(root, ROUTE_CONFIG)


def test_route_line_exemption_suppresses() -> None:
    with repo() as root:
        write(
            root,
            "scripts/pages.mjs",
            "const PAGES = [\n"
            f"  // standards: docs-uncovered-route exempt -- {EXEMPT_REASON}\n"
            "  { path: '/en/pricing' },\n"
            "];\n",
        )
        assert "docs-uncovered-route" not in rules_fired(root, ROUTE_CONFIG)


def test_missing_inventory_file_is_silent() -> None:
    with repo() as root:
        write(root, "docs/index.md", FRONT + "# Map\n")
        assert "docs-uncovered-route" not in rules_fired(root, ROUTE_CONFIG)


def test_no_route_config_means_no_route_checking() -> None:
    with repo() as root:
        write(root, "scripts/pages.mjs", "const PAGES = [\n  { path: '/en/pricing' },\n];\n")
        assert "docs-uncovered-route" not in rules_fired(root)


# ---- the shared off-switch ---------------------------------------------------------------------


def test_check_docs_false_disables_all_coverage() -> None:
    with repo() as root:
        write(root, COMMAND, "class Command:\n    pass\n")
        write(root, ".env.example", "MYSTERY_TOKEN=\n")
        assert findings(root, CheckConfig(check_docs=False)) == []


if __name__ == "__main__":
    sys.exit(run_module_tests(globals(), "documentation coverage cases"))
