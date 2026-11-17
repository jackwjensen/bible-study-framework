#!/usr/bin/env python3
"""The client's address, read from somewhere that does not know it.

    client-address   the peer address, or the leftmost forwarded entry, used as the client

standards: client-address exempt -- this file names the two wrong readings in order to
detect them; every accessor quoted below is the subject, never a use. Stated up here rather
than beside the prose it excuses, because the marker is only read in the first 40 lines --
buried lower it is invisible to the scanner and to whoever opens the file.

Rate limits, audit trails, abuse detection and geo all key on "who is this request from",
and in an estate that sits behind Cloudflare and a proxy every naive answer is wrong the
same way: it returns a real, well-formed address that belongs to somebody else's
infrastructure. Nothing errors, the value looks plausible in a log, and the feature built on
it is quietly measuring the wrong thing for every request.

WHY THIS EARNED A SCANNER RULE rather than staying prose in session-authority.md. The prose
half was written on 2026-08-27 after allegro-it-services produced two wrong answers in a
row, and the second one is the argument for a mechanism. The first was the classic bug --
`request.META['x-forwarded-for']`, a key Django never sets, so every request fell through to
`REMOTE_ADDR`, which behind the proxy is one address shared by every visitor; its login rate
limit had therefore been a single global bucket for months. The fix, written by someone who
had just read the whole chain and knew exactly what it looked like, then walked the
forwarded header and returned the first public entry -- which is a **Cloudflare edge
server**, because every hostname is proxied. Two consecutive careful readings, two confident
wrong answers. A rule that depends on being read had already failed twice by the time it was
written down.

WHAT IT LOOKS FOR, both shapes being ways of naming the wrong hop:

  * THE PEER ADDRESS USED AS THE CLIENT. `REMOTE_ADDR`, `$_SERVER['REMOTE_ADDR']`,
    `Connection.RemoteIpAddress`, `socket.remoteAddress`. Behind any proxy this is the
    proxy, identical for every visitor.
  * THE LEFTMOST FORWARDED ENTRY. Each nginx hop APPENDS the peer it saw, and a client may
    send `X-Forwarded-For` itself, so the leftmost entry is the one piece of the chain the
    caller controls. `split(',')[0]` is the single most repeated formulation of this fix on
    the internet, and it takes exactly the forgeable part.

BOTH SHAPES ARE ALSO READ ACROSS LINES, because the spelling people actually write does not
put them on one. The first version of this detector matched a single line at a time and so
saw only the textbook formulation; the fallback chain -- the natural way to express "try
these headers in order" -- evaded it completely, for two independent reasons:

    foreach (['HTTP_CF_CONNECTING_IP', 'HTTP_X_FORWARDED_FOR', 'REMOTE_ADDR'] as $header) {
        $value = $_SERVER[$header] ?? '';
        if (!is_string($value) || $value === '') continue;
        $first = trim(explode(',', $value)[0]);

The peer address is never subscripted literally -- `REMOTE_ADDR` is a string in an array and
the access is `$_SERVER[$header]` -- so the accessor patterns cannot see it. And the header's
name sits three lines above the `explode(',', ...)[0]`, so no single line carries both halves
of the leftmost take. Zero findings, on a function containing both defects at once.

That mattered because the rule's whole justification is that careful readers got this wrong
twice in a row: a detector that catches only the spelling nobody writes is a rule that has
failed a third time, silently.

WHAT KEEPS THE WIDENING FROM BECOMING NOISE is what each half refuses to pair. The peer
reading needs a COMPUTED container read near the name, so a header allowlist that merely
mentions `REMOTE_ADDR` is not a finding. The leftmost reading needs the split and the index
on ONE line, and lets only the header's NAME sit further off -- because `$parts[0]` two lines
under an `explode('/', $cidr)` is ordinary code, and pairing those across lines would fire on
a CIDR parser. The consequence worth stating: the CORRECT implementation -- walk the chain
from the right, reach the peer address exactly once on a deliberate last-resort line -- is
as quiet after the widening as it was before it. `test_standards_client_address.py` carries
both halves, and `common/utils.py` in allegro-it-services is the shape being protected.

WHAT IT DELIBERATELY DOES NOT LOOK FOR. The correct answer is not decidable from one line,
only the wrong ones. A rule that demanded a particular header would be asserting a network
topology it cannot see, and would be wrong the day a repo is deployed anywhere else. So this
fires on the two readings that are wrong under every topology, and leaves the right one to
the prose.

AND THE PROSE NO LONGER SAYS "USE CF-Connecting-IP", which is the 2026-09-01 correction and
the reason this paragraph was rewritten. It used to: the header was called trustworthy
*here* because the origin firewall makes Cloudflare the only route in. That is a true fact
about one network and a bad instruction for a rule, because the instruction travels with the
repo and the firewall does not -- to a site with no Cloudflare, no Docker, no allowlist, or
to a framework someone else deploys. Worse, read first and unconditionally the header makes
the rest of the resolver unreachable, so all four implementations in the estate carried a
fallback described as "correct on its own" that no forged request could ever reach; the
firewall was not their second layer, it was their only one.

The prose now specifies the walk: append the peer address to the chain, step right to left
over the hops THE DEPLOYMENT DECLARES as its own, and stop at the first entry that is not one
of them. Parameterised by the trusted set rather than by a vendor or a topology, so the same
algorithm is correct behind Cloudflare, behind an Azure Application Gateway, and with nothing
in front at all -- where the declared set is empty and the peer address falls out of the walk
by construction.

The first draft of that correction inferred the set instead of declaring it -- "step over
what is not globally routable" -- and Jack rejected it on 2026-09-01 for the right reason:
it is wrong wherever a proxy holds a PUBLIC address, which is every managed load balancer and
WAF, and it silently returns the proxy as the visitor. Private-range hops are a common case,
never a definition. See `claude/rules/session-authority.md`, and the adjacent rule this leans
on: every layer stands alone.

THE EXEMPTION IS LINE-SCOPED, and the canonical helper is the reason. Every repo needs one
function that does reach for the peer address -- as the last fallback, for local development
where there is no proxy at all -- and that function is the correct implementation, not a
violation of it. A file-scoped marker would excuse the helper and every careless read that
later joined it in the same file. See `common/utils.py` in allegro-it-services for the shape
this is protecting.

A FILE-SCOPED MARKER IS ACCEPTED TOO, for one narrow case that is not application code: a
file whose subject IS these patterns, meaning this detector and its fixtures. There the whole
file is the exception and there is no line to point at, which is the same reading
`test_standards_query.py` already applies to `query-shape`. It is deliberately the weaker
instrument -- reach for the line marker in anything that serves a request. This file carries
one, at the top.

Source of truth: engineering-standards/engineering_standards/standards_client_address.py
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from standards_core import Violation
from standards_exemptions import exemption_reason, line_exemption_reason

CLIENT_ADDRESS_RULE = "client-address"

# The peer address, in each language's spelling. Anchored on the accessor rather than the
# bare word so prose in a comment ("REMOTE_ADDR is the proxy") is not a finding -- the
# comment guard below handles the rest.
#
# Express's `req.ip` is deliberately absent: it honours the `trust proxy` setting, so it is
# the framework's own correct answer when that setting is configured and a false accusation
# when it is. `socket.remoteAddress` has no such setting and is always the raw peer.
PEER_ADDRESS = re.compile(
    r"""(?x)
    (?:META\s*(?:\[|\.get\s*\()\s*['"]REMOTE_ADDR['"])      # Django
  | (?:\$_SERVER\s*\[\s*['"]REMOTE_ADDR['"]\s*\])           # PHP
  | (?:\bConnection\s*(?:\.|\?\.)\s*RemoteIpAddress\b)      # ASP.NET Core
  | (?:\b(?:socket|connection)\s*(?:\.|\?\.)\s*remoteAddress\b)  # Node
    """
)

# Naming the forwarded header, in any of the spellings the four languages use for it.
FORWARDED_NAME = re.compile(r"(?i)(?:x[-_]forwarded[-_]for|xforwardedfor|\bxff\b|\bforwarded_?for\b)")

# Taking the front of a comma-separated list: `[0]`, `.first()`, `.Split(',')[0]`,
# `explode(',', $x)[0]`. `.split(',')` followed by an index of 0 is the shape; the split and
# the index may be separated by a cast or a trim, so they are matched independently on the
# line rather than as one glued expression.
SPLITS_ON_COMMA = re.compile(r"""(?:split|explode)\s*\(\s*(?:['"],['"]|['"],\s*['"])?""", re.IGNORECASE)
TAKES_THE_FRONT = re.compile(r"""(?:\[\s*0\s*\]|\.\s*(?:first|First)\s*\(\s*\))""")

# The peer address's NAME, written as a bare string rather than as a subscript on the
# container it belongs to. On its own this is not evidence of anything -- a header allowlist
# names it too, and so does a test that seeds a fake environment -- so it is only read as a
# finding when the indirect read below appears near it.
PEER_ADDRESS_NAME = re.compile(r"""['"]REMOTE_ADDR['"]""")

# A read of a request container whose key is COMPUTED rather than written down:
# `$_SERVER[$header]`, `request.META.get(name)`, `req.headers[name]`. This is the other half
# of the fallback-chain idiom -- the header names have moved into a collection, so the access
# point no longer says which header it is reading, and `PEER_ADDRESS` above (which requires a
# literal subscript) cannot see it.
#
# The subscript must START with an identifier character: that excludes the literal forms the
# accessor patterns already cover (`['REMOTE_ADDR']`) and excludes `headers[0]`, so the only
# thing this matches is a key held in a variable or a constant.
INDIRECT_CONTAINER_READ = re.compile(
    r"""(?x)
    (?:\$_SERVER | \bMETA | \bheaders | \bHeaders | \benviron)
    \s* (?: \[ | \.\s*(?:get|GetValues|TryGetValue)\s*\( )
    \s* (?!['"]) [A-Za-z_$]
    """
)

# How far apart the two halves of the idiom may sit and still be read as one expression.
#
# The chain is always the same five or six lines -- name the headers, open the loop, read the
# container, guard the value, split it, take the front -- so ten leaves room for a guard
# clause or two. It is deliberately NOT function-scoped, which was the first design: header
# names are routinely a module-level constant ABOVE the function that reads them (the
# idiomatic Python spelling), so stopping the walk at a function boundary would blind the
# rule to exactly the case it exists to catch.
IDIOM_WINDOW = 10

COMMENT_LINE = re.compile(r"^\s*(//|#|\*|/\*|--)")

SUFFIXES = frozenset({".py", ".cs", ".razor", ".php", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"})

_PEER_ADVICE = (
    "this is the address of whatever connected to the server, which behind a proxy is the "
    "proxy -- one value shared by every visitor, so anything keyed on it (a rate limit, an "
    "audit row, an abuse counter) is measuring one global bucket rather than one per client"
)
_LEFTMOST_ADVICE = (
    "each proxy hop APPENDS the peer it saw, and a client may send X-Forwarded-For itself, "
    "so the leftmost entry is the one part of the chain the caller controls -- taking it "
    "takes exactly the forgeable part"
)


def _appears_near(pattern: re.Pattern[str], lines: list[str], index: int, *, below: bool) -> bool:
    """Does `pattern` match a non-comment line within IDIOM_WINDOW of `index`, inclusive?

    One direction only, and which one is a property of the idiom rather than a shortcut: a
    fallback chain names its headers and then reads them, never the reverse. Searching both
    ways would double the surface on which two unrelated expressions can be paired, for a
    shape nobody writes.

    Comment lines are skipped here for the same reason the main walk skips them -- this
    file's own prose names every pattern it looks for, and a rule that reads its own
    docstring as evidence would fire on any explanation of itself.
    """
    if below:
        span = range(index, min(len(lines), index + IDIOM_WINDOW + 1))
    else:
        span = range(max(0, index - IDIOM_WINDOW), index + 1)
    return any(not COMMENT_LINE.match(lines[i]) and pattern.search(lines[i]) for i in span)


def _peer_named_in_a_candidate_list(lines: list[str], index: int) -> bool:
    """The peer address named as a bare string, with a computed container read below it.

    Reported on the NAME rather than on the read, because the name is the line you edit:
    the fix is to drop `REMOTE_ADDR` from the chain (or to say why it is the last resort),
    and the read a few lines down is correct code that stays exactly as it is.
    """
    return bool(PEER_ADDRESS_NAME.search(lines[index])) and _appears_near(
        INDIRECT_CONTAINER_READ, lines, index, below=True
    )


def _takes_the_leftmost_entry(lines: list[str], index: int) -> bool:
    """This line takes the front of a comma-separated list that is the forwarded chain.

    The split and the take must be on ONE line; only the header's NAME may be further off.
    That asymmetry is what keeps the widening honest -- `parts[0]` two lines under an
    `explode('/', $cidr)` is ordinary code, and pairing those across lines would have fired
    on jacks_corner's CIDR parser, which is the correct implementation of this very rule.
    """
    line = lines[index]
    if not TAKES_THE_FRONT.search(line):
        return False
    if FORWARDED_NAME.search(line):
        return True
    return bool(SPLITS_ON_COMMA.search(line)) and _appears_near(FORWARDED_NAME, lines, index, below=False)


def check_client_address(path: Path, lines: list[str]) -> Iterable[Violation]:
    """Every line that names the peer address, or takes the front of the forwarded chain.

    Per line rather than per file: one module legitimately holds the canonical helper (which
    reads the peer address as its last fallback) and any number of careless reads beside it,
    and a single marker at the top would excuse both.
    """
    if path.suffix not in SUFFIXES:
        return

    # The weaker instrument, for the narrow case in the module docstring: a file whose
    # subject is the patterns themselves. Checked once, before the walk, so a detector's
    # fixture table does not need a marker per case.
    if exemption_reason(lines, CLIENT_ADDRESS_RULE) is not None:
        return

    for index, line in enumerate(lines):
        if COMMENT_LINE.match(line):
            continue

        if PEER_ADDRESS.search(line) or _peer_named_in_a_candidate_list(lines, index):
            advice = _PEER_ADVICE
        elif _takes_the_leftmost_entry(lines, index):
            advice = _LEFTMOST_ADVICE
        else:
            continue

        if line_exemption_reason(lines, index, CLIENT_ADDRESS_RULE) is not None:
            continue

        yield Violation(
            path=path,
            line=index + 1,
            rule=CLIENT_ADDRESS_RULE,
            message=(
                f"the client's address is being read from the wrong hop: {advice}. Build the "
                "chain instead -- the X-Forwarded-For entries, then the connection's peer "
                "address appended as the last, unforgeable element -- walk it from the "
                "RIGHT over the hops THIS DEPLOYMENT DECLARES as its own, and stop at the "
                "first entry that is not one of them. Declare that set; do not infer it "
                "from the addresses, because 'skip anything not globally routable' stops on "
                "the proxy wherever the proxy has a public address (an Azure Application "
                "Gateway, an ALB, a WAF, a CDN). With nothing declared the peer address "
                "falls out of the walk, which is the right answer for a deployment with "
                "nothing in front. Behind a CDN the chain is what earns the CDN's header: "
                "reaching that CDN's own address among the hops is what proves it is really "
                "adjacent, and only then is CF-Connecting-IP (or X-Azure-ClientIP, "
                "CloudFront-Viewer-Address, Fastly-Client-IP) worth reading -- read first "
                "and unconditionally it is forgeable by anyone who can reach the origin "
                "directly, and it makes the rest of the resolver unreachable in exactly the "
                "case that code exists for. Where no hop can be established, return unknown "
                "rather than a guess. If this IS the last-resort peer read inside the one "
                "helper that resolves the address, say so on the line above: "
                f"'standards: {CLIENT_ADDRESS_RULE} exempt -- <why>'."
            ),
        )
