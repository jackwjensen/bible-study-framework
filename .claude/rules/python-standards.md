---
paths:
  - "**/*.py"
description: Python/Django standards that ruff and flake8 cannot check
---

# Python / Django standards (judgment rules)

Ruff enforces the mechanical half: complexity (`C901`), too many branches/args/statements
(`PLR0912`/`PLR0913`/`PLR0915`), logging format (`G`), line length, and import hygiene.
File length and money-as-float come from `check-source-limits.py`. Don't re-check those by eye.

## The interpreter floor is enforced, and every declaration moves together

**`python-support` in `check-source-limits.py` enforces `PYTHON_FLOOR`** — the number and the
support dates behind it live in `engineering_standards/standards_versions.py` and
`engineering_standards/standards_python_support.py`, never here, so there is one place to change it.

The judgment part is what to do when it fires. It reads five sites — `.python-version`,
`python-version:` in CI, `FROM …python:`, `requires-python`, and the ruff/black/mypy target —
and **they are one change, not five.** `python-consistency` enforces exactly that: clearing the
floor on every line is not enough, the sites must also name the *same* version. A repo testing
on one minor and shipping on another is green right up until production, which is the whole
reason it is a rule and not a convention.

Watch the lint target in particular: set *ahead* of the interpreter it makes ruff accept and
suggest syntax that fails at runtime, silently, because ruff cannot know which interpreter
executes; set *behind*, it withholds the modern-syntax fixes these rules exist to apply.

`requires-python` is a **minimum, never a pin.** Write `>=` (or a range with an upper bound if
something genuinely breaks above it). `~=3.11.1` is not a minimum — it means `>=3.11.1,
==3.11.*`, which is a ceiling, and it is how a repo ends up unable to move at all.

Where a dependency genuinely has no build for the floor, exempt the line and name both the
blocker and what would unblock it:

```toml
# standards: python-support exempt -- ViennaRNA publishes no wheel for this floor yet;
# revisit when upstream builds one, tracked in PUBLISH_NOTES.md.
requires-python = ">=3.11"
```

## Exception handling: the same three layers as C#

1. **Entry points** — every view, DRF action, management command, Celery task, webhook
   receiver, and signal handler wraps its body in `try/except` and returns a human-readable
   error (or a proper HTTP status). Never let a raw traceback reach a user — nor `str(e)`:
   what the error must say is `user-errors.md` (enforced as `technical-error-shown`).
2. **External resource access** — every DB call that can fail, HTTP request, file operation,
   cache call, and third-party SDK call gets its own `try/except`. Rethrow or handle
   deliberately.
3. **Business logic in between** — no `try/except`. Let it propagate.

Never `except:` or `except Exception:` without re-raising or logging with `exc_info=True`.
Never use exceptions for flow control — check state first (`if not items:`, not
`try: items[0] except IndexError`).

## Money is `decimal.Decimal`

Never `float` for money. Model fields are `DecimalField(max_digits=..., decimal_places=2)`.
Serializers must not round-trip money through `float` — that silently reintroduces the bug
the model field prevented.

## Logging uses `%s` templates, never f-strings

`logger.info("Invoice %s created", invoice_id)` — not `logger.info(f"Invoice {id} created")`.
The template form defers formatting and keeps the message searchable as one event. Levels:
`debug` = internal diagnostics, `info` = business events, `warning` = recoverable,
`error` = needs attention. Never log passwords, tokens, or PII.

## Type hints at boundaries

Annotate public functions and anything crossing a system boundary. `Optional[T]` where a value
really can be absent. Validate at the edges (request data, external API responses, DB results);
trust internal contracts.

## Async naming

Async functions carry an `_async` suffix or live in a clearly async module — the calling
convention must be obvious at the call site.

## Unset is `None`, never a zero-like sentinel

**This rule is about the value, not the type: is there a value, or is there not?** If "no
value" is a legitimate state, the field is `Optional[T]` and unset is `None` — never `0`, `-1`,
`""`, or `date.min`. Each of those is also a real value, so once stored, "nobody chose" and
"somebody chose this" cannot be told apart.

```python
job.project_id = selected_project.id if selected_project else 0  # WRONG - dangling FK
tenant_id = current_tenant_id or 0  # WRONG - silent empty queryset
amount_minor = session.get("amount_subtotal") or 0  # WRONG - credits a wallet ZERO
payment_intent_id = charge.get("payment_intent") or ""  # WRONG - lookups silently miss
```

Beware `or` specifically: `count or 10` swallows a legitimate `0`. Use
`count if count is not None else 10`.

Django model fields follow the same rule — `null=True` on the column rather than a magic
default. (`null=True` on a `CharField` is the documented exception; use `blank=True` there.)

**Two kinds of value can never be zero-like**, and the scanner enforces exactly these:

