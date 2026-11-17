#!/usr/bin/env python3
"""Code that keeps the TLS encryption and throws away the identity check.

    tls-verification-disabled   a certificate validator hardcoded to accept anything

standards: tls-verification-disabled exempt -- this file and its tests NAME the disabling
shapes in order to detect them; every option and callback quoted below is the subject of a
regex, never a live client configuration. Stated in the header because the marker is only
read in the first 40 lines and the PHP leads further down are real occurrences that would
otherwise flag this detector on itself.

WHAT VERIFICATION ACTUALLY BUYS. TLS does two things: it encrypts the channel, and it proves
the party at the other end is who the name says. Disabling verification keeps the first and
discards the second -- so the traffic is still unreadable to a passive observer, and completely
open to anyone who can get into the path, because the client will now accept whatever
certificate it is handed. The connection still shows a padlock's worth of encryption while no
longer answering the only question the certificate was there to answer.

WHY IT EARNS A RULE OF ITS OWN, and why it belongs beside the certificate-expiry rule rather
than in a general security bucket: this is what people reach for WHEN A CERTIFICATE EXPIRES.
The peer's certificate lapses, the calls start failing, and one line makes the failure stop.
It works, it ships under outage pressure, and nothing ever fails again to prompt removing it.
That converts a temporary availability incident into a permanent security defect -- the
expiry is fixed within days by whoever owns the certificate, and the disabled check stays for
years. Microsoft's own EX1464935 (2026-08-31) is the shape of the triggering event: an
internal certificate expired unrenewed and every dependent client started failing at once.

WHAT IT LOOKS FOR -- only the residue the stronger layers cannot express, because a standard
lives in exactly one layer:

  * C# (.cs) -- a validation callback assigned an inline lambda or delegate that returns
                `true` unconditionally: `ServerCertificateCustomValidationCallback`,
                `RemoteCertificateValidationCallback`, `ServerCertificateValidationCallback`.
                The assignment is commonly wrapped across two lines, so each callback line is
                read together with the one after it.
  * PHP      -- curl's `CURLOPT_SSL_VERIFYPEER` set to false/0 (in the options array or via
                `curl_setopt`), `CURLOPT_SSL_VERIFYHOST` set to 0, and Guzzle's
                `'verify' => false`.

WHAT IS DELIBERATELY NOT HERE. The two C# kill-switch SYMBOLS
(`DangerousAcceptAnyServerCertificateValidator`, `ServicePointManager.
ServerCertificateValidationCallback`) are banned in `dotnet/BannedSymbols.txt`, which is a
stronger layer and already decides them. JS/TS `rejectUnauthorized: false` and
`NODE_TLS_REJECT_UNAUTHORIZED` are `no-restricted-syntax` entries in
`typescript/eslint-standards.mjs`. Python's `verify=False` and `ssl._create_unverified_context`
are ruff's S501 and S323, and the `S` family is already selected in `python/ruff-standards.toml`.
Restating any of them here would be the pack's own single-source rule broken by the module
that exists to enforce a standard.

A NAMED TRUST ANCHOR IS THE FIX and must never be the finding: a callback that inspects the
chain, a pinned thumbprint comparison, curl's `CAINFO`, Guzzle's `'verify' => '/path/ca.pem'`
all pass. Only an unconditional accept is reported.

WHY THE SCANNER AND NOT AN ANALYZER. It is a security property rather than an estate
convention, so it is not config-gated and a repo cannot tune it off. It is line-exemptable,
because one options module legitimately holds a local self-signed dev endpoint beside
production configuration and a file-scoped marker would excuse both.

Source of truth: engineering-standards/engineering_standards/standards_tls.py
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, Optional

from standards_core import Violation, iter_code_lines
from standards_exemptions import exemption_reason, line_exemption_reason
from standards_scope import SOURCE_SUFFIXES

TLS_RULE = "tls-verification-disabled"

# C#: the three callback properties whose job is to decide whether a certificate is
# acceptable. Named alone they are neutral -- assigning a real validator is the correct code
# -- so the finding needs the RESULT as well, which is why the return value is a separate
# pattern applied to the callback line joined with the one after it.
CS_CALLBACK = re.compile(
    r"\b(?:ServerCertificateCustomValidationCallback"
    r"|RemoteCertificateValidationCallback"
    r"|ServerCertificateValidationCallback)\b"
)

# The unconditional accept, in the two spellings C# has: an expression-bodied lambda whose
# body is the literal `true`, and a `delegate { return true; }` block. Both require the
# literal, so a lambda that computes a verdict (`(_, _, chain, errors) => errors ==
# SslPolicyErrors.None`) is not matched.
CS_ACCEPTS_ANYTHING = re.compile(r"=>\s*true\b|\breturn\s+true\s*;")

# PHP: curl's two verification switches, in both the options-array (`=>`) and the
# `curl_setopt($ch, OPTION, value)` (`,`) spellings. VERIFYPEER off is the whole check;
# VERIFYHOST at 0 keeps the chain check but stops comparing the name on the certificate to
# the host actually dialled, which accepts any valid certificate from anywhere.
PHP_VERIFYPEER_OFF = re.compile(r"\bCURLOPT_SSL_VERIFYPEER\b\s*(?:=>|,)\s*(?:false|0)\b", re.IGNORECASE)
PHP_VERIFYHOST_OFF = re.compile(r"\bCURLOPT_SSL_VERIFYHOST\b\s*(?:=>|,)\s*(?:false|0)\b", re.IGNORECASE)

# Guzzle's request option. Gated on the literal `false` so that pointing it at a CA bundle --
# `'verify' => '/etc/ssl/company-ca.pem'`, the actual fix for a private CA -- is not reported.
PHP_GUZZLE_VERIFY_OFF = re.compile(r"""['"]verify['"]\s*=>\s*false\b""")

