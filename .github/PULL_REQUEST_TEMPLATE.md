<!--
Thanks for contributing. A reminder on scope before you fill this in: this repo
accepts framework improvements — tooling, schema, docs — and does not accept
findings or theological content. That is a scope decision, not a judgement about
your view. See CONTRIBUTING.md.
-->

## What this changes

<!-- One or two sentences. What was wrong or missing, and what does this do about it? -->

## Why

<!-- The problem behind the change. If there's an issue, link it: Fixes #123 -->

## How it was verified

<!--
What did you actually run, and what did you see? "It should work" is not
verification. For validator changes, the useful evidence is a corpus (or a single
finding file) that behaved wrongly before and correctly after.
-->

```
$ python build_index.py --check

```

## Checklist

- [ ] `python build_index.py --check` passes
- [ ] `INDEX.md` is **not** committed in this PR — it is generated
- [ ] One concern per PR
- [ ] No findings or theological content added
- [ ] If this changes behaviour, the relevant docs are updated in the same commit

<!--
On that last point: docs and code drifting apart is how a project starts lying to
its users. Keeping them in one commit means the repo is never in a state where
they disagree.
-->
