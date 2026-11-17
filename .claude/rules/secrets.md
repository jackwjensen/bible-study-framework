---
description: What may be committed, and what may not — the one rule with no exceptions
alwaysLoad: any file in any language can carry a credential into git, and the ones that do are config, fixtures and seeders rather than a scopeable file type
---

# Credentials: what may be committed, and what may not

One rule, and it has no exceptions worth the words: **a credential that works anywhere must
never reach git.** Not in production config, not in a fixture, not in a seeder, and not in a
development-only settings file. "Development-only" describes where it is *used*; it says
nothing about where it is *stored*, and git is forever in a way a dev environment is not.

*"It's only a dev password"* loses: it is a working credential, a repo's audience changes, and
git keeps it after you delete it. The reasoning and the incidents: [engineering-standards/docs/secrets.md](https://github.com/jackwjensen/engineering-standards/blob/master/docs/secrets.md).

## Where they may live instead

In rough order of preference. Pick the first that fits the stack.

1. **The framework's own secret store.** `dotnet user-secrets` (a `UserSecretsId` in the csproj,
   values under the user profile), Django's environment-backed settings, Laravel's `.env`. The
   value sits outside the repo while staying tied to the project, and no `.gitignore` line has
   to be right for it to stay out.
2. **A gitignored `.env`.** Fine, and the estate already relies on it — but only the repo's own
   `.gitignore` counts; a global ignore is a backstop that does not travel with the clone
   (`self-contained-repo.md`). Check with it off: `git -c core.excludesFile= check-ignore -v .env`.
   **Ignore every copy: `.env*`** (`gitignore-build-output` enforces it) — a copy is the same
   credential; on 2026-10-10 a `.env.bak-staging` sat unignored in a production checkout, one
   `git add -A` from a push. Admit the example on purpose with `!.env.example`. Never back up a
   `.env`: a fresh `.env.example` plus the secret store restores everything in it.
3. **Generated at first run and printed once** — for machine secrets only (a signing key, a
   token secret). **Never for an account password**: a printed password lands in the logs, and
   anyone who can read them signs in. Accounts follow the next section.

## What may be committed

- **Key names**, in `.env.example`, with empty values and a comment saying what each is for.
- **A pointer** to where the value lives — "see the identity notes", not the value.
- **A deliberately fake value that cannot work anywhere**, and only where a placeholder is
  structurally required. The design-time connection string in `migrations.md` is the worked
  example, and its lesson applies here: make it *obviously* fake, because a realistic-looking
  one invites somebody to "fix" it into a real credential.

## Seed test systems through an explicit local step — never through configuration

Test systems are seeded; the **mechanism** is the rule. A seeder, restore or app that reads an
account password from `.env`, a compose variable or an app setting is a standing "set a
password, get an admin" path on every machine that runs it — whoever can set that value gets
in (`seeded-credential-config` flags the key). Instead (snippets: `templates/seed-users/`):

- **A seed command with no values**: reads the accounts as JSON on **stdin**, **refuses unless
  the system is local** (guard in the command, not the caller), validates each password against
  the *real* validators (a direct hash skips them — a demo admin once held its own e-mail
  address as password), **hashes, never stores**, prints only names.
- **A PC-side wrapper** pipes the values file into it; on a server there is nothing to pipe.
- **Logins without the password**: a local-only, single-use, short-lived link — never typed into
  a browser through a tool call. **A live guard** fails if the test accounts exist in production.

## The first administrator of a fresh install has no password until they set one

**Create it passwordless and mail a single-use, expiring set-password link** (the framework's
reset token) to the configured address; nothing reusable reaches config, a log or the console.
Without working mail, **no fallback**: it stays passwordless until "forgot password" works —
and check mail can really leave (a "would send" stub reports success). Bootstrap only while no
administrator exists. Why, and the worked example: [engineering-standards/docs/secrets.md](https://github.com/jackwjensen/engineering-standards/blob/master/docs/secrets.md).

## The estate's standard local accounts

`localadmin@allegroit.dk` and `localuser@allegroit.dk` are seeded that way wherever a repo needs
a local login. **The addresses may be committed; the passwords may not**, not even as an empty
key: they are in `~/.claude/shared/dev-seed-users.secret.json`, piped unread, never read by Claude.

## A secret must never reach the client, git or no git

The rule above is about *history*; this is about *distribution*. A frontend build inlines its
`environment` / config object into the bundle it ships, so a secret placed there is served to
every visitor — moving it to a different frontend file changes nothing, the whole object is
compiled in. Keep server-side the keys a call *uses* (payment capture, mail send, OAuth
exchange); the browser holds only the deliberately-public half. An external audit found live
Paystack and Brevo *secret* keys in a production Angular bundle; rotating them without moving
the calls server-side just republished the new ones next deploy. `committed-credential` catches
a known secret *format* even in a `.ts`, but "this ships to the browser" it cannot judge.

## Checking

Before a commit that touches config, a seeder, a fixture or a compose file:

```bash
git diff --cached | grep -iE 'password|secret|api[_-]?key|token' 
```

A hit is not automatically wrong — a key *name* is fine — but every hit deserves the question.
A committed credential is rotated as part of the fix: history keeps it whatever the tree says.

Source of truth: engineering-standards/claude/rules/secrets.md
