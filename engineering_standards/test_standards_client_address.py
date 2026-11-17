#!/usr/bin/env python3
"""Cases for `client-address`.

Both halves, per docs/changing-a-rule.md. The synthetic cases below answer "does the signal
fire"; `test_stays_quiet_on_this_repos_own_source` answers the half synthetic cases cannot,
because a regex that has never been run over real code is indistinguishable from one that
fires on everything.

standards: client-address exempt -- every accessor below is a fixture the detector is
supposed to catch, not an address this file resolves; the file IS the pattern table.

Run: pytest test_standards_client_address.py
"""

from __future__ import annotations

import sys
from pathlib import Path

PACK_DIRECTORY = Path(__file__).resolve().parent
sys.path.insert(0, str(PACK_DIRECTORY))

from standards_client_address import (  # noqa: E402
    CLIENT_ADDRESS_RULE,
    IDIOM_WINDOW,
    check_client_address,
)


def _findings(source: str, name: str = "views.py") -> list[int]:
    lines = source.splitlines()
    return [v.line for v in check_client_address(Path(name), lines)]


# ---- the peer address used as the client -----------------------------------------------


def test_django_remote_addr_subscript_fires():
    assert _findings("client = request.META['REMOTE_ADDR']\n") == [1]


def test_django_remote_addr_get_fires():
    assert _findings('client = request.META.get("REMOTE_ADDR")\n') == [1]


def test_php_server_superglobal_fires():
    assert _findings("$ip = $_SERVER['REMOTE_ADDR'];\n", "Controller.php") == [1]


def test_aspnet_connection_remote_ip_fires():
    source = "var ip = HttpContext.Connection.RemoteIpAddress;\n"
    assert _findings(source, "Handler.cs") == [1]


def test_aspnet_null_conditional_still_fires():
    source = "string? ip = context.Connection?.RemoteIpAddress?.ToString();\n"
    assert _findings(source, "Handler.cs") == [1]


def test_node_socket_remote_address_fires():
    assert _findings("const ip = req.socket.remoteAddress;\n", "server.ts") == [1]


# ---- the leftmost forwarded entry --------------------------------------------------------


def test_leftmost_forwarded_entry_fires():
    source = "ip = request.headers['X-Forwarded-For'].split(',')[0]\n"
    assert _findings(source) == [1]


def test_leftmost_via_first_fires():
    source = "var ip = Request.Headers[\"X-Forwarded-For\"].ToString().Split(',').First();\n"
    assert _findings(source, "Handler.cs") == [1]


def test_php_explode_front_fires():
    source = "$ip = explode(',', $_SERVER['HTTP_X_FORWARDED_FOR'])[0];\n"
    # Two readings on one line -- the superglobal and the leftmost take. One finding per
    # line is correct: there is one address being resolved and one fix.
    assert _findings(source, "Controller.php") == [1]


def test_camel_case_spelling_fires():
    assert _findings("const ip = xForwardedFor.split(',')[0];\n", "server.ts") == [1]


# ---- the fallback chain, where the two halves are not on one line ------------------------
#
# The idiom that evaded the first version of this detector: the header names are hoisted into
# a collection, so the container read is `$_SERVER[$header]` rather than a literal subscript,
# and the `explode(',', ...)[0]` sits several lines below the name it applies to. Reproduced
# against jacks_corner's `lib/request.php` on 2026-08-31, which contains BOTH wrong readings
# and scored zero. It is not an exotic shape -- it is the natural way to write a fallback
# chain, and it appears in at least two repos in this estate.


def test_php_fallback_chain_fires_on_both_readings():
    source = (
        "<?php\n"
        "function client_ip(): string {\n"
        "    foreach (['HTTP_CF_CONNECTING_IP', 'HTTP_X_FORWARDED_FOR', 'REMOTE_ADDR'] as $header) {\n"
        "        $value = $_SERVER[$header] ?? '';\n"
        "        if (!is_string($value) || $value === '') continue;\n"
        "        $first = trim(explode(',', $value)[0]);\n"
        "        if (filter_var($first, FILTER_VALIDATE_IP)) return $first;\n"
        "    }\n"
        "    return '0.0.0.0';\n"
        "}\n"
    )
    # Line 3 names the peer address in the candidate list; line 6 takes the front of the
    # forwarded chain. Two lines to edit, so two findings.
    assert _findings(source, "request.php") == [3, 6]


def test_django_module_level_candidate_list_fires():
    """The names live ABOVE the function, which is why the window is not function-scoped."""
    source = (
        "CANDIDATES = ('HTTP_X_FORWARDED_FOR', 'REMOTE_ADDR')\n"
        "\n"
        "def client_ip(request):\n"
        "    for name in CANDIDATES:\n"
        "        value = request.META.get(name, '')\n"
        "        if value:\n"
        "            return value.split(',')[0].strip()\n"
        "    return '0.0.0.0'\n"
    )
    assert _findings(source) == [1, 7]


def test_node_header_loop_fires():
    source = (
        "const FORWARD_HEADERS = ['cf-connecting-ip', 'x-forwarded-for'];\n"
        "\n"
        "export function clientIp(req) {\n"
        "  for (const name of FORWARD_HEADERS) {\n"
        "    const value = req.headers[name];\n"
        "    if (typeof value !== 'string' || value === '') continue;\n"
        "    return value.split(',')[0].trim();\n"
        "  }\n"
        "  return req.socket.remoteAddress;\n"
        "}\n"
    )
    # 7 is the leftmost take below the header names; 9 is the peer address, same-line.
    assert _findings(source, "request.ts") == [7, 9]


