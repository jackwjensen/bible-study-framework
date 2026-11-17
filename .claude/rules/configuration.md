---
description: Where a value that may change is allowed to live — constant, config, or a row
alwaysLoad: every language declares constants and every repo has a deployment, so the question is asked wherever a value is introduced rather than in a scopeable file type
---

# Values that may change

**Anything that may change should not be a constant** (Jack, 2026-09-07). A value's home is
chosen when it is introduced, not when it first needs changing:

| Home | When | A change costs |
|---|---|---|
| **Constant** | The app does not own it, **and** changing it breaks an external contract or corrupts data at rest | a migration either way |
| **Configuration** | Operational; differs per deployment; unlikely but possible | a restart |
| **Database row** | Someone will edit it, or it must be effective-dated | an edit |

**A screen is not the bar.** "Unlikely but possible" is fully answered by a config key or a
table row; build a UI only when someone other than a developer will do the editing.

The constant is the **narrowest** home, not the default — ask "can this value's owner ever want
it different without a deploy?", not "is this a magic number". Three shapes are mechanical
(`config-default-in-code`, `const-environment-literal`, `const-duplicated-literal`); yours is
the question itself, about a value whose shape gives nothing away.
Reasoning: [engineering-standards/docs/values-that-change.md](https://github.com/jackwjensen/engineering-standards/blob/master/docs/values-that-change.md).
