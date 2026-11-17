"""The documentation dimension's mechanical rules: structure, frontmatter, links, orphans.

Each case builds a throwaway repo tree and runs `check_documentation` over it directly --
the repo-level entry point that check-source-limits.py calls on whole-tree scans. The CLI
integration (baseline ratchet, gate refusal) is covered by test_hook_blocks.py; these cases
own the rules themselves.

Run: python test_standards_docs.py   (or pytest)
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
from standards_selftest import run_module_tests  # noqa: E402

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


# ---- docs-missing --------------------------------------------------------------------------


def test_repo_without_docs_index_reports_docs_missing() -> None:
    with repo() as root:
        write(root, "README.md", "# x\n")
        found = findings(root)
        assert [violation.rule for violation in found] == ["docs-missing"]
        assert quoted(found[0]) == "docs/index.md"


def test_repo_with_docs_index_reports_no_docs_missing() -> None:
    with repo() as root:
        write(root, "docs/index.md", FRONT + "# Map\n")
        assert "docs-missing" not in rules_fired(root)


def test_check_docs_false_disables_everything() -> None:
    with repo() as root:
        write(root, "docs/orphan.md", "no frontmatter, unreachable, [broken](gone.md)\n")
        assert findings(root, CheckConfig(check_docs=False)) == []


# ---- docs-frontmatter ----------------------------------------------------------------------


def test_page_without_frontmatter_block_fires_for_both_required_keys() -> None:
    with repo() as root:
        write(root, "docs/index.md", FRONT + "[setup](setup.md)\n")
        write(root, "docs/setup.md", "# Setup\n")
        keys = {quoted(v) for v in findings(root) if v.rule == "docs-frontmatter"}
        assert keys == {"audience", "type"}


def test_invalid_audience_value_fires() -> None:
    with repo() as root:
        write(root, "docs/index.md", "---\naudience: users\ntype: reference\n---\n")
        found = [v for v in findings(root) if v.rule == "docs-frontmatter"]
        assert len(found) == 1 and quoted(found[0]) == "audience"


def test_invalid_type_value_fires() -> None:
    with repo() as root:
        write(root, "docs/index.md", "---\naudience: dev\ntype: guide\n---\n")
        found = [v for v in findings(root) if v.rule == "docs-frontmatter"]
        assert len(found) == 1 and quoted(found[0]) == "type"


def test_valid_frontmatter_passes() -> None:
    with repo() as root:
        write(root, "docs/index.md", "---\naudience: user\ntype: how-to\naccess: customer\n---\n")
        assert "docs-frontmatter" not in rules_fired(root)


def test_invalid_access_value_fires() -> None:
    with repo() as root:
        write(root, "docs/index.md", "---\naudience: dev\ntype: reference\naccess: internal\n---\n")
        found = [v for v in findings(root) if v.rule == "docs-frontmatter"]
        assert len(found) == 1 and quoted(found[0]) == "access"


def test_frontmatter_value_comment_is_ignored() -> None:
    with repo() as root:
        write(root, "docs/index.md", "---\naudience: dev   # dev | user\ntype: reference\n---\n")
        assert "docs-frontmatter" not in rules_fired(root)


def test_frontmatter_after_a_byte_order_mark_is_still_frontmatter() -> None:
    """A BOM left in line 1 reads `\\ufeff---`, which is not a fence, so the whole block
    vanished and both required keys were reported missing from a page that has them."""
    with repo() as root:
        write(root, "docs/index.md", "﻿---\naudience: dev\ntype: reference\n---\n")
        assert "docs-frontmatter" not in rules_fired(root)


# ---- docs-broken-link ----------------------------------------------------------------------


def test_relative_link_to_missing_file_fires() -> None:
    with repo() as root:
        write(root, "docs/index.md", FRONT + "[gone](guides/nothere.md)\n")
        found = [v for v in findings(root) if v.rule == "docs-broken-link"]
        assert len(found) == 1 and quoted(found[0]) == "guides/nothere.md"
        assert found[0].line == 5


def test_link_with_fragment_checks_only_the_file() -> None:
    with repo() as root:
        write(root, "docs/index.md", FRONT + "[setup](setup.md#step-2)\n")
        write(root, "docs/setup.md", FRONT + "# Setup\n")
        assert "docs-broken-link" not in rules_fired(root)


def test_absolute_and_anchor_links_are_skipped() -> None:
    with repo() as root:
        write(
            root,
            "docs/index.md",
            FRONT + "[a](https://example.com/x.md) [b](mailto:x@y.dk) [c](#local)\n",
        )
        assert "docs-broken-link" not in rules_fired(root)


def test_reference_style_definition_is_checked() -> None:
    with repo() as root:
        write(root, "docs/index.md", FRONT + "see [x]\n\n[x]: missing.md\n")
        found = [v for v in findings(root) if v.rule == "docs-broken-link"]
        assert len(found) == 1 and quoted(found[0]) == "missing.md"


def test_readme_links_are_checked_too() -> None:
    with repo() as root:
        write(root, "docs/index.md", FRONT + "# Map\n")
        write(root, "README.md", "[docs](docs/nothere.md)\n")
        found = [v for v in findings(root) if v.rule == "docs-broken-link"]
        assert len(found) == 1 and quoted(found[0]) == "docs/nothere.md"


def test_links_inside_fenced_code_blocks_are_ignored() -> None:
    with repo() as root:
        write(root, "docs/index.md", FRONT + "```markdown\n[example](nothere.md)\n```\n")
        assert "docs-broken-link" not in rules_fired(root)


def test_links_inside_inline_code_spans_are_ignored() -> None:
    """Prose DOCUMENTING link syntax is not a link. A fenced block is already skipped, but
    the one-line form is written inline -- and since this rule ships with no exemption, a
    false positive here can only be cleared by rewording correct prose."""
    with repo() as root:
        write(root, "docs/index.md", FRONT + "Teams renders markdown `[label](url)` here.\n")
        assert "docs-broken-link" not in rules_fired(root)


def test_link_whose_TEXT_is_a_code_span_is_still_checked() -> None:
    """`[`page.md`](./page.md)` is a real link with code in its label, and the estate's
    plans are written that way -- stripping spans must not blind the rule to it."""
    with repo() as root:
        write(root, "docs/index.md", FRONT + "See [`gone.md`](./gone.md) for more.\n")
        found = [v for v in findings(root) if v.rule == "docs-broken-link"]
        assert len(found) == 1 and quoted(found[0]) == "./gone.md"


def test_a_real_link_beside_an_inline_code_span_still_fires() -> None:
    """Stripping spans must not swallow the rest of the line."""
    with repo() as root:
        write(root, "docs/index.md", FRONT + "Syntax `[a](b)` -- see [map](nothere.md).\n")
        found = [v for v in findings(root) if v.rule == "docs-broken-link"]
        assert len(found) == 1 and quoted(found[0]) == "nothere.md"


# ---- docs-orphan-page ----------------------------------------------------------------------


def test_page_reachable_from_index_passes() -> None:
    with repo() as root:
        write(root, "docs/index.md", FRONT + "[setup](setup.md)\n")
        write(root, "docs/setup.md", FRONT + "# Setup\n")
        assert "docs-orphan-page" not in rules_fired(root)


def test_page_reachable_transitively_passes() -> None:
    with repo() as root:
        write(root, "docs/index.md", FRONT + "[a](a.md)\n")
        write(root, "docs/a.md", FRONT + "[b](b.md)\n")
        write(root, "docs/b.md", FRONT + "# B\n")
        assert "docs-orphan-page" not in rules_fired(root)


def test_unreachable_page_fires_quoting_its_own_name() -> None:
    with repo() as root:
        write(root, "docs/index.md", FRONT + "# Map\n")
        write(root, "docs/lost.md", FRONT + "# Lost\n")
        found = [v for v in findings(root) if v.rule == "docs-orphan-page"]
        assert len(found) == 1 and quoted(found[0]) == "lost.md"


def test_page_linked_only_from_readme_passes() -> None:
    with repo() as root:
        write(root, "docs/index.md", FRONT + "# Map\n")
        write(root, "docs/notes.md", FRONT + "# Notes\n")
        write(root, "README.md", "[notes](docs/notes.md)\n")
        assert "docs-orphan-page" not in rules_fired(root)


def test_a_link_from_an_orphan_does_not_rescue_its_target() -> None:
    with repo() as root:
        write(root, "docs/index.md", FRONT + "# Map\n")
        write(root, "docs/lost.md", FRONT + "[also-lost](also-lost.md)\n")
        write(root, "docs/also-lost.md", FRONT + "# Also\n")
        found = {quoted(v) for v in findings(root) if v.rule == "docs-orphan-page"}
        assert found == {"lost.md", "also-lost.md"}


def test_orphan_exemption_marker_suppresses() -> None:
    with repo() as root:
        write(root, "docs/index.md", FRONT + "# Map\n")
        write(
            root,
            "docs/help.md",
            FRONT + f"<!-- standards: docs-orphan-page exempt -- {ORPHAN_REASON} -->\n# Help\n",
        )
        assert "docs-orphan-page" not in rules_fired(root)


# ---- claude-md-length ----------------------------------------------------------------------

LENGTH_REASON = "estate-wide constitution carrying rules no docs tree exists for yet"
assert len(LENGTH_REASON) >= MIN_EXEMPTION_REASON_LENGTH, "fixture must clear the reason floor"


def claude_md_of(line_count: int, header: str = "") -> str:
    filler = [f"- rule {i}" for i in range(line_count - (1 if header else 0))]
    return "\n".join(([header] if header else []) + filler) + "\n"


def test_claude_md_over_threshold_fires() -> None:
    with repo() as root:
        write(root, "docs/index.md", FRONT + "# Map\n")
        write(root, "CLAUDE.md", claude_md_of(151))
        found = [v for v in findings(root) if v.rule == "claude-md-length"]
        assert len(found) == 1 and quoted(found[0]) == "CLAUDE.md"


def test_claude_md_at_threshold_passes() -> None:
    with repo() as root:
        write(root, "docs/index.md", FRONT + "# Map\n")
        write(root, "CLAUDE.md", claude_md_of(150))
        assert "claude-md-length" not in rules_fired(root)


def test_missing_claude_md_passes() -> None:
    with repo() as root:
        write(root, "docs/index.md", FRONT + "# Map\n")
        assert "claude-md-length" not in rules_fired(root)


def test_claude_md_file_scoped_exemption_suppresses() -> None:
    with repo() as root:
        write(root, "docs/index.md", FRONT + "# Map\n")
        write(
            root,
            "CLAUDE.md",
            claude_md_of(151, f"<!-- standards: claude-md-length exempt -- {LENGTH_REASON} -->"),
        )
        assert "claude-md-length" not in rules_fired(root)


def test_claude_md_threshold_is_configurable() -> None:
    with repo() as root:
        write(root, "docs/index.md", FRONT + "# Map\n")
        write(root, "CLAUDE.md", claude_md_of(80))
        assert "claude-md-length" in {v.rule for v in findings(root, CheckConfig(claude_md_max_lines=50))}


# ---- docs-plan-page ------------------------------------------------------------------------

PLAN_REASON = "reusable release checklist, not a one-shot plan; re-run for every release"
assert len(PLAN_REASON) >= MIN_EXEMPTION_REASON_LENGTH, "fixture must clear the reason floor"


def plan_findings(root: Path) -> list[Violation]:
    return [v for v in findings(root) if v.rule == "docs-plan-page"]


def test_checkboxed_plan_titled_page_in_docs_fires() -> None:
    with repo() as root:
        write(
            root,
            "docs/design/feature.md",
            FRONT + "# Feature X — Implementation Plan\n\n- [ ] Step 1\n- [x] Step 2\n",
        )
        found = plan_findings(root)
        assert len(found) == 1 and quoted(found[0]) == "docs/design/feature.md"


def test_page_under_a_plans_directory_fires_even_without_checkboxes() -> None:
    with repo() as root:
        write(root, "docs/superpowers/plans/feature.md", FRONT + "# Feature\n\nProse only.\n")
        assert len(plan_findings(root)) == 1


def test_plan_word_in_filename_with_checkboxes_fires() -> None:
    with repo() as root:
        write(root, "docs/migration-plan.md", FRONT + "# Migrating\n\n- [ ] Step 1\n")
        assert len(plan_findings(root)) == 1


def test_reusable_checklist_without_plan_identity_passes() -> None:
    with repo() as root:
        write(
            root,
            "docs/deploy.md",
            FRONT + "# Deploying\n\n- [ ] DNS points at the box\n- [ ] TLS terminates\n",
        )
        assert plan_findings(root) == []


def test_prose_about_a_plan_without_checkboxes_passes() -> None:
    with repo() as root:
        write(
            root,
            "docs/why-the-plan.md",
            FRONT + "# Why the rollout plan looks like this\n\nRationale prose.\n",
        )
        assert plan_findings(root) == []


def test_checkbox_inside_a_code_fence_does_not_count() -> None:
    with repo() as root:
        write(
            root,
            "docs/writing-a-plan.md",
            FRONT + "# Writing a plan\n\n```markdown\n- [ ] a step template\n```\n",
        )
        assert plan_findings(root) == []


def test_plan_page_file_exemption_is_honoured() -> None:
    with repo() as root:
        write(
            root,
            "docs/release-plan.md",
            "---\naudience: dev\ntype: how-to\n---\n"
            f"<!-- standards: docs-plan-page exempt -- {PLAN_REASON} -->\n"
            "# Release plan\n\n- [ ] Tag\n- [ ] Push\n",
        )
        assert plan_findings(root) == []


if __name__ == "__main__":
    sys.exit(run_module_tests(globals(), "documentation rule cases"))
