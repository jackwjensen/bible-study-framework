---
paths:
  - '**/docker-compose*.yml'
  - '**/docker-compose*.yaml'
  - '**/Dockerfile'
  - '**/.env.example'
  - '**/dev.sh'
  - '**/dev.bat'
description: Container, port and naming conventions shared by every Allegro IT stack
---

# Local infrastructure: ports, names, and why they are fixed

Every app here is a Docker Compose stack on one developer machine, and only one stack runs at
a time. That makes the host's port space and container namespace **shared state between
projects**, and shared state that each repo picks for itself drifts.

It already did. Two repos both published MySQL on `3306`, so they could not run at once and
either collided with a locally installed MySQL. Container names existed in production but not
in development, so the same service answered to `allegroit-dk-web-1` on one machine and
`allegroit-dk-web` on the server. When several sessions worked in parallel, containers and
ports moved under each other and the reported name stopped matching the running thing.

The fix is not cleverness. It is that these values are **assigned, not chosen**.

## The port registry — the feature defines the port

**One number per job, across every project.** The port is chosen by *what the thing is for*,
never by what happens to be running it: the app answers on `8080` whether it is PHP behind
nginx, Blazor, or a Vite dev server, and the admin surface is `8081` whether that is Django, a
second SPA, or a marketing site. That is what makes a browser bookmark survive switching
projects, and it is the whole point of the registry.

Host-side ports. The container side is whatever the image listens on and is nobody's business
outside the compose file — remap freely to hit the assigned host number.

| The feature | Host port | Bind |
|---|---|---|
| **Frontend / the app's main entry point** | `8080` | all interfaces |
| **Backend / admin / API / a second web surface** | `8081` | all interfaces |
| **Mail catcher — browsable inbox** | `1080` | **`127.0.0.1` only** |
| **Mail catcher — SMTP the app delivers to** | `1025` | **`127.0.0.1` only** |
| **The app's database**, whatever the engine | `3307` | **`127.0.0.1` only** |
| Cache / broker (Redis), *only if the host needs it* | `6380` | **`127.0.0.1` only** |
| Everything else | **not published** | — |

**Only one app runs at a time.** Running two simultaneously is explicitly *not* a supported
case, and these numbers deliberately do not try to make it one — that is the trade that buys
the memorability. When another project's container holds a port, check whether it is in use
before touching it (see below) — and never answer the collision by moving *your* port.

**8080 matters most.** It is where the app answers in every repo, and what the Cloudflare
tunnel (`localtest.allegroit.dk`) points at — so inbound webhooks reach whichever app is
currently running with no reconfiguration. An app that picks its own port silently breaks
every webhook test.

**The mail catcher is `1080`, and this reverses an earlier decision.** The convention was
briefly MailHog's own default of `8025`, chosen so a stack adding MailHog out of the box would
need no remapping. That optimised for the wrong thing: it made the port depend on *which tool*
caught the mail, when the whole registry is meant to depend on *what the port is for*.

**The mail catcher's tool is fixed too: Mailpit** (`axllent/mailpit` at an exact tag, never
`:latest`), published as `"127.0.0.1:1080:8025"` and `"127.0.0.1:1025:1025"`. Fixing only the
ports let each repo take whatever was current — MailCatcher, MailHog, Mailpit all ran at once.
MailHog has had no release since 2020; Mailpit is maintained and moves through the dependency
gate like any image. One tool also means one API for headless mail tests: Mailpit's
`/api/v1/messages`. Migrating off MailHog moves its `/api/v2/messages` readers too — the JSON
shape differs.

**The database is `3307` whatever the engine** — MySQL, PostgreSQL, anything. Same principle:
one saved connection in a DB client serves every project, and "3307" means "the app I am
working on" rather than "MySQL". Remap in the compose file (`"127.0.0.1:3307:5432"` for
PostgreSQL).

