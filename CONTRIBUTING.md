# Contributing

Thanks for looking. This project is happy to receive bug reports, feature requests, and improvements to
the framework itself.

Please read the scope section first — it will save you writing something that gets declined for reasons
that have nothing to do with its quality.

## Scope: what this repo is, and isn't

This repository is a **framework** — a method, a schema, and a small validator for keeping a biblical
study corpus. It ships deliberately empty of findings.

**Welcome:**

- Bug reports and fixes in `build_index.py` or `packs.py`
- Improvements to the validator: better error messages, checks that catch real corpus rot
- Documentation that is unclear, wrong, or missing a case
- Schema suggestions — a field, an edge type, or a status value that the current design can't express
- Portability work: making the tooling easier to run on a platform where it currently isn't
- Additions to [`LIMITATIONS.md`](LIMITATIONS.md), if you've observed an AI failure mode this doesn't
  already describe. These are especially valuable — the document is only as good as the failures it names

**Not accepted:**

- **Findings, or any theological content.** No doctrinal positions in the docs, no example findings
  asserting a reading, no changes to the canon default
- Content that argues for or against a theological position anywhere in the repo

That second list is a **scope decision, not a theological judgment.** The entire premise of the framework
is that a corpus belongs to the person who built it — that's what the `origin` axis exists to protect. A
shared repo of collective findings would be a different project, and a worse one, because nobody could
tell whose reading any given file represented.

If you want a corpus of your own findings, that's exactly the intended use: take this framework, start
your own repo, and fill `findings/` with your work. You don't need permission and you don't need to
contribute anything back.

## Reporting a bug

Open an issue with:

- what you ran, and what happened
- what you expected instead
- your Python version (`python --version`) and OS
- a minimal finding file that reproduces it, if the bug is in parsing or validation

Validator bugs are the highest-value reports here. If `--check` passes on a corpus that is actually
broken, that's a serious bug — the validator is the thing keeping a corpus honest over years.

## Requesting a feature

Open an issue describing the problem before the solution. "I couldn't express X about a finding" is more
useful than "add field Y" — the schema is deliberately small, and the answer is often that an existing
field or edge already covers it.

## Pull requests

1. Open an issue first for anything beyond a typo, so the approach can be agreed before you spend time.
2. Keep it to one concern per PR.
3. Run the validator before pushing:
   ```bash
   python build_index.py --check
   ```
4. **Do not commit `INDEX.md`.** It is generated. If your change affects it, say so in the PR and let it
   regenerate.
5. Match the surrounding style — the code is plain, dependency-free Python (standard library only),
   and the docs are written to be read start to finish rather than skimmed.

All pull requests are reviewed before merging. A decline is usually about scope (see above), not quality.

## Forking

You are free to fork this and maintain your own variant, publicly or privately, without asking. That's
how the framework is meant to spread — your corpus should be yours, not a branch of someone else's.

## Conduct

Disagreement about the framework is welcome. Argument about theology belongs somewhere other than this
issue tracker — not because the questions don't matter, but because this repo is about the method, and
keeping the two apart is what lets people with very different readings use the same tool.
