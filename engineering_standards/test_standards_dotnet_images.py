"""Cases for dotnet-consistency: the runtime image against what the projects target.

THE GAP THESE COVER was found by grepping every rule module for `mcr.microsoft.com` on
2026-08-25 and matching nothing. `runtime-support` reads `<TargetFramework>` and nothing else,
and a Dockerfile reached only container-user, the Node floor and the Python floor -- so a repo
targeting net10.0 and building `FROM mcr.microsoft.com/dotnet/aspnet:8.0` produced no finding of
any kind.

The direction matters and is asserted below: an image OLDER than the TFM fails loudly at
startup, so nobody ships it twice. An image NEWER works SILENTLY via roll-forward, and that is
the case worth a rule -- production on a runtime the build never targeted.

Run: python test_standards_dotnet_images.py   (or pytest)
"""

from __future__ import annotations

import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

sys.path.insert(0, str(Path(__file__).resolve().parent))

from standards_core import CheckConfig  # noqa: E402
from standards_dotnet_images import DOTNET_TOOLCHAIN, declarations_in  # noqa: E402
from standards_scope import should_check  # noqa: E402
from standards_selftest import run_module_tests  # noqa: E402
from standards_toolchain_consistency import check_consistency  # noqa: E402
from standards_versions import DOTNET_FLOOR  # noqa: E402

FLOOR = DOTNET_FLOOR
PROJECT = "<Project><PropertyGroup><TargetFramework>net{0}.0</TargetFramework></PropertyGroup></Project>"


@contextmanager
def repo(**files: str) -> Iterator[tuple[Path, list[Path]]]:
    with tempfile.TemporaryDirectory() as tree:
        root = Path(tree)
        for relative, text in files.items():
            path = root / relative.replace("__", "/")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        paths = [path for path in sorted(root.rglob("*")) if path.is_file() and should_check(path, root, CheckConfig())]
        yield root, paths


def agree(**files: str) -> list[str]:
    with repo(**files) as (root, paths):
        return [v.message for v in check_consistency(root, paths, CheckConfig(), DOTNET_TOOLCHAIN)]


# ---- the silent direction --------------------------------------------------------------------


def test_an_image_newer_than_the_tfm_is_flagged() -> None:
    """THE case worth the rule. aspnet:11.0 hosts a net10.0 app happily via roll-forward, so
    production runs a runtime the build never targeted and the tests never exercised. Nothing
    fails; nothing looks wrong."""
    assert agree(**{"App.csproj": PROJECT.format(FLOOR), "Dockerfile": "FROM mcr.microsoft.com/dotnet/aspnet:11.0\n"})


def test_an_image_older_than_the_tfm_is_also_flagged() -> None:
    """Self-correcting at startup, but still a disagreement, and cheaper to catch here than in
    a container that will not boot."""
    assert agree(**{"App.csproj": PROJECT.format(FLOOR), "Dockerfile": "FROM mcr.microsoft.com/dotnet/aspnet:9.0\n"})


def test_a_matching_image_is_clean() -> None:
    assert not agree(
        **{"App.csproj": PROJECT.format(FLOOR), "Dockerfile": f"FROM mcr.microsoft.com/dotnet/aspnet:{FLOOR}.0\n"}
    )


def test_the_runtime_repository_is_read_too() -> None:
    """A console worker ships on `runtime:`, not `aspnet:`."""
    assert agree(
        **{"App.csproj": PROJECT.format(FLOOR), "Dockerfile": "FROM mcr.microsoft.com/dotnet/runtime:9.0-alpine\n"}
    )


def test_a_chiselled_variant_is_read() -> None:
    """`aspnet-composite` and the chiselled tags spell the repository with a suffix."""
    assert agree(
        **{"App.csproj": PROJECT.format(FLOOR), "Dockerfile": "FROM mcr.microsoft.com/dotnet/aspnet-composite:9.0\n"}
    )


def test_a_patch_tag_is_read_by_its_major() -> None:
    assert not agree(
        **{
            "App.csproj": PROJECT.format(FLOOR),
            "Dockerfile": f"FROM mcr.microsoft.com/dotnet/aspnet:{FLOOR}.0.2-noble\n",
        }
    )


