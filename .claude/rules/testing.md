---
description: The testing dimension — what must carry a test, and what makes one worth having
paths: ["**/test_*.py", "**/*_test.py", "**/tests/**", "**/*.test.ts", "**/*.test.tsx", "**/*.test.js", "**/*.spec.ts", "**/*.spec.tsx", "**/*.spec.js", "**/*Test.cs", "**/*Tests.cs", "**/test-*.php", "**/*Test.php"]
---

# Testing: the second dimension

Code, tests, documentation. `documentation.md` opens by calling the first two gated; until
2026-09-01 that was false — nothing in the pack touched tests. This file and
`standards_tests.py` are the correction.

## The rule, stated once

**Every source module carries a test that names it.** Mechanical: `tests-uncovered-module`,
whole-tree, no grandfathering. A module nothing exercises is a module whose behaviour nobody
has stated, so every change to it is unreviewable against intent and every regression in it
is silent.

**Adoption relief is the baseline's job, never the rule's.** This is the decision that
shapes everything below (Jack, 2026-09-01). The first draft was a change-ratchet — a branch
that touches source must touch a test — designed so it would produce no findings on day one.
That is how a requirement becomes decorative: every escape built into a rule is an escape
somebody takes, and a rule nobody ever has to satisfy is not a rule. So the rule fires on
everything, and a repo that wants a ramp takes a baseline, visibly and on purpose.

The corollary matters as much: **an exemption is not a ramp**. `standards: tests-uncovered-module
exempt -- <why>` means "there is nothing here to run" — a declarative table, a config
fragment this repo has no runtime for. It never means "not yet".

## What counts as covered, and its honest limit

The test corpus must name the module — by its stem (`test_content.py` covers `content.py`),
or by a symbol it defines. That is the `docs-uncovered-command` bargain: **naming is a weak
proxy for exercising**, and it is the strongest thing a scanner can decide without running
the suite. It catches a module nothing has ever looked at. It cannot tell a thorough suite
from a shallow one, and it never will — that half is yours and `/code-review`'s.

## A test that cannot fail is worse than no test

Three mechanical rules, at the commit stage where the test is being written:
`test-without-assertion`, `test-always-passes`, `test-silently-skipped`. Each catches a test
that reports coverage while checking nothing — which is worse than an absent test, because
an absent test does not also report the module as working.

The judgment the scanner cannot make:

- **Assert on behaviour, not on the mock.** `expect(client.send).toHaveBeenCalledTimes(3)`
  tests the test double. Assert what the code produced, and reach for a mock only where the
  real collaborator cannot run.
- **One behaviour per test, named as the behaviour.** "and" in a test name means two tests.
  `test_rejects_empty_email` beats `test_validation`.
- **Watch it fail before you trust it.** A test written after the code passes immediately,
  and a test that has never failed is indistinguishable from one that cannot. This is the
  cheapest lie in a codebase to tell yourself.
- **A test with no assertion is sometimes right** — an import-does-not-explode smoke test, a
  migration that must simply run. Say so with the exemption marker rather than adding a
  decorative `assert True`, which is the same thing with the evidence removed.
- **Fix the code, not the test.** A test changed to match new behaviour is a specification
  change and should read like one in the diff; a test changed to stop failing is a defect
  being filed away.

## A module that cannot be imported cannot be tested

Both of these surfaced the first time allegroit-dk's modules were covered (2026-09-01), and
both are defects in the module, never problems for the test to work around:

- **A script must do nothing on import.** `require`-ing the mail-log pruner to reach its
  helpers *deleted files* and then `exit()`ed the requiring process, so the only safe way to
  test it was not to. Put the executable half behind an entry-point guard —
  `if (PHP_SAPI !== 'cli' || realpath($argv[0] ?? '') !== realpath(__FILE__)) return;`,
  `if __name__ == "__main__":` — and the helpers become reachable.
- **Locate a sibling relative to the FILE, not to a global root.** `require ROOT . '/lib/x'`
  means the module only loads when `ROOT` is the repo, so a test cannot point `ROOT` at a
  sandbox to catch what the module writes. `require __DIR__ . '/x'` loads anywhere. Keep the
  global for things that genuinely belong to the deployment (a log tree, a content
  directory); a sibling is not one.

## Assert on the artifact the defect would appear in

The most expensive wrong test is the one that fails against correct code, because it teaches
people the suite is unreliable. Two from the same afternoon: a header-injection case that
searched the mail *log* for a forged header, when the log is a receipt that echoes the
submitted text verbatim and the injection would land in the SMTP headers; and a case that
took "the newest log file by name" when the filename has one-second resolution plus a random
suffix, so several writes in the same second do not sort. Both looked like defects and were
not. Ask where the failure would actually be visible, and make each case produce exactly one
artifact so there is no ordering question to get wrong.

## A test answers to the repo, not the machine

The suite is part of the build, so `self-contained-repo.md` applies to it: green on a fresh
clone, whoever's machine that is. Two incidents from one day (2026-10-02), both red on one
machine and green on another:

- **A test that needs a tool says so.** Three gate tests ran a real ruff and asserted PASSED,
  so wherever ruff was absent they contradicted a gate that correctly reported SKIPPED. Where
  the repo *declares* the tool, its absence fails the test loudly; where it does not, the test
  skips with a named reason. Never assert as if it were installed.
- **A test that drives git isolates it** from user and system config — `GIT_CONFIG_GLOBAL`,
  `GIT_CONFIG_NOSYSTEM=1`, and an explicit `core.excludesFile`, because git reads
  `~/.config/git/ignore` even when nothing sets it. A developer's global `.env` ignore turned a
  "tracked .env" fixture into an untracked one. Give the code under test the same
  environment: if it asks git, isolating only the setup builds one repo and scans another.
- **A fixture step fails loudly** (`check=True`). A swallowed setup failure surfaces later as
  an unrelated assertion, which is what made the second incident read as a scanner bug.

## Before you refactor, characterise

A file with no test is not a reason to leave it alone — it is a reason to **add the
characterisation test first**, pinning what it does today, before changing what it does. That
is the same argument `refactoring.md` makes about splitting under a green suite, and it is
what makes a four-step move-then-clean refactor independently revertable.

## What a security-critical resolver owes

Where a function's wrong answers are *plausible* — a client-address resolver, a permission
check, a signature verifier — coverage is not the bar; the specific wrong answers are. For
the client's address that means a forged leftmost forwarded entry, a wholly internal chain,
and a CDN header arriving with no CDN hop to vouch for it. The incident that established
this is in [engineering-standards/docs/session-authority.md](https://github.com/jackwjensen/engineering-standards/blob/master/docs/session-authority.md): one resolver passed
the gate three separate times while bypassable, because the scanner can only see the shape.

Source of truth: engineering-standards/claude/rules/testing.md