**Never `3306` (or `5432`), and the reason is measured rather than theoretical.** This machine
runs a standalone `MySQL80` service on `3360` *and* Herd's bundled MySQL on `3309` — both
already moved off the default by hand, which is how you can tell this has bitten before. `3306`
is free today only because two hand-edited configs say so, and a MySQL reinstall or a Herd
update resets either one back. Beyond the collision, a container on `3306` makes every saved
`localhost:3306` connection ambiguous: local server or container, depending on what is up.

**Loopback, and the long form.** Write `"127.0.0.1:3307:3306"`. The short `"3307:3306"` binds
every interface, which puts the database on the network for anyone on the same wifi.
Production republishes nothing at all — an exposed database port on a public host is the
single worst thing to leave lying around.

**Compose MERGES `ports`, so an overlay that omits the key republishes everything the base
file declared.** Only `ports: !override []` clears it, and it is needed on *every* service the
base file publishes, not just the datastore. InvoTrack reset `mysql`, forgot the app, and left
`0.0.0.0:8080` open on a public VPS — while `compose-port` stayed silent, because 8080 is a
perfectly legal *development* port and the rule had no opinion about which file it appeared in.
Enforced since 2026-08-18 by **`compose-production-ports`**, which reads the base file to learn
what needs clearing; the older rule had asserted this convention in a docstring for months
without checking it. Hetzner scans for open ports and can suspend a server over them, so this
is availability as much as security.

**Publish nothing you do not need to reach from the host.** Redis, queues and internal APIs
use `expose:` or nothing, and talk over the compose network by service name.

## Container names are assigned in the base file, not the overlay

```yaml
services:
  web:
    container_name: ${COMPOSE_PROJECT_NAME}-web
```

**Shape: `<COMPOSE_PROJECT_NAME>-<role>`.** Roles in use: `web`, `api`, `db`, `redis`,
`celery`, `worker`. A stack with two of a kind qualifies the role rather than numbering it —
`sourcetext-web` and `sourcetext-web-bible`, never `web-1` and `web-2`, because a number tells
you which one started first and nothing about which one it is.

**In `docker-compose.yml`, not only in `docker-compose.production.yml`.** Setting it in the
production overlay alone is the mistake three repos made: development then falls back to
Compose's generated `<project>-<service>-<N>`, so the name depends on the directory, the
service key and a replica counter. `docker logs allegroit-dk-web` works on the server and
fails on the laptop, and a runbook cannot name a container that only exists in one place.

`COMPOSE_PROJECT_NAME` is set in `.env` and is the site's identity, not the folder's —
`jackscorner-dk` for the repo `jacks_corner`, because the deployed thing is the site. **Give it a
default in the compose file** — `${COMPOSE_PROJECT_NAME:-invotrack}-web` — because a repo with no
`.env` in development expands the bare form to nothing and names the container `-web`.

### The project name gets the same fallback, as `name:` at the top of the base file

```yaml
name: ${COMPOSE_PROJECT_NAME:-allegroit-dk}
```

**Every `docker-compose.yml` opens with this, with the site's identity as the default.** The
container-name default above is not enough on its own: without a `.env`, Compose takes the
*folder* name as the project. A git worktree never has a `.env`, since it is gitignored, so it
became its own project (measured on allegroit-dk, 2026-10-22: project, image and network all
named `php-8-5-12-0f94b3` after the worktree folder). Its literal `container_name`s stayed the
same as the main checkout's. Compose then saw two different projects fighting over one container
name: `up` in one fails with `Conflict. The container name ... is already in use`, and `down` in
one does not see the other's containers.

The variable still wins wherever it is set, so the server's `.env` and a multi-instance repo
like WAPPIT (`wappit-api-staging`) behave exactly as before. The fallback covers only the
checkout that has no `.env`, and gives it the site's name instead of the folder's.

### The service name is on the shared proxy network too

