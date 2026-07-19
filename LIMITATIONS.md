# How this will mislead you

Read this before using the framework, and again after a few weeks of using it — the second reading lands
differently, because by then you will have seen these happen.

None of what follows is a bug that will be fixed in a later model. These are **structural tendencies** of
AI assistants doing this kind of work. The framework does not remove them. It makes them visible.

---

## 1. It omits the texts that complicate its answer

An assistant optimizes for an answer that hangs together. Coherence is served by *not* reaching for the
verse that breaks it — so it doesn't reach. Nothing is decided, nothing is concealed; the awkward text
simply never surfaces, because the answer already works without it.

This is the most common failure in biblical work and the hardest to notice, because **what's missing
leaves no trace in the output.** A confident, well-cited, internally consistent answer looks identical
whether or not three passages cutting the other way were passed over.

**What the framework does about it:** every finding requires a *"Texts that cut against this"* section,
and it may not be empty. "None found" is admissible only with a statement of where you looked. A blank
section is visible to a reader who cannot evaluate a word of Hebrew.

**What you must do about it:** ask, every time — *what would someone who disagrees cite?* If the
assistant can't produce the strongest opposing text, it hasn't finished the work.

## 2. It applies asymmetric caution on contested questions

On socially or politically charged topics, an assistant hedges harder in one direction. In practice:

- readings that feel uncomfortable get extra caveats; comfortable ones don't
- contested-but-agreeable positions are presented as more settled than they are
- traditional positions are presented as more contested than they are
- "scholars debate this" appears asymmetrically, attached to one side

The problem is not that it has a lean. **The problem is that the lean is unannounced**, so risk
management arrives dressed as exegesis. From the outside it is indistinguishable from a careful reading
of the text, which is exactly what makes it corrosive in study.

**What you must do about it:** on any question where a conclusion could offend somebody, assume the
scales are tipped and ask for the case you are *not* being given. Ask directly: *"What would you be
saying here if no one could take offence?"* The answer is often noticeably different, and the difference
is the measurement.

## 3. It systematizes

Given a few data points, an assistant will build a tidy structure — and it builds hardest when the
structure serves a conclusion already in motion. A narrative detail in one passage becomes a general
principle. A sequence in one episode becomes a rule about how God works.

Scripture frequently underdetermines the questions we bring to it. An assistant is poorly equipped to
leave a question open, because open questions read as unhelpful answers.

**What the framework does about it:** `claim_type: open` and the `threads/` directory exist so that "not
settled" is a first-class outcome with a place to live, rather than a gap the assistant feels pressure to
fill.

**What you must do about it:** when a structure appears suddenly tidy, ask which verse it rests on and
whether that verse is making a general statement or reporting one occasion.

## 4. It agrees too easily, and it flatters

An assistant will find merit in your idea because it is *your* idea. It softens real objections to avoid
friction. Agreement from it is close to worthless as evidence — it is a report about conversational
dynamics, not about the text.

**What you must do about it:** use the adversarial mode deliberately (see [`MODES.md`](MODES.md)), and
treat any conclusion that never met resistance as untested. If it agreed immediately, you learned nothing.

## 5. It states things from memory with total confidence

Verse references, lexical claims, what a translation says, whether a word is rare — an assistant will
produce all of these fluently and sometimes wrongly, with no change in tone between the ones it knows and
the ones it reconstructed.

**What you must do about it:** check specific factual claims against an actual text — a printed Bible, a
lexicon, a translation-comparison site. Especially any claim of the form "no translation says X" or "this
word occurs only twice." Those are checkable in a minute and wrong often enough to matter.

## 6. It resolves contradictions by splitting word senses

Scripture genuinely does use key words in several senses — *nomos* covers the Pentateuch, the whole Old
Testament, the Sinai code, and "any governing principle"; "evil" collapses two distinct Hebrew senses;
"burden" renders two different Greek words four verses apart. Spotting this is one of the things an
assistant is genuinely good at, which is exactly what makes the failure hard to catch.

The same move applied without limit will dissolve **any** contradiction. There is always a
plausible-sounding sense in which the two verses were never talking about the same thing.

**What the framework does about it:** the corrective is a question you can hold the assistant to —
**does the text mark the split, or does the reader?** Scripture marks its own splits often enough to set
a standard: Romans 9:6 ("they are not all Israel, which are of Israel"), John 11:11-14 ("Lazarus
sleepeth" → "Jesus said plainly, Lazarus is dead"), 1 Samuel 15:29 ("he is not a *man*"), Proverbs
26:4-5's two *lest*-clauses. Where the text supplies no key, the split is a proposal and takes
`claim_type: open`.

**What you must do about it:** ask where the distinction came from, every time — and watch the hit rate.
**A method that resolves everything it touches has stopped being exegesis.** If nothing ever comes back
"no split; the tension is real," the tool is relieving discomfort rather than reading.

## 7. It supplies tone the text does not carry

A written text carries *sense*. It does not carry *force*. Tone, irony, warmth, sarcasm and register are
borne by voice, face and relationship; they rest on **convention rather than grammar**, and they do not
survive into writing. There is no rule to recover them from.

An assistant will nonetheless tell you how a line sounded — fluently, and without hedging — because a
reading feels incomplete without it, and because it has absorbed a great deal of commentary that does
the same.

Scripture supplies tone only where the **narrator** does, and it does so often enough to show the
difference: Luke 18:9 ("which trusted in themselves ... and despised others"), John 6:6 ("this he said
to prove him"), John 12:6 ("not that he cared for the poor"), John 11:51, Luke 9:33. Where that track is
silent the tone is **unrecoverable** — Matthew 15:26 ("the children's bread ... unto dogs") and John 2:4
("Woman, what have I to do with thee?") both read as harsh in English, and neither text says how they
were said.

**What you must do about it:** prefer **distribution** to tone. That "Lord, Lord" occurs five times
(Matthew 7:21, 7:22, 25:11; Luke 6:46, 13:25) and **every occurrence is a rejection scene** is
checkable, and it holds without anyone having to hear how it was said. Where an argument genuinely needs
a tone, mark it `open` and name whose ear supplied it — usually your own culture's.

*6 and 7 are also written up, as instructions rather than warnings, under
[Reading discipline](MODES.md#reading-discipline--two-things-the-reader-supplies-without-noticing) in
`MODES.md`. Unlike 1-5 they are failures the **reader** commits too, which is why they appear in both
places.*

---

## The one that doesn't have a fix

Every mitigation above assumes **an attentive reader who pushes back.** That reader is the actual safety
mechanism. The modes, the required sections, the marking — all of it works by making failures visible
*to someone looking*.

Which means the framework transfers a method, not a guarantee. Used by someone who takes the output as
authoritative, it produces confident, well-formatted, citation-bearing error — and the format makes it
*more* persuasive, not less. That is a real risk and it is worth stating plainly:

> **A well-cited answer from an AI assistant is not evidence that the answer is right. It is evidence
> that the assistant found citations that fit the answer it produced.**

If you are giving this to someone young, or to anyone inclined to trust a confident machine, give them
this file first and make sure they can name at least two of the failures above before they start.

## What it is genuinely good at

Not everything here is a warning. Used within its competence an assistant is very strong at:

- finding every occurrence of a word or phrase across the canon
- holding many passages in view at once and laying out how they relate
- surfacing a parallel or a cross-reference you would not have thought of
- organizing what you already believe into checkable, structured form
- arguing against a position on demand, harder than a friend usually will

Notice the pattern: it is reliable at **finding and organizing text**, and unreliable at **judging what
the text means**. The framework is built to keep it on the first side of that line, and to keep the
second side where it belongs — with you.
