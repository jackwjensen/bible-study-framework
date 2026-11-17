---
description: A fresh clone builds and tests with nothing but what the repo itself declares
paths: ["**/README*", "**/.env.example", "**/docker-compose*.yml", "**/Dockerfile*", "**/pyproject.toml", "**/requirements*.txt", "**/package.json", "**/composer.json", "**/global.json", "**/.config/dotnet-tools.json", "**/Directory.Build.props", "**/.nvmrc", "**/.python-version", "**/.gitignore", ".github/workflows/*"]
---

# A repo is self-contained

**A fresh clone on a clean machine builds and passes its tests straight away**, with nothing
but the toolchain the repo itself declares (Jack, 2026-10-02). Running it may take **one
documented step** — creating `.env` from `.env.example` — because credentials are never
committed (`secrets.md`). Nothing else: no step from somebody's memory, nothing borrowed from
the developer's machine.

**Whatever the machine provides is an extra layer, never a dependency.** A global gitignore,
user-level git config, a globally installed tool, a PATH entry, an IDE setting: none of it
travels with the clone, so a repo that leans on it works for exactly one person and fails for
the next — usually without saying why, because the failure is in what is *absent*.

## What the repo carries

| Need | Where it lives in the repo |
|---|---|
| Toolchain version | `global.json`, `.python-version`, `.nvmrc`, `packageManager`, `require.php` |
| Repo-local tools and dev dependencies | `.config/dotnet-tools.json`, `requirements*.txt` or a `pyproject.toml` dev group, `devDependencies`, `require-dev` |
| The pack's own prerequisites | `engineering_standards/requirements.txt`, shipped with the pack |
| Ignore rules | the repo's own `.gitignore`, every `.env*` copy included and `!.env.example` re-admitted on purpose (`gitignore-missing` reads only that file) |
| Commit gate | `engineering_standards/hooks/` plus the bootstrap that points git at them |
| Configuration keys | `.env.example`: every key, empty values |
| The one documented step | the README: clone → `.env` → build → test → run, every command spelled out |

## CI is the proof — if it is honest

A CI runner *is* a fresh clone on a clean machine, so a green run is this rule's test —
**provided its setup installs only what the repo declares.** A version typed into a workflow
(`pip install "ruff==0.16.5"`, `npm i -g x`) is a dependency the repo does not carry: CI
passes, the next developer's machine does not, and the version now lives in more than one
place.
Install from the declaration instead — `pip install -r …`, `dotnet tool restore`,
`pnpm install --frozen-lockfile`. Not yet mechanical; a review question.

The incident (2026-10-02): the pack's CI installed ruff inline, so the pack looked
self-contained. InvoTrack's workflow, merged before that line existed, installed only pytest,
and three scanner tests asserted as if ruff were present — red for four days, deploy blocked.

## Tests are part of the build

They answer to the repo, not the machine — `testing.md` § "A test answers to the repo, not the
machine" has the specifics (declared tools, isolated git, loud fixtures).

## Check it with the machine's layer switched off

That layer hides exactly the failures this rule is about, so verifying with it on proves
nothing:

- `git -c core.excludesFile= check-ignore -v .env` must name the repo's own `.gitignore`. git
  reads `~/.config/git/ignore` even when nothing sets `core.excludesFile`, so the plain command
  can answer from the global file.
- Run the suite from an environment built only from the repo's declarations — a fresh venv,
  a container — not from the one you have been developing in.
- The real check, before calling a repo onboarded: clone into an empty directory, follow the
  README and nothing else, build, test.

Source of truth: engineering-standards/claude/rules/self-contained-repo.md