A stack that joins `nginx-proxy-network` registers **both** its `container_name` and its *service
name* as DNS aliases there. So a service called `web` publishes the alias `web` onto a network
shared with every other site — and allegroit-dk, jacks_corner and wappit.net all have one.
Whichever answers is undefined.

Nginx Proxy Manager currently avoids this by targeting the container name
(`allegroit-dk-web:80`, `jackscorner-dk-web:80`), while pointing at the *service* name for the
stacks whose service happens to be unique (`invotrack:8080`, `donorlink:8080`). Both work; the
mix is what makes it fragile, because the two names are guaranteed identical only when someone
keeps them so.

**Name the service what you want the container called** — `sourcetext-web`, `sourcetext-api`,
`sourcetext-db` — so `container_name`, the service alias and the NPM target are one string with
no way to drift. That is what sourcetext.ai does, and it is why renaming a container there
cannot break routing.

Write `container_name` as a **literal**, not `${COMPOSE_PROJECT_NAME}-web`. The point is one
string; an interpolation is a second source of truth that a wrong `.env` can desynchronise from
the service key.

**Only proxy-facing services need the unified name.** The collision this fixes is on
`nginx-proxy-network`, so it applies to the service that joins it — the web one. A `mysql`,
`redis` or `mailpit` service lives on the project's own `default` network, which is per-project
and cannot collide, so it keeps its short generic key and only needs a distinct
`container_name` for `docker ps` to be readable. **Do not rename an internal service for
tidiness**: the service key *is* the DNS name its siblings connect to, so renaming `mysql` to
`donorlink-db` breaks every `Server=mysql;…` connection string in the stack for no benefit.

### Renaming a service breaks the next deploy unless you clear the old container first

Found by doing it. Compose identifies a container by its **service key**, so the moment the key
changes the running container becomes an *orphan* — Compose no longer recognises it, but Docker
still holds its `container_name`. The next `up` dies on:

```
Conflict. The container name "/jackscorner-dk-web" is already in use by container ...
```

**`--remove-orphans` does not save you.** Measured: `docker compose up -d --force-recreate
--remove-orphans` begins removing the orphan and then races its own create, failing with the
same conflict. The removal has to complete as its own step:

```bash
docker compose down --remove-orphans   # separate command, must finish first
docker compose up -d
```

That is a **one-time** step for the deploy that carries the rename, and it means brief downtime
— so it belongs in `PUBLISH_NOTES.md` rather than in the deploy workflow, which would otherwise
tear the site down on *every* future deploy.

### When one repo deploys as several instances, the names cannot unify — and should not

WAPPIT serves both `api.wappit.net` and `staging-api.wappit.net` from one repo, distinguished by
`COMPOSE_PROJECT_NAME` (`wappit-api` / `wappit-api-staging`). Its `container_name` is therefore
`${COMPOSE_PROJECT_NAME:-wappit-api}-api`, which differs per instance, while the service key is
a single static `api`. No one string can be all three.

That is correct, not a gap: the container name is the only one of the three that *can* carry the
instance, so **NPM must target the container name** — which it does. The rule is one string
wherever one string is possible; where a repo has more than one deployment, the interpolated
`container_name` is the discriminator and the service key stays generic.

## The two-file compose convention

`docker-compose.yml` is the development stack and is complete on its own.
`docker-compose.production.yml` is an **overlay** that changes only what production changes:
resets published ports (`ports: !override []`), joins `nginx-proxy-network`, and sets
production environment. `.env` carries
`COMPOSE_FILE=docker-compose.yml:docker-compose.production.yml` so a bare `docker compose`
command on the server picks up both.

The consequence to remember locally: on a machine whose `.env` sets that variable, plain
`docker compose up` brings up the **production** shape — no published ports, and a network
that does not exist locally. Override it (`COMPOSE_FILE=docker-compose.yml`) rather than
editing the file.

## `restart:` belongs in the production overlay, and on every service

