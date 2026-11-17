---
description: An expiring credential is a scheduled outage — monitor the renewal, surface it as health, and name the failure class when it lapses
alwaysLoad: every language here holds credentials that expire on a date, and which file type they live in predicts nothing about where that failure lands
---

# Expiring credentials: certificates, keyrings, signing keys

Reasoning, the security line on what a message may say, and the incident this came from:
[engineering-standards/docs/certificate-expiry.md](https://github.com/jackwjensen/engineering-standards/blob/master/docs/certificate-expiry.md). Disabling verification to work
around an expiry is the automated `tls-verification-disabled` rule and the two banned symbols
in `BannedSymbols.txt`, not restated here.

**A certificate carries its own expiry date, so its failure is scheduled, not random.** Every
other dependency failure is a surprise you can only handle well. This one is a date you can
read months ahead — so reaching the error path is already the defect, and the rules below are
ordered by how early they act.

Applies to every credential that expires on a date, not only TLS: the Data Protection
keyring, signing keys, provider secrets with a rotation period, and any token with an
`exp`. Owning it changes nothing about the handling — see the docs page.

1. **Every expiring credential has a named owner and a monitored expiry.** If nothing in the
   system reads its `NotAfter`, the first thing that notices is an outage.

2. **Monitor the renewal, not the certificate.** "Valid for another 60 days" stays green for
   89 days after the renewal automation silently dies; "renewal ran last night" goes red the
   week it breaks. Alert on the renewal not being observed within its expected interval.

3. **Expiry surfaces as a health check, never first as a user-facing failure.** Degraded at 30
   days, unhealthy at 7. A credential read at startup gets its expiry logged there too.

4. **Handle the expiry as its own case, not as a connection failure.** An expired peer
   certificate arrives as a generic transport error (in .NET, an `AuthenticationException`
   inside an `HttpRequestException`); left undistinguished it reads as "couldn't connect", and
   somebody spends an evening debugging their own code. Inspect the chain status
   (`X509ChainStatusFlags.NotTimeValid`) and report it as what it is.

5. **Never retry an expiry.** It is deterministic and time-based, so retry logic spends the
   outage hammering a wall. Circuit-break and report.

6. **Report the failure class, never the credential's identity.** The user is told *that a
   certificate expired and that it is server-side* — enough to know it is not their account,
   not an attacker, and not fixable by retrying. Thumbprints, internal hostnames and internal
   service names go to the log, which is a different audience. "Something went wrong" fails
   this rule: it is human-readable, and it communicates neither fault nor next action.

`user-errors.md` asks every failure message what failed, whose problem it is, and whether
trying again helps. For an expired credential the last two answers are fixed: server-side, no.
