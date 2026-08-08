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
| **Mail catcher — browsable inbox** | `1080` | all interfaces |
| **Mail catcher — SMTP the app delivers to** | `1025` | all interfaces |
| **The app's database**, whatever the engine | `3307` | **`127.0.0.1` only** |
| Cache / broker (Redis), *only if the host needs it* | `6380` | **`127.0.0.1` only** |
| Everything else | **not published** | — |

**Only one app runs at a time.** Running two simultaneously is explicitly *not* a supported
case, and these numbers deliberately do not try to make it one — that is the trade that buys
the memorability. When another project's container holds a port, stop it (see below).

**8080 matters most.** It is where the app answers in every repo, and what the Cloudflare
tunnel (`localtest.allegroit.dk`) points at — so inbound webhooks reach whichever app is
currently running with no reconfiguration. An app that picks its own port silently breaks
every webhook test.

**The mail catcher is `1080`, and this reverses an earlier decision.** The convention was
briefly MailHog's own default of `8025`, chosen so a stack adding MailHog out of the box would
need no remapping. That optimised for the wrong thing: it made the port depend on *which tool*
caught the mail, when the whole registry is meant to depend on *what the port is for*. MailHog
therefore publishes `"1080:8025"`. The SMTP side needs no thought — MailHog and MailCatcher
both listen on `1025`.

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
`redis` or `mailhog` service lives on the project's own `default` network, which is per-project
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

## Cross-project collisions are routine — resolve them, don't ask

Only one Allegro IT app can hold `8080`. If another project's container has it, stop that
container, bring yours up, and say so in the final summary. This is a standing decision and
does not need confirming each time. Bring the displaced one back if it is practical.

## Never `proxy_set_header X-Forwarded-Proto $scheme`

TLS terminates at Nginx Proxy Manager, which speaks plain HTTP to these containers, so
`$scheme` inside them is *always* `http`. Setting the header from it overwrites the one value
that knows better, and everything asking "is this really HTTPS?" is told no — session cookies
lose `Secure` in production, and generated absolute URLs advertise `http://`. Use
`$http_x_forwarded_proto`: nginx omits an empty header entirely, so a developer hitting the
port directly falls back to the request's own scheme, which is then the truth. Same reasoning
makes the host `$http_host` rather than `$host` — `$host` drops the port, and on
`localhost:8081` that is the difference between a working address and a dead one.