**No restart policy in `docker-compose.yml`.** In development a self-resurrecting container is a
nuisance with teeth: it comes back after `docker compose down`, it survives a Docker Desktop
restart, and it holds `3307` against whichever project the developer switches to next — which
the port registry above makes a certainty rather than a risk, since only one stack runs at a
time.

**`restart: unless-stopped` in `docker-compose.production.yml`, on EVERY service.** A server
reboot or a single crash otherwise leaves the site down until a human notices.

**The failure mode to watch for is the asymmetric one.** In `ligelon-compliance` (found
2026-08-31) exactly one service — `mysql` — carried a policy, so the database kept returning
while the app and the mail catcher stayed down. That is worse than having none: the stack looks
partly alive, `docker compose ps` shows a running container, and the thing that is actually
missing is the one nobody checked. Set it on all of them or none of them.

Because the overlay may not exist yet when the base file is written, **say in the base file why
the key is absent** and put the requirement in `PUBLISH_NOTES.md` — otherwise the next person to
create the overlay has no way to know it was a decision rather than an omission.

## Logs go to `journald`, and that is an interim answer

**`logging: driver: journald` on every service, not `json-file`.** The reason is retention, not
capacity, and the failure it fixes had been invisible because `docker logs` kept working.

A `json-file` log lives under the **container's id**, in
`/var/lib/docker/containers/<id>/`. Every deploy runs `docker compose up --force-recreate`,
which creates a container with a *new* id, and the deploy script then prunes the old one "to
free disk space". **So each deploy destroys the previous container's entire log.** `docker
logs` shows you history no older than the last release, which is exactly the history you do
not need — the interesting question is always about something that happened before the change.

journald stores entries on the host, outside any container, indexed by fields rather than kept
as text. They survive recreates, restarts and reboots, and they are queryable rather than
greppable:

```bash
journalctl CONTAINER_NAME=invotrack-web -p err --since "2 days ago"
journalctl CONTAINER_NAME=invotrack-db -f
```

`docker logs <name>` still works — journald is one of the drivers Docker reads back from — so
nothing that used it before has to change.

**Retention moves from the service to the host, and the hazard moves with it.** Drop the
per-service `max-size`; it is journald's `SystemMaxUse` in `/etc/systemd/journald.conf` that
decides now. Two things follow. A **rebuilt server needs that set again**, and nothing in any
repo will remind you. And the default is **10% of the filesystem capped at 4G**, which is
smaller than it sounds once several stacks share it — the Allegro IT server's journal was
already at 3.9G of that ceiling before a single container was pointed at it, so eviction there
is brisk and the window is days rather than weeks. Check `journalctl --disk-usage` before
assuming a repo's logs will still be there.

**This is explicitly a stopgap.** One journal on one box still means SSH-only access, no
search across stacks, no alerting, and nothing left if the box is the thing that failed. The
real answer is a destination off the host — a self-hosted receiver (Seq, Loki + Grafana, or a
small ingest service of our own) or a hosted one. Until that exists, journald is the cheapest
thing that stops a deploy erasing the evidence. **Do not let its existence close the question.**

## Cross-project collisions: parked is yours to stop, in use is not

Only one Allegro IT app can hold `8080`. When another project's container holds a port you
need, first decide whether it is **parked** or **in use** — several sessions share one Docker
Desktop, and the container may be the one Jack is testing right now. (Decided 2026-09-28,
replacing "resolve them, don't ask", after an InvoTrack session found `payvisia-db` on `3307`
36 seconds after Jack had started it for a Payvisia test.)

- **In use → stop and wait for Jack's explicit go-ahead.** Do not stop, restart, recreate or
  `down` the other stack. Remove only your own half-created containers, say which container
  holds which port and why you judged it in use, and wait. A go-ahead covers that one
  collision, not the next.
- **Signals that it is in use** — any one is enough: `Up` for less than ~2 hours or recreated
  within that window; fresh request or query activity in `docker logs --since 30m`; another
  session working in that project; Jack has said so. **Unsure counts as in use**: a paused
  verification costs a message, a database pulled from under a live test costs the test.
- **Parked** (idle for hours, no recent log activity) → stop it, bring yours up, say so in the
  final summary, and bring the displaced one back if practical. No confirmation needed.

**Never work around a collision by changing ports — not "temporarily", not in an override
file, not with `-p`, not by editing `.env`.** A temporary remap is how a permanent one starts:
it gets committed with the fix, or survives in an untracked override that silently changes
the next run, and the registry above stops being true without anyone deciding it. The only
answers to a collision are the two above — stop a parked container, or wait.

## One port means one browser origin, so local apps share a cookie jar

The flip side of everyone using `8080`: browsers key cookies and `localStorage` by **origin** —
scheme, host and port — not by application. `http://localhost:8080` is therefore the *same*
origin for every app in the estate, and they share storage whether or not they know it.

