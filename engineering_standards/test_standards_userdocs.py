"""The user-docs rules: the in-product documentation system, enforced as a contract.

Each case builds a throwaway repo tree carrying a miniature of InvoTrack's shape -- a
registry file enumerating topics, pages carrying help links, routes declared per page --
and runs `check_userdocs` (whole-tree rules) or `unsynced_change_notes` (the commit-stage
nudge) directly, mirroring test_standards_coverage.py.

Run: python test_standards_userdocs.py   (or pytest)
"""

from __future__ import annotations

import json
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

sys.path.insert(0, str(Path(__file__).resolve().parent))

from standards_core import CheckConfig, UserDocsConfig, Violation  # noqa: E402
from standards_exemptions import MIN_EXEMPTION_REASON_LENGTH  # noqa: E402
from standards_selftest import run_module_tests  # noqa: E402
from standards_userdocs import check_userdocs, unsynced_change_notes  # noqa: E402

EXEMPT_REASON = "public marketing page; user docs would only restate the page itself"
assert len(EXEMPT_REASON) >= MIN_EXEMPTION_REASON_LENGTH, "fixture must clear the reason floor"

# A miniature of the estate's reference shape (InvoTrack): a C# registry whose entries
# carry slug and route, Razor pages that declare a route and link a topic by slug.
REGISTRY_FILE = "App/Docs/DocRegistry.cs"
REGISTRY_PATTERN = r"new\(\"(?P<slug>[a-z-]+)\", \"[^\"]*\", \"(?P<route>/[^\"]*)\""
HELP_PATTERN = r"HelpSlug=\"(?P<slug>[a-z-]+)\""
ROUTE_PATTERN = r"@page \"(?P<route>/[^\"]+)\""

CONFIG = UserDocsConfig(
    registry_file=REGISTRY_FILE,
    registry_pattern=REGISTRY_PATTERN,
    help_links=(("App/Pages/*.razor", HELP_PATTERN),),
    routes=(("App/Pages/*.razor", ROUTE_PATTERN),),
    content_glob="App/Docs/Content/*.razor",
)


@contextmanager
def repo() -> Iterator[Path]:
    with tempfile.TemporaryDirectory() as tree:
        yield Path(tree)


def write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def registry_entry(slug: str, route: str) -> str:
    return f'        new("{slug}", "Title", "{route}", Groups.X, typeof(Doc)),\n'


def write_registry(root: Path, *entries: tuple[str, str]) -> None:
    body = "".join(registry_entry(slug, route) for slug, route in entries)
    write(root, REGISTRY_FILE, "public static class DocRegistry\n{\n" + body + "}\n")


def page(route: str, slug: str | None = None) -> str:
    help_line = f'<PageHeader Title="T" HelpSlug="{slug}" />\n' if slug else ""
    return f'@page "{route}"\n{help_line}<h1>Page</h1>\n'


def config_with(user_docs: UserDocsConfig | None) -> CheckConfig:
    return CheckConfig(user_docs=user_docs)


def findings(root: Path, user_docs: UserDocsConfig | None = CONFIG) -> list[Violation]:
    return list(check_userdocs(root, config_with(user_docs)))


def rules_fired(root: Path, user_docs: UserDocsConfig | None = CONFIG) -> set[str]:
    return {violation.rule for violation in findings(root, user_docs)}


def quoted(violation: Violation) -> str:
    return violation.message.split("'")[1]


# ---- no config: the whole family is silent ---------------------------------------------------


def test_without_userdocs_config_nothing_fires() -> None:
    with repo() as root:
        write(root, "App/Pages/Invoices.razor", page("/invoices"))
        assert findings(root, user_docs=None) == []


# ---- userdocs-missing ------------------------------------------------------------------------


def test_declared_registry_that_does_not_exist_fires() -> None:
    with repo() as root:
        write(root, "App/Pages/Invoices.razor", page("/invoices", "fakturaer"))
        found = [v for v in findings(root) if v.rule == "userdocs-missing"]
        assert len(found) == 1 and quoted(found[0]) == REGISTRY_FILE


def test_registry_whose_pattern_matches_nothing_fires() -> None:
    with repo() as root:
        write(root, REGISTRY_FILE, "public static class DocRegistry { }\n")
        assert "userdocs-missing" in rules_fired(root)


