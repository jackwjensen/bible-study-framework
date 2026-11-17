"""Cases for the tls-verification-disabled rule.

standards: tls-verification-disabled exempt -- this suite's fixtures are literal
verification-disabling lines, quoted so the detector can be tested against them, never
applied as configuration; the marker keeps the scanner from reporting the PHP fixtures
below on this file itself.

THE CASE THAT MOTIVATED IT is `test_the_expired_cert_workaround_is_flagged`: the one line
somebody types when a peer's certificate has expired and the calls must start working again.
It is a security defect rather than a style nit -- so it is not config-gated and a repo
cannot tune it off.

THE NEGATIVES CARRY EQUAL WEIGHT. A real validator and a named CA bundle are exactly the
code this rule steers toward, so reporting `= CertificatePinningValidator` or
`'verify' => '/etc/ssl/company-ca.pem'` would be flagging the fix as if it were the defect.
That half is what a regex gets wrong, and it is why the wrapped-assignment case below reads
the next CODE line rather than simply widening the pattern.

Run: python test_standards_tls.py   (or pytest)
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from standards_exemptions import MIN_EXEMPTION_REASON_LENGTH  # noqa: E402
from standards_selftest import run_module_tests  # noqa: E402
from standards_tls import TLS_RULE, check_tls_verification  # noqa: E402

EXEMPT = "local mailpit endpoint with a self-signed certificate; never reached in production"
assert len(EXEMPT) >= MIN_EXEMPTION_REASON_LENGTH, "fixture must clear the reason floor"


def found(name: str, *lines: str) -> list[str]:
    return [v.message for v in check_tls_verification(Path(name), list(lines))]


# ---- C# ---------------------------------------------------------------------------------------


def test_the_expired_cert_workaround_is_flagged() -> None:
    """The line typed to make an expired peer certificate stop failing."""
    assert found(
        "MailClient.cs",
        "    handler.ServerCertificateCustomValidationCallback = (_, _, _, _) => true;",
    )


def test_the_wrapped_assignment_is_flagged() -> None:
    """The property name and four lambda parameters rarely fit one line, so the verdict is
    routinely on the next line -- the form a single-line regex would miss entirely."""
    assert found(
        "MailClient.cs",
        "    handler.ServerCertificateCustomValidationCallback =",
        "        (message, cert, chain, errors) => true;",
    )


def test_a_delegate_block_returning_true_is_flagged() -> None:
    assert found(
        "Legacy.cs",
        "    handler.RemoteCertificateValidationCallback = delegate { return true; };",
    )


def test_a_comment_cannot_hide_the_verdict() -> None:
    """The lookahead walks CODE lines, so an intervening comment does not separate the
    assignment from its `=> true`."""
    assert found(
        "MailClient.cs",
        "    handler.ServerCertificateCustomValidationCallback =",
        "        // the staging box has a self-signed cert",
        "        (_, _, _, _) => true;",
    )


def test_a_named_validator_is_clean() -> None:
    """Assigning a real validator is the correct code this rule steers toward."""
    assert not found(
        "MailClient.cs",
        "    handler.ServerCertificateCustomValidationCallback = CertificatePinningValidator;",
    )


def test_a_lambda_that_computes_a_verdict_is_clean() -> None:
    assert not found(
        "MailClient.cs",
        "    handler.ServerCertificateCustomValidationCallback =",
        "        (_, _, _, errors) => errors == SslPolicyErrors.None;",
    )


def test_an_unrelated_true_returning_lambda_is_clean() -> None:
    """`=> true` is ordinary code; only its assignment to a validation callback is a finding."""
    assert not found("Filters.cs", "    var always = new Func<bool>(() => true);")


# ---- PHP --------------------------------------------------------------------------------------


def test_curl_verifypeer_false_is_flagged() -> None:
    assert found("Http.php", "    CURLOPT_SSL_VERIFYPEER => false,")


def test_curl_verifypeer_zero_via_setopt_is_flagged() -> None:
    assert found("Http.php", "    curl_setopt($ch, CURLOPT_SSL_VERIFYPEER, 0);")


def test_curl_verifyhost_zero_is_flagged() -> None:
    assert found("Http.php", "    CURLOPT_SSL_VERIFYHOST => 0,")


def test_guzzle_verify_false_is_flagged() -> None:
    assert found("Client.php", "    'verify' => false,")


def test_curl_verifypeer_true_is_clean() -> None:
    assert not found("Http.php", "    CURLOPT_SSL_VERIFYPEER => true,")


def test_curl_verifyhost_two_is_clean() -> None:
    """2 is the correct value -- chain checked and hostname compared."""
    assert not found("Http.php", "    CURLOPT_SSL_VERIFYHOST => 2,")


def test_a_named_ca_bundle_is_clean() -> None:
    """Pointing `verify` at a CA bundle is the fix for a private CA, not the defect."""
    assert not found("Client.php", "    'verify' => '/etc/ssl/company-ca.pem',")


# ---- shared -----------------------------------------------------------------------------------


def test_the_message_steers_toward_fixing_the_trust_chain() -> None:
    message = found("Http.php", "    CURLOPT_SSL_VERIFYPEER => false,")[0]
    assert "Fix the trust chain" in message


def test_the_message_names_the_outage_tradeoff() -> None:
    """The message has to say why it is worth the argument during an incident."""
    message = found("Client.php", "    'verify' => false,")[0]
    assert "temporary outage for a permanent hole" in message


def test_a_line_exemption_silences_it() -> None:
    assert not found(
        "Http.php",
        f"// standards: {TLS_RULE} exempt -- {EXEMPT}",
        "    CURLOPT_SSL_VERIFYPEER => false,",
    )


def test_a_trailing_line_exemption_silences_it_too() -> None:
    assert not found(
        "MailClient.cs",
        "    handler.ServerCertificateCustomValidationCallback = (_, _, _, _) => true;"
        f"  // standards: {TLS_RULE} exempt -- {EXEMPT}",
    )


def test_an_unrelated_language_is_ignored() -> None:
    """JS/TS and Python are owned by eslint and ruff; restating them here would put one
    standard in two layers."""
    assert not found("client.ts", "  const agent = new https.Agent({ rejectUnauthorized: false });")
    assert not found("client.py", "requests.get(url, verify=False)")


if __name__ == "__main__":
    sys.exit(run_module_tests(globals(), "tls-verification-disabled cases"))