What that produces, all of it local-only and none of it obvious:

- One app's `.AspNetCore.*` cookies overwrite another's, so signing into one can appear to sign
  you out of the next.
- `localStorage` keys collide. Two apps that both store `theme`, `currentTenant` or
  `entryProduct` read each other's values, and the one that wrote last wins.
- **DevTools shows you another app's state and gives no hint that it is not yours.** Seen on
  2026-08-27: an InvoTrack tab whose `localStorage` held a `currentTenant` key with
  base64 Relay ids in it. InvoTrack has no GraphQL and never writes that key — it was Teams
  Summary's, left behind on the shared origin, and it read exactly like an InvoTrack security
  finding until the keys were traced back.

Production is unaffected: the apps are on different domains there. So this is a debugging trap
rather than a vulnerability — but it is worth knowing before you reason about a value you found
in browser storage, and it is a reason to prefix `localStorage` keys per app and to give each
app a distinct auth-cookie name.

## Never `proxy_set_header X-Forwarded-Proto $scheme`

TLS terminates at Nginx Proxy Manager, which speaks plain HTTP to these containers, so
`$scheme` inside them is *always* `http`. Setting the header from it overwrites the one value
that knows better, and everything asking "is this really HTTPS?" is told no — session cookies
lose `Secure` in production, and generated absolute URLs advertise `http://`. Use
`$http_x_forwarded_proto`: nginx omits an empty header entirely, so a developer hitting the
port directly falls back to the request's own scheme, which is then the truth. Same reasoning
makes the host `$http_host` rather than `$host` — `$host` drops the port, and on
`localhost:8081` that is the difference between a working address and a dead one.

## Never strip `CF-Connecting-IP` on the way through

Same family as the rule above, and the same failure: a forwarded header that arrives correct
and is destroyed in transit. Behind Cloudflare, `CF-Connecting-IP` is the most precise client
address the application can get — `X-Forwarded-For` reaches the app as
`client, cloudflare-edge, npm`, where the middle entry is a *public* address that a
right-to-left scan stops at unless it recognises the edge. nginx forwards unknown headers
untouched, so this costs nothing to preserve and everything to clobber: do not
`proxy_set_header` it, and do not add a `proxy_pass_request_headers off`.

**`X-Forwarded-For` matters just as much, and it must APPEND.** Keep
`$proxy_add_x_forwarded_for` — it is not a development-only fallback. The application walks
that chain from the right on every request, and a hop configured to overwrite it with
`$remote_addr` throws away the entries that make it trustworthy. It is also what proves
Cloudflare is in front: the edge hop in the chain is what earns `CF-Connecting-IP` the right
to be read at all, so stripping or flattening the chain does not merely degrade the answer,
it stops the CDN header being believed.

The application half of this rule — the walk itself, and why the header is earned from the
chain rather than inherited from the firewall — is in `session-authority.md`.