def test_registry_with_entries_passes() -> None:
    with repo() as root:
        write_registry(root, ("fakturaer", "/invoices"))
        write(root, "App/Pages/Invoices.razor", page("/invoices", "fakturaer"))
        assert "userdocs-missing" not in rules_fired(root)


# ---- userdocs-dangling-slug ------------------------------------------------------------------


def test_help_link_to_an_unregistered_slug_fires_quoting_it() -> None:
    with repo() as root:
        write_registry(root, ("fakturaer", "/invoices"))
        write(root, "App/Pages/Invoices.razor", page("/invoices", "fakturarer"))
        found = [v for v in findings(root) if v.rule == "userdocs-dangling-slug"]
        assert len(found) == 1 and quoted(found[0]) == "fakturarer"


def test_help_link_to_a_registered_slug_passes() -> None:
    with repo() as root:
        write_registry(root, ("fakturaer", "/invoices"))
        write(root, "App/Pages/Invoices.razor", page("/invoices", "fakturaer"))
        assert "userdocs-dangling-slug" not in rules_fired(root)


def test_dangling_slug_line_exemption_is_honoured() -> None:
    with repo() as root:
        write_registry(root, ("fakturaer", "/invoices"))
        write(
            root,
            "App/Pages/Invoices.razor",
            '@page "/invoices"\n'
            f"@* standards: userdocs-dangling-slug exempt -- {EXEMPT_REASON} *@\n"
            '<PageHeader HelpSlug="coming-soon" />\n',
        )
        assert "userdocs-dangling-slug" not in rules_fired(root)


# ---- userdocs-unlinked-page ------------------------------------------------------------------


def test_routed_page_with_no_help_link_fires_quoting_its_route() -> None:
    with repo() as root:
        write_registry(root, ("fakturaer", "/invoices"))
        write(root, "App/Pages/Payslips.razor", page("/payslips"))
        found = [v for v in findings(root) if v.rule == "userdocs-unlinked-page"]
        assert len(found) == 1 and quoted(found[0]) == "/payslips"


def test_routed_page_with_a_help_link_passes() -> None:
    with repo() as root:
        write_registry(root, ("fakturaer", "/invoices"))
        write(root, "App/Pages/Invoices.razor", page("/invoices", "fakturaer"))
        assert "userdocs-unlinked-page" not in rules_fired(root)


def test_file_in_scope_without_a_route_is_not_a_page_and_passes() -> None:
    with repo() as root:
        write_registry(root, ("fakturaer", "/invoices"))
        write(root, "App/Pages/InvoiceCard.razor", "<div>partial, no @page</div>\n")
        assert "userdocs-unlinked-page" not in rules_fired(root)


def test_unlinked_page_file_exemption_is_honoured() -> None:
    with repo() as root:
        write_registry(root, ("fakturaer", "/invoices"))
        write(
            root,
            "App/Pages/Landing.razor",
            f"@* standards: userdocs-unlinked-page exempt -- {EXEMPT_REASON} *@\n" + page("/"),
        )
        assert "userdocs-unlinked-page" not in rules_fired(root)


# ---- userdocs-orphan-topic -------------------------------------------------------------------


def test_registry_route_no_page_declares_fires_quoting_the_slug() -> None:
    with repo() as root:
        write_registry(root, ("fakturaer", "/invoices"), ("gamle-ting", "/removed-feature"))
        write(root, "App/Pages/Invoices.razor", page("/invoices", "fakturaer"))
        found = [v for v in findings(root) if v.rule == "userdocs-orphan-topic"]
        assert len(found) == 1 and quoted(found[0]) == "gamle-ting"


def test_registry_routes_all_declared_pass() -> None:
    with repo() as root:
        write_registry(root, ("fakturaer", "/invoices"), ("indstillinger", "/settings"))
        write(root, "App/Pages/Invoices.razor", page("/invoices", "fakturaer"))
        write(root, "App/Pages/Settings.razor", page("/settings", "indstillinger"))
        assert "userdocs-orphan-topic" not in rules_fired(root)


