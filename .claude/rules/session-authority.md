---
description: A signed claim is not a current fact — revalidate the authority a session carries, and harden the cookie carrying it
alwaysLoad: every app with a login carries authority in a session, and this failure is silent in every language and framework
---

# Session authority

Language-neutral, so this file has no `paths:` and always loads. The reasoning, the incident it
came from, and the per-framework detail live in
[engineering-standards/docs/session-authority.md](https://github.com/jackwjensen/engineering-standards/blob/master/docs/session-authority.md); this is the constraint.

**Authority in a session is a snapshot, and a snapshot goes stale.** A signature proves the
server said it, not that the server would still say it.

## The rules

1. **Revalidate on an interval.** Organisation or tenant membership, roles, permissions, plan
   tier and impersonation are re-read from the source of truth on a stated interval. Write the
   interval's justification beside it: it *is* your revocation window.
2. **Compare the relation, not existence.** "The user exists" and "the tenant exists" can both
   be true while "this user belongs to that tenant" is false. Same for roles.
3. **Do not trust the framework's session-invalidation mechanism to cover this.** ASP.NET's
   security stamp, Django's `get_session_auth_hash`, Devise's `authenticatable_salt` and
   Laravel's password rehash all key on the **credential**. A role change or an org move does
   not touch them.
4. **Revalidate on the connection's lifecycle too, not just the request's.** In Blazor Server,
   LiveView, Livewire and Hotwire a per-connection service lives for the connection — hours —
   and the connection issues almost no HTTP requests, so request-pipeline checks never fire for
   the longest-lived sessions.
5. **On mismatch, end the session.** Do not patch cached state in place; you will not find every
   place authority was captured. Dropping the connection disposes that scope wholesale.
6. **One rule, one home.** The door and the interval call the *same* predicate, extracted pure
   and tested there. Two copies do not fail loudly when they drift — one just stops refusing.
7. **Authorization fails closed.** A lookup that finds nothing is not permission. Beware
   nullable comparisons that invert this: a `bool?` compared against `false` is `false` when the
   row is missing, so the code reads as a check and behaves as an allow.
8. **Never put authority where the client can write it.** Not `localStorage`, not a hidden
   field, not a query string. An id in a path is acceptable only as a *routing* key with
   something else proving the caller — and that proof must be bound to the id in the path.
9. **Never bind a session to the address it was issued from.** It ejects every user whose
   network changes mid-session — a phone moving between wifi and mobile data, a corporate
   proxy pool egressing from a different address per request — while doing nothing about a
   thief on the victim's own network, which is the likeliest way a session is taken. Record
   the address (below) and alert on it; do not enforce on it.
10. **A privileged field is never client-settable.** Role, admin flag, status, plan tier,
    balance and owner id are set by the server, never bound from the payload. Allow-list the
    fields a caller may set; never mass-assign the whole body or use the blanket opt-out
    (`$guarded = []`, an unfiltered `load($_POST)`). An external audit made a working admin by
    posting `role=Admin` to open registration — rule 8 in disguise, the client writing authority
    into a model instead of a cookie.

## The client's address is not `REMOTE_ADDR`, and not the first forwarded entry

Rate limits, audit trails, abuse detection and geo all key on it, and **every naive reading is
wrong behind a proxy** — silently, with a plausible value, on every request. All four have
shipped here; incidents in [engineering-standards/docs/session-authority.md](https://github.com/jackwjensen/engineering-standards/blob/master/docs/session-authority.md).
`REMOTE_ADDR` is the nearest proxy. The leftmost `X-Forwarded-For` entry is written by the
caller, because each hop *appends*. The first *public* entry is the CDN edge, shared by
everyone behind it. A CDN header read unconditionally is forgeable by anyone reaching the
origin another way — a header cannot attest to its own provenance.

**Walk the chain from the right, over hops the deployment DECLARES.** One algorithm everywhere,
parameterised by the only thing that differs between deployments — which hops are ours:

1. Build the chain: the `X-Forwarded-For` entries, then **append the connection's peer address**
   as the final element — the one value nobody can forge, so the chain ends in a fact.
2. Walk **right to left**, stepping over entries matching this deployment's **declared trusted
   hops**: the proxy, load-balancer or CDN ranges it sits behind, in the app's own config.
3. **Stop at the first entry that is not trusted. That is the caller.** Everything to its left
   is what the caller sent — so never the leftmost entry, nor any fixed index from that end:
   the caller chooses how many entries precede theirs.
4. **Empty trusted set → the peer address is returned**, straight out of step 1, and correct
   for a deployment with nothing in front.
5. **Every entry trusted** → the request came from your own infrastructure, a health check or
   an internal call. Return unknown, never the leftmost entry.

**Do not infer the trusted set from the addresses.** "Step over anything not globally routable"
is wrong wherever a proxy holds a public address — an Azure Application Gateway, an ALB, a WAF,
any CDN — where the walk stops on the proxy and calls it the visitor. Private-range hops are a
common case, never a definition. Validate each entry too: it becomes a rate-limit key.

**The chain earns the CDN header, then checks it.** Where the last hop stepped over is a CDN,
the walk has proved it adjacent — only then may its header be read (`CF-Connecting-IP`,
`X-Azure-ClientIP`, `CloudFront-Viewer-Address`, `Fastly-Client-IP`). A CDN *overwrites* its
own header, so a caller cannot seed it — but it cannot attest to that itself. Use both:

* **Agree** → return it, on two attestations. Compare *parsed addresses, never strings* — a
  bracketed, expanded or port-carrying IPv6 is one address, and string equality drops every
  healthy IPv6 request into the unknown bucket.
* **Chain ran out, CDN crossed** → the header is the answer, recovering a hop that *overwrites*
  `X-Forwarded-For` instead of appending (`infrastructure.md`).
* **Disagree** → unknown, and alarm; nearly always a misconfigured hop of your own.
* **No CDN crossed** → do not read it at all.

**Never read the CDN header first and unconditionally.** It makes the rest of the resolver
unreachable in exactly the case that code exists for, leaving no second layer — only a comment
claiming one. Trust inherited from a firewall is real but belongs to *one* network; it is not
a property of the code and does not travel with the repo. Keep the firewall regardless: it is
the stronger control, and the walk is what stands up the day it is wrong.

**Return unknown rather than a guess — and do not block on that alone.** Bucket unidentifiable
callers together: they throttle each other instead of passing free. Never reject merely because
no trusted hop was found — development has no proxy, health checks come from inside, and a
deployment with nothing in front is correct; a CDN range you forgot to list would then turn a
stale config file into an outage, where distrusting only shares a bucket.

**You get the nearest address your infrastructure can attest to — not a person.** A VPN, Tor exit, CGNAT and corporate NAT *are* the origin; you meter a shared location.

The two wrong readings are mechanical — `client-address` covers Python, C#, Razor, PHP and the
script suffixes, so do not check them by eye. Only the *wrong* answers are decidable; which
header is right depends on the edge in front of you. The one legitimate peer read — the last
fallback inside the single resolver — takes a line exemption, not a redesign. **That resolver
carries a test** (`client-address-untested`): the scanner guards the spelling, only a test
guards the answer. Cover a forged leftmost entry, a wholly internal chain, and an unvouched
CDN header.

## Every layer stands alone: an outer gate is never a reason to leave the inner one open

*(Jack, 2026-08-30. A rejected premise, not a new detail.)*

**If an endpoint or feature exposes sensitive data, it carries its own authorisation — whatever
sits in front of it.** A firewall, proxy, `.htaccess`, VPN or blanket fallback policy is an
*additional* layer; none makes the code behind it safe. The question is never "can anyone reach
this today?" but **"what does this expose the day the outer layer is wrong?"**

**A blanket policy inside the application is the same rule, not a milder one.** An authorization
fallback protects only what routes through it, and even where it covers, the endpoint still
states its own — a protection the code does not state is one nobody can review and nobody
notices losing. Keep the fallback; treat a surface declaring nothing as a defect.

Judge by what the response *contains*, not where it sits — and "not sensitive" is a real answer.
Where trust genuinely is inherited from infrastructure (`CF-Connecting-IP` above), write that
next to the code that relies on it. Incident, evidence and the review test:
[engineering-standards/docs/session-authority.md](https://github.com/jackwjensen/engineering-standards/blob/master/docs/session-authority.md).

## The session cookie

- `HttpOnly`, stated rather than inherited.
- `Secure` unconditionally outside local development — never "same as request", which makes the
  flag depend on one forwarded header (see `infrastructure.md` for the proxy half).
- The `__Host-` name prefix where the platform allows it: the browser then *rejects* a cookie
  that is not `Secure`, `Path=/` and `Domain`-less, so a regression breaks login loudly instead
  of shipping an insecure cookie silently. Adopting it signs everyone out once.
- `SameSite`: choose and say why. `Lax` is the safe default. **`Strict` is correct for an
  SPA whose shell is static** — the shell renders without a cookie and the app's own
  same-origin XHR carries it afterwards, so invite and email links work normally; the
  "`Strict` breaks email links" warning is about *server-rendered* pages, which need the
  cookie on the cross-site navigation itself. Check which kind you have rather than
  reaching for `Lax` reflexively (measured on `allegro-it-services`, 2026-08-27).
- Set the flags **explicitly on every call**. A framework's session-cookie settings govern
  its own session cookie, never cookies you set yourself — Django's `set_cookie` defaults to
  `secure=False` with no `SameSite`, so a hand-rolled auth cookie ships unprotected while the
  settings file appears to say otherwise.
- Set the lifetime deliberately; framework defaults run to weeks.

This is not a licence to re-query on every access check — one indexed read per interval per
session.