def test_a_version_hoisted_into_a_build_argument_is_still_judged() -> None:
    assert agree(
        **{
            "App.csproj": PROJECT.format(FLOOR),
            "Dockerfile": "ARG DOTNET=9.0\nFROM mcr.microsoft.com/dotnet/aspnet:${DOTNET}\n",
        }
    )


# ---- what it deliberately does not read ------------------------------------------------------


def test_the_sdk_stage_is_not_judged() -> None:
    """An SDK ahead of the TFM is normal and supported -- SDK 11 building net10.0 is not a
    defect. Reading it as an environment would invent findings on correct multi-stage builds."""
    assert not agree(
        **{
            "App.csproj": PROJECT.format(FLOOR),
            "Dockerfile": (
                f"FROM mcr.microsoft.com/dotnet/sdk:11.0 AS build\nFROM mcr.microsoft.com/dotnet/aspnet:{FLOOR}.0\n"
            ),
        }
    )


def test_a_non_dotnet_image_is_not_judged() -> None:
    assert not agree(**{"App.csproj": PROJECT.format(FLOOR), "Dockerfile": "FROM node:24-alpine\n"})


def test_netstandard_is_ignored_rather_than_failing_the_declaration() -> None:
    """A library multi-targeting netstandard2.0 for compatibility is judged only on its real
    runtime targets -- and on nothing at all if it has none."""
    project = (
        "<Project><PropertyGroup><TargetFrameworks>netstandard2.0;net"
        f"{FLOOR}.0</TargetFrameworks></PropertyGroup></Project>"
    )
    assert not agree(**{"Lib.csproj": project, "Dockerfile": f"FROM mcr.microsoft.com/dotnet/aspnet:{FLOOR}.0\n"})


def test_a_multi_targeted_library_is_treated_as_a_matrix() -> None:
    """`net8.0;net10.0` ships both, like a test matrix: no vote on what the repo runs, but the
    shipped runtime has to be one of them."""
    project = (
        f"<Project><PropertyGroup><TargetFrameworks>net8.0;net{FLOOR}.0</TargetFrameworks></PropertyGroup></Project>"
    )
    assert not agree(**{"Lib.csproj": project, "Dockerfile": f"FROM mcr.microsoft.com/dotnet/aspnet:{FLOOR}.0\n"})
    assert agree(**{"Lib.csproj": project, "Dockerfile": "FROM mcr.microsoft.com/dotnet/aspnet:11.0\n"})


def test_several_projects_agreeing_with_the_image_is_clean() -> None:
    """The common shape: a solution of projects all on one TFM, one runtime image."""
    assert not agree(
        **{
            "src__App__App.csproj": PROJECT.format(FLOOR),
            "src__Core__Core.csproj": PROJECT.format(FLOOR),
            "tests__Tests__Tests.csproj": PROJECT.format(FLOOR),
            "Dockerfile": f"FROM mcr.microsoft.com/dotnet/aspnet:{FLOOR}.0\n",
        }
    )


def test_one_project_off_the_others_is_flagged() -> None:
    assert agree(
        **{
            "src__App__App.csproj": PROJECT.format(FLOOR),
            "src__Legacy__Legacy.csproj": PROJECT.format(9),
            "Dockerfile": f"FROM mcr.microsoft.com/dotnet/aspnet:{FLOOR}.0\n",
        }
    )


def test_a_repo_with_only_projects_and_no_image_is_judged_on_the_projects() -> None:
    """Most .NET repos here have no Dockerfile at all. Two projects on different TFMs is still
    a disagreement -- and `runtime-support` only catches it if one is below the floor."""
    assert not agree(**{"A.csproj": PROJECT.format(FLOOR), "B.csproj": PROJECT.format(FLOOR)})
    assert agree(**{"A.csproj": PROJECT.format(FLOOR), "B.csproj": PROJECT.format(11)})


def test_the_message_spells_versions_as_target_frameworks() -> None:
    """`.NET net10.0` reads oddly; `net10.0` is what a human greps for in a csproj."""
    message = agree(
        **{"App.csproj": PROJECT.format(FLOOR), "Dockerfile": "FROM mcr.microsoft.com/dotnet/aspnet:11.0\n"}
    )[0]
    assert f"net{FLOOR}.0" in message and "net11.0" in message


def test_the_reader_is_the_descriptors() -> None:
    assert declarations_in is DOTNET_TOOLCHAIN.readers


if __name__ == "__main__":
    sys.exit(run_module_tests(globals(), "dotnet-consistency cases"))