| Kind | Forbidden | Why |
|---|---|---|
| Identity (`project_id`, `id`) | `0` and `""` | There is no record `0` and no record `""` |
| External reference (`stripe_reference`, `idempotency_key`) | `""` | The next `if reference:` takes the not-found branch |

Everything else may legitimately default — a display name, a subject, a description. There
"absent" and "empty" render identically and the distinction buys nothing.

**Money is deliberately NOT on that list.** A money amount can legitimately *be* zero — a
zero-amount Stripe charge used to verify a payment flow, an empty wallet, a sum over no rows —
so for money, `None` is usually the *wrong* model and `0` is the genuine domain default. Make
money columns non-nullable with a `0` default, and `?? 0` / `or 0` on a money value read from
your own objects is normal, correct code.

**The one exception — a genuine default.** Where the domain really has a default that applies
in the absence of a choice, assign it by **name**, never as a bare literal:
`status = chosen_status if chosen_status is not None else JobStatus.PLANNED`.

**Not covered: an aggregate over a possibly-empty set.** `sum(amounts, Decimal(0))` over an
empty list is genuinely zero — the additive identity, not a stand-in for "unknown".

**Not covered: normalise-then-guard.** Coalescing an *optional* value and branching on it
immediately is correct — the branch restores the distinction before anything is stored:

```python
declared_id = str(finding["meta"].get("id", "") or "").strip()
if declared_id != "" and declared_id != finding_id:  # right - it branches on absence
    errors.append(...)
```

A guard on the same name against the same zero-like value (or a truthiness test on it) makes
"nobody chose" and "somebody chose this" distinguishable again, so the scanner stays silent
when one appears within a few lines. **This exemption does not extend to the payload rule
below** — there the value can genuinely *be* zero, so `if amount == 0` cannot tell a missing
key from a real amount of nothing.

## Never default a field you read out of an external payload

Different rule, and the one that matters most in the webhook handlers. It is not about
null-versus-zero at all — it is about *where the data came from*:

```python
amount_minor = session.get("amount_subtotal") or 0  # WRONG - credits a wallet ZERO
```

`payload.get("x")` returning `None` means **the key was absent**, which is an API contract
surprise, not a value. Substituting anything — `0`, `None`, `""` — converts a broken
assumption into a confident number that looks entirely legitimate in the ledger afterwards.
And because a real amount *can* be `0`, the invented value and the true one become
indistinguishable: this is precisely why zero cannot double as the "key was missing" marker.

Two correct shapes:

```python
amount_minor = session["amount_subtotal"]  # required -> fail loudly if absent
if (amount_minor := session.get("amount_subtotal")) is None:
    logger.error("checkout %s has no amount_subtotal", session["id"])
    return EventDisposition.FAILED  # optional -> handle it explicitly
```

Note the inconsistency to watch for: code that indexes `session["id"]` on one line and
defaults `session.get("amount_subtotal")` on the next is already trusting the payload for the
id while papering over the field that carries the money.

`check-source-limits.py` flags this as `payload-default` for money-named fields read by a
**literal key** (`.get("x")`, `["x"]`, `getattr(o, "x", None)`). It cannot see attribute access
on an SDK object — `invoice.subtotal or 0` is the same bug and the scanner will not catch it, so
watch for it by eye in webhook and API-client code.

## Persisted dates declare a sensible range

Every stored date has a plausible window, enforced in two places. Business dates belong in
roughly `2000-01-01 … today + 1 year`; system timestamps in `2020-01-01 … now + 1 day`. A
field deliberately allowed to hold dates outside its window — historic archives, birth dates,
BC years — must say so in a comment on the field, so the width reads as a decision.

Enforce with a model `validators=[...]` **and** a `CheckConstraint` in `Meta.constraints`. The
validator alone is not enough: it never runs for `bulk_create`, `update()`, `loaddata`, a raw
SQL import, or a restored backup — which is how bad rows normally arrive. Only the database
constraint sees those.

Never store `date.min`/`datetime.max` to mean "not set" — that is the sentinel rule above in
another costume; use `None`. Using them as a non-stored comparison key (a sort key, a filter
bound) is fine.

## Django specifics

- Connections are per-request. Never store a connection or queryset on a class attribute.
- Migrations already applied to production are immutable history — fix forward with a new one.
  Whether a migration is *correct* is `migrations.md`: run them against the engine production
  uses and compare the result against the models, because a suite that builds its schema from
  the models has verified the models and nothing else.
- Query in the view/service layer, not in templates. No N+1: `select_related`/`prefetch_related`.
- Business logic belongs on the model or a service, never in a template or a serializer.

## Danish bookkeeping retention (5 years)

Financial records under 5 years old cannot be deleted while the organisation is active. Check
record age before any destructive operation; offer archival instead.
