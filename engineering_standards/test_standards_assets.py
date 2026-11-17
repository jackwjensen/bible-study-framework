"""Cases for the asset-unversioned rule.

THE CASE THAT MOTIVATED IT: sourcetext.ai's root site linked `/site.css` and
`/account.js` at fixed addresses, nginx said "always revalidate", and Cloudflare told
browsers four hours -- so a deploy of the account pages stayed invisible to anyone who had
visited that afternoon (2026-11-11).

The negatives are the estate's working shapes, quoted from the repos that have them: an
mtime stamp computed in PHP (allegroit-dk), a build stamp in Razor (InvoTrack), a Vite entry
page, a hashed bundle, Blazor's fingerprinted `_framework/` script, and a CDN's file. A rule
that flagged those would be noise on exactly the code that already does it right.

Run: python test_standards_assets.py   (or pytest)
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from standards_assets import check_page, check_unversioned_assets, is_unversioned  # noqa: E402
from standards_core import CheckConfig  # noqa: E402
from standards_exemptions import MIN_EXEMPTION_REASON_LENGTH  # noqa: E402
from standards_selftest import run_module_tests  # noqa: E402

EXEMPT = "a third-party widget loader whose vendor requires this exact, unchanging address"
assert len(EXEMPT) >= MIN_EXEMPTION_REASON_LENGTH, "fixture must clear the reason floor"


def found(*lines: str, name: str = "page.html") -> list[int]:
    return [v.line for v in check_page(Path(name), list(lines))]


# ---- it fires ----------------------------------------------------------------------------


def test_the_sourcetext_stylesheet_is_flagged() -> None:
    """Verbatim from web-root/site/account.html before the fix."""
    assert found('<link rel="stylesheet" href="/site.css">') == [1]


def test_a_module_script_is_flagged() -> None:
    assert found('<script type="module" src="/account.js"></script>') == [1]


def test_a_relative_address_is_flagged() -> None:
    """The prototypes' `href="styles.css"` is as fixed as an absolute one."""
    assert found('<link rel="stylesheet" href="styles.css">') == [1]


def test_a_mjs_and_single_quotes_are_flagged() -> None:
    assert found("<script type='module' src='/app/main.mjs'></script>") == [1]


def test_two_references_on_one_line_are_both_reported() -> None:
    line = '<link rel="stylesheet" href="/a.css"><script src="/b.js"></script>'
    assert len(found(line)) == 2


def test_a_vendored_library_is_flagged() -> None:
    """DonorLink's `lib/bootstrap/...`: an upgrade changes the bytes and not the address."""
    assert found('<link rel="stylesheet" href="lib/bootstrap/bootstrap.min.css" />', name="App.razor") == [1]


def test_a_plain_word_after_a_dash_is_not_a_hash() -> None:
    """`account-sections.js` must not pass as a hashed bundle."""
    assert is_unversioned("/account-sections.js")
    assert is_unversioned("/bootstrap.bundle.min.js")


# ---- it stays quiet ----------------------------------------------------------------------


def test_a_query_stamp_passes() -> None:
    assert found('<link rel="stylesheet" href="/site.css?v=3f2a9c1b">') == []
    assert found('<script type="module" src="/account.js?v=__ASSET_VERSION__"></script>') == []


def test_a_php_computed_stamp_passes() -> None:
    """allegroit-dk's layout.php, whose `?>` sits inside the quoted value."""
    line = (
        '<link rel="stylesheet" href="/assets/css/<?= $css_name ?>.css'
        "<?= $css_ver === null ? '' : '?v=' . $css_ver ?>\">"
    )
    assert found(line, name="layout.php") == []


def test_a_razor_stamp_passes() -> None:
    """InvoTrack's App.razor."""
    assert found('<link href="css/app.css?v=@InvoTrack.Components.AssetVersion.Current" rel="stylesheet" />') == []


def test_a_hashed_bundle_passes() -> None:
    assert found('<script type="module" src="/assets/index-BkX2a9Qz.js"></script>') == []
    assert found('<link rel="stylesheet" href="/assets/app.3f2a9c1b.css">') == []


def test_blazors_framework_script_passes() -> None:
    assert found('<script src="_framework/blazor.web.js"></script>', name="App.razor") == []


def test_a_cdn_file_passes() -> None:
    assert found('<script src="https://cdn.jsdelivr.net/npm/x@1.2.3/x.min.js"></script>') == []
    assert found('<script src="//cdn.example.com/x.js"></script>') == []


def test_a_link_that_is_not_an_asset_passes() -> None:
    assert found('<link rel="canonical" href="https://sourcetext.ai/account">') == []
    assert found('<link rel="icon" href="/favicon.ico">') == []


def test_an_anchor_is_not_a_reference() -> None:
    """Only `<link>` and `<script>` load an asset; an `<a>` to a .js file is a download."""
    assert found('<a href="/llms-helper.js">source</a>') == []


def test_a_line_marker_answers_the_rule() -> None:
    assert (
        found(
            f"<!-- standards: asset-unversioned exempt -- {EXEMPT} -->",
            '<script src="/widget/loader.js"></script>',
        )
        == []
    )


# ---- the tree walk -----------------------------------------------------------------------


def _git_tree(files: dict[str, str]) -> Path:
    root = Path(tempfile.mkdtemp())
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    for relative, body in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    return root


def flagged(files: dict[str, str]) -> list[str]:
    root = _git_tree(files)
    return sorted(v.path.relative_to(root).as_posix() for v in check_unversioned_assets(root, CheckConfig()))


def test_the_walk_reports_a_served_page() -> None:
    assert flagged({"site/index.html": '<link rel="stylesheet" href="/site.css">'}) == ["site/index.html"]


def test_a_vite_entry_page_is_the_bundlers_input() -> None:
    """`index.html` beside `vite.config.ts` names `/src/main.js`; the build hashes it."""
    assert (
        flagged(
            {
                "web/index.html": '<script type="module" src="/src/main.js"></script>',
                "web/vite.config.ts": "export default {}",
            }
        )
        == []
    )


def test_node_modules_and_ignored_output_are_not_read() -> None:
    assert (
        flagged(
            {
                ".gitignore": "dist/\n",
                "node_modules/pkg/demo.html": '<script src="/demo.js"></script>',
                "dist/index.html": '<script src="/main.js"></script>',
            }
        )
        == []
    )


def test_markup_outside_a_page_template_is_not_read() -> None:
    """A docs page quoting the bad shape is prose, not a page."""
    assert flagged({"docs/assets.md": '<link rel="stylesheet" href="/site.css">'}) == []


if __name__ == "__main__":
    sys.exit(run_module_tests(globals(), "asset-unversioned cases"))
