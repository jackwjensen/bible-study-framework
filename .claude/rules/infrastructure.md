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
`jackscorner-dk` for the repo `jacks_corner`, because the deployed thing is the site.

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