_CS_LEAD = "this assigns a certificate-validation callback that returns `true` for every certificate it is shown"
_PHP_VERIFYPEER_LEAD = "this sets `CURLOPT_SSL_VERIFYPEER` to false, which switches off the check entirely"
_PHP_VERIFYHOST_LEAD = (
    "this sets `CURLOPT_SSL_VERIFYHOST` to 0, which stops the certificate's name being "
    "compared to the host actually dialled -- any valid certificate from anywhere is then "
    "accepted for this connection"
)
_PHP_GUZZLE_LEAD = "this passes `'verify' => false` to Guzzle, which disables certificate validation"

_SHARED_TAIL = (
    ". TLS does two jobs -- it encrypts the channel, and it proves who is on the other end. "
    "This keeps the first and discards the second, so anything that can get into the network "
    "path can read and rewrite the traffic while the connection still looks encrypted. It is "
    "usually typed to make an expired certificate stop failing, which trades a temporary "
    "outage for a permanent hole: the certificate gets renewed within days, this line stays "
    "for years. Fix the trust chain instead -- install the CA the peer actually uses, or pin "
    "the one certificate you mean to accept. If this is a local development endpoint with a "
    f"self-signed certificate, say so on the line above: 'standards: {TLS_RULE} exempt -- <why>'."
)


def _php_lead_for_line(line: str) -> Optional[str]:
    """The message lead for a PHP disabling shape on this line, or None."""
    if PHP_VERIFYPEER_OFF.search(line):
        return _PHP_VERIFYPEER_LEAD
    if PHP_VERIFYHOST_OFF.search(line):
        return _PHP_VERIFYHOST_LEAD
    if PHP_GUZZLE_VERIFY_OFF.search(line):
        return _PHP_GUZZLE_LEAD
    return None


def check_tls_verification(path: Path, lines: list[str]) -> Iterable[Violation]:
    """Every line that turns off TLS certificate verification.

    Line-scoped rather than file-scoped: an HTTP-client factory legitimately holds a
    self-signed local endpoint beside the production client, and a header marker would excuse
    both. The file-scoped marker is kept only for the case that is not application code -- a
    file whose subject IS these patterns, this detector and its fixtures -- and is checked
    once before the walk, exactly as standards_cors.py treats its own kind.
    """
    suffix = path.suffix
    if suffix not in SOURCE_SUFFIXES or suffix not in (".cs", ".php"):
        return

    if exemption_reason(lines, TLS_RULE) is not None:
        return

    code_lines = list(iter_code_lines(lines, suffix))

    for index, (line_number, line) in enumerate(code_lines):
        if suffix == ".php":
            lead = _php_lead_for_line(line)
        else:
            # The C# assignment is routinely wrapped, because the four lambda parameters and
            # the property name rarely fit one line:
            #     handler.ServerCertificateCustomValidationCallback =
            #         (message, cert, chain, errors) => true;
            # so the verdict is looked for across this line and the next CODE line -- not the
            # next raw line, which would let an intervening comment hide it.
            lead = None
            if CS_CALLBACK.search(line):
                following = code_lines[index + 1][1] if index + 1 < len(code_lines) else ""
                if CS_ACCEPTS_ANYTHING.search(line) or CS_ACCEPTS_ANYTHING.search(following):
                    lead = _CS_LEAD

        if lead is None:
            continue

        if line_exemption_reason(lines, line_number - 1, TLS_RULE) is not None:
            continue

        yield Violation(
            path=path,
            line=line_number,
            rule=TLS_RULE,
            message=lead + _SHARED_TAIL,
        )