def _spread(first: str, filler: int, last: str) -> str:
    """`first`, then `filler` lines of unrelated code, then `last`."""
    return "\n".join([first] + [f"pad_{i} = {i}" for i in range(filler)] + [last]) + "\n"


_NAMES_THE_CHAIN = "chain = request.META.get('HTTP_X_FORWARDED_FOR', '')"
_TAKES_THE_FRONT = "first = chain.split(',')[0]"


def test_the_window_reaches_its_limit_and_stops():
    """At IDIOM_WINDOW lines apart the two halves are one expression; at eleven they are not.

    The variable is called `chain` rather than the natural `xff` on purpose -- `\\bxff\\b` is
    itself a FORWARDED_NAME spelling, so that name would satisfy the pairing on the take's
    own line and the test would pass at any distance without exercising the window at all.
    """
    assert _findings(_spread(_NAMES_THE_CHAIN, IDIOM_WINDOW - 1, _TAKES_THE_FRONT)) == [IDIOM_WINDOW + 1]
    assert _findings(_spread(_NAMES_THE_CHAIN, IDIOM_WINDOW, _TAKES_THE_FRONT)) == []


def test_the_take_above_the_name_is_not_a_pair():
    """A fallback chain names its headers and then reads them, never the reverse."""
    assert _findings(f"{_TAKES_THE_FRONT}\n{_NAMES_THE_CHAIN}\n") == []


def test_a_header_named_only_in_a_comment_is_not_evidence():
    """The reported line is comment-guarded; so is every line the window reads as evidence."""
    source = "# the X-Forwarded-For header, named only here\npad = 1\nfirst = c.split(',')[0]\n"
    assert _findings(source) == []


# The marker is pushed below HEADER_SCAN_LINES on purpose. `exemption_reason` reads a file's
# first 40 lines, so a marker written above that line is ALSO a valid file-scoped marker and
# would silence the file whether or not line scope works -- a test placing it at the top
# would pass on a build where the line scope had been deleted outright.
_BELOW_THE_HEADER_SCAN = ["# module header, pushing what follows past the 40-line header scan."] * 45

_HOISTED_CANDIDATE_LIST = [
    "CANDIDATES = ('HTTP_CF_CONNECTING_IP', 'REMOTE_ADDR')",
    "",
    "def peer(request):",
    "    for name in CANDIDATES:",
    "        return request.META.get(name, '')",
]


def test_the_hoisted_name_fires_without_a_marker():
    """The other half of the test below: proof that source is a finding to begin with."""
    source = "\n".join(_BELOW_THE_HEADER_SCAN + _HOISTED_CANDIDATE_LIST) + "\n"
    assert _findings(source) == [len(_BELOW_THE_HEADER_SCAN) + 1]


def test_the_line_exemption_still_silences_the_hoisted_name():
    """The finding lands on the line you would edit, so the marker goes above that line."""
    marker = [
        "# standards: client-address exempt -- the candidate list inside the one helper that",
        "# resolves the address; REMOTE_ADDR is last, for local development with no proxy.",
    ]
    source = "\n".join(_BELOW_THE_HEADER_SCAN + marker + _HOISTED_CANDIDATE_LIST) + "\n"
    assert _findings(source) == []


# ---- what must stay silent ---------------------------------------------------------------


def test_a_comment_is_not_a_finding():
    source = "# REMOTE_ADDR is the proxy, never the client -- see session-authority.md\n"
    assert _findings(source) == []


def test_the_line_exemption_silences_it():
    source = (
        "    # standards: client-address exempt -- the last fallback inside the one helper\n"
        "    # that resolves the address, for local development with no proxy in front.\n"
        "    return request.META.get('REMOTE_ADDR')\n"
    )
    assert _findings(source) == []


def test_reading_the_forwarded_chain_without_taking_the_front_is_silent():
    """The correct fallback walks the chain; only taking its FRONT is the defect."""
    source = (
        "forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR', '')\n"
        "for hop in reversed([h.strip() for h in forwarded_for.split(',')]):\n"
        "    if not _is_infrastructure_address(hop):\n"
        "        return hop\n"
    )
    assert _findings(source) == []


def test_the_recommended_header_is_not_a_finding():
    source = "cloudflare_client_ip = request.META.get('HTTP_CF_CONNECTING_IP', '')\n"
    assert _findings(source) == []


def test_an_unrelated_index_zero_is_not_a_finding():
    """`[0]` is everywhere; it is only a finding next to the forwarded header."""
    assert _findings("first_user = users.split(',')[0]\n") == []


def test_a_non_source_suffix_is_skipped():
    assert _findings("request.META['REMOTE_ADDR']\n", "notes.md") == []


# ---- the half synthetic cases cannot do --------------------------------------------------


def test_stays_quiet_on_this_repos_own_source():
    """Real code, exercising every construct these languages have that the rule must ignore.

    `paged-without-order` reported zero findings estate-wide on its first run -- a result
    indistinguishable from a dead regex until its positive cases proved it could fire. The
    cases above are that proof; this is the other half.
    """
    pack_directory = Path(__file__).resolve().parent
    offenders: list[str] = []

    for path in sorted(pack_directory.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        # This module and the rule module both quote the patterns as data.
        if path.name in {"standards_client_address.py", Path(__file__).name}:
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        offenders.extend(f"{path.name}:{v.line}" for v in check_client_address(path, lines))

    assert offenders == [], f"{CLIENT_ADDRESS_RULE} fired on the pack's own source: {offenders}"
