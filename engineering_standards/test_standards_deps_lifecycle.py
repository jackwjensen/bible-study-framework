"""Cases for standards_deps_lifecycle: which product a dependency is judged against, and its line.

Run: python test_standards_deps_lifecycle.py   (or pytest)
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from standards_deps_cache import CACHE_ENVIRONMENT  # noqa: E402
from standards_deps_declared import DOCKER, DOTNET_SDK, NUGET, PYPI, Dependency  # noqa: E402
from standards_deps_lifecycle import LIFECYCLE_API, Cycle, cycle_of, lifecycle, product_for  # noqa: E402
from standards_selftest import run_module_tests  # noqa: E402


def dependency(ecosystem: str, name: str) -> Dependency:
    return Dependency(ecosystem, name, "1.0.0", Path("f"), 1)


def test_the_runtimes_and_databases_map_to_their_products() -> None:
    assert product_for(dependency(DOCKER, "mysql")) == "mysql"
    assert product_for(dependency(DOCKER, "postgres")) == "postgresql"
    assert product_for(dependency(DOCKER, "node")) == "nodejs"
    assert product_for(dependency(DOCKER, "mcr.microsoft.com/dotnet/aspnet")) == "dotnet"
    assert product_for(dependency(DOTNET_SDK, "dotnet-sdk")) == "dotnet"
    assert product_for(dependency(NUGET, "Microsoft.EntityFrameworkCore.Tools")) == "dotnet", "EF Core tracks .NET"
    assert product_for(dependency(NUGET, "dotnet-ef")) == "dotnet"


def test_a_package_or_a_vendor_image_has_no_lifecycle() -> None:
    assert product_for(dependency(NUGET, "Stripe.net")) is None
    assert product_for(dependency(PYPI, "ruff")) is None
    assert product_for(dependency(DOCKER, "bitnami/mysql")) is None, "a vendor's image is not the product"


def test_a_version_belongs_to_the_cycle_that_prefixes_it() -> None:
    cycles = [Cycle("9.7", True, None), Cycle("8.4", True, None), Cycle("8", False, None), Cycle("10", True, None)]
    assert cycle_of("8.4.11", cycles).name == "8.4", "the longest matching cycle wins"
    assert cycle_of("10.0.401", cycles).name == "10"
    assert cycle_of("8.0.46", cycles).name == "8"
    assert cycle_of("26.7.0", cycles) is None


def test_the_lifecycle_is_parsed_and_cached() -> None:
    payload = {
        "result": {
            "releases": [{"name": "8.4", "isLts": True, "eolFrom": "2032-04-30"}, {"name": "9.7", "isLts": True}]
        }
    }
    asked: list[str] = []

    def fetch(url: str, _headers: dict[str, str]) -> bytes:
        asked.append(url)
        return json.dumps(payload).encode()

    with tempfile.TemporaryDirectory() as tree:
        previous = os.environ.get(CACHE_ENVIRONMENT)
        os.environ[CACHE_ENVIRONMENT] = str(Path(tree) / "cache.json")
        try:
            first = lifecycle("mysql", fetch)
            second = lifecycle("mysql", fetch)
        finally:
            if previous is None:
                os.environ.pop(CACHE_ENVIRONMENT, None)
            else:
                os.environ[CACHE_ENVIRONMENT] = previous
    assert first == second == [Cycle("8.4", True, date(2032, 4, 30)), Cycle("9.7", True, None)]
    assert asked == [LIFECYCLE_API + "mysql"]


if __name__ == "__main__":
    sys.exit(run_module_tests(globals(), "dependency lifecycles"))