def test_registry_pattern_without_a_route_group_skips_orphan_checking() -> None:
    slugs_only = UserDocsConfig(
        registry_file=REGISTRY_FILE,
        registry_pattern=r"new\(\"(?P<slug>[a-z-]+)\"",
        help_links=CONFIG.help_links,
        routes=CONFIG.routes,
        content_glob=None,
    )
    with repo() as root:
        write_registry(root, ("gamle-ting", "/removed-feature"))
        assert "userdocs-orphan-topic" not in rules_fired(root, slugs_only)


def test_orphan_topic_line_exemption_is_honoured() -> None:
    with repo() as root:
        write(
            root,
            REGISTRY_FILE,
            "public static class DocRegistry\n{\n"
            f"        // standards: userdocs-orphan-topic exempt -- {EXEMPT_REASON}\n"
            + registry_entry("gamle-ting", "/removed-feature")
            + "}\n",
        )
        assert "userdocs-orphan-topic" not in rules_fired(root)


def test_multiple_topics_may_share_one_route() -> None:
    with repo() as root:
        write_registry(
            root,
            ("indstillinger", "/settings"),
            ("regnskabsintegration", "/settings"),
            ("betalingsintegration", "/settings"),
        )
        write(root, "App/Pages/Settings.razor", page("/settings", "indstillinger"))
        assert "userdocs-orphan-topic" not in rules_fired(root)


# ---- the commit-stage note -------------------------------------------------------------------


def notes(root: Path, *staged: str) -> list[str]:
    return unsynced_change_notes(root, config_with(CONFIG), [root / path for path in staged])


def test_staged_documented_page_without_registry_or_content_notes() -> None:
    with repo() as root:
        write_registry(root, ("fakturaer", "/invoices"))
        write(root, "App/Pages/Invoices.razor", page("/invoices", "fakturaer"))
        produced = notes(root, "App/Pages/Invoices.razor")
        assert len(produced) == 1 and "fakturaer" in produced[0]


def test_staged_registry_alongside_the_page_satisfies_the_note() -> None:
    with repo() as root:
        write_registry(root, ("fakturaer", "/invoices"))
        write(root, "App/Pages/Invoices.razor", page("/invoices", "fakturaer"))
        assert notes(root, "App/Pages/Invoices.razor", REGISTRY_FILE) == []


def test_staged_topic_content_satisfies_the_note() -> None:
    with repo() as root:
        write_registry(root, ("fakturaer", "/invoices"))
        write(root, "App/Pages/Invoices.razor", page("/invoices", "fakturaer"))
        write(root, "App/Docs/Content/DocFakturaer.razor", "<h1>Fakturaer</h1>\n")
        assert notes(root, "App/Pages/Invoices.razor", "App/Docs/Content/DocFakturaer.razor") == []


def test_staged_page_without_help_links_produces_no_note() -> None:
    with repo() as root:
        write_registry(root, ("fakturaer", "/invoices"))
        write(root, "App/Pages/Landing.razor", page("/"))
        assert notes(root, "App/Pages/Landing.razor") == []


def test_no_userdocs_config_produces_no_note() -> None:
    with repo() as root:
        write(root, "App/Pages/Invoices.razor", page("/invoices", "fakturaer"))
        produced = unsynced_change_notes(root, config_with(None), [root / "App/Pages/Invoices.razor"])
        assert produced == []


# ---- config parsing --------------------------------------------------------------------------


def test_userdocs_config_parses_from_standards_json() -> None:
    with repo() as root:
        write(
            root,
            ".standards.json",
            json.dumps(
                {
                    "userDocs": {
                        "registry": {"file": REGISTRY_FILE, "pattern": REGISTRY_PATTERN},
                        "helpLinks": [{"glob": "App/Pages/*.razor", "pattern": HELP_PATTERN}],
                        "routes": [{"glob": "App/Pages/*.razor", "pattern": ROUTE_PATTERN}],
                        "contentGlob": "App/Docs/Content/*.razor",
                    }
                }
            ),
        )
        config = CheckConfig.load(root / ".standards.json")
        assert config.user_docs == CONFIG


def test_absent_userdocs_key_parses_to_none() -> None:
    with repo() as root:
        write(root, ".standards.json", "{}")
        assert CheckConfig.load(root / ".standards.json").user_docs is None


if __name__ == "__main__":
    sys.exit(run_module_tests(globals(), "standards_userdocs"))
