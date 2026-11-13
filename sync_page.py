#!/usr/bin/env python3
"""
sync_page.py — the page the browser shows when it comes back from signing in.

The sign-in ends in the reader's browser, on a listener this computer runs on
127.0.0.1, so this page is the last thing they see of it. It says what actually
happened — signed in, not approved, or failed and why — because the listener
renders it only after the code has been exchanged and the sign-in stored.
Saying "signed in" before then would claim a success that can still fail.

Self-contained on purpose: no stylesheet, font or script from anywhere, since
the page is served by a listener that closes a moment later and must not make
the browser reach out to a third party. Server-neutral too — the framework
syncs with any server that speaks SYNC.md, so it carries no server's branding.

Standard library only.
"""

from __future__ import annotations

import html
from dataclasses import dataclass

TITLE = "Study sync"


@dataclass(frozen=True)
class Outcome:
    """What the sign-in came to, in words for the person at the browser."""

    ok: bool
    heading: str
    detail: str


def signed_in(server: str, client_name: str) -> Outcome:
    return Outcome(True, "You are signed in",
                   f"This computer now syncs with {server}, listed there as “{client_name}”. "
                   "You can close this tab and go back to the terminal.")


def not_approved(reason: str) -> Outcome:
    return Outcome(False, "Sign-in was not approved",
                   f"{reason} Nothing was stored. Run the sign-in again from the terminal "
                   "if you meant to approve it.")


def failed(reason: str) -> Outcome:
    return Outcome(False, "Sign-in did not complete",
                   f"{reason} Nothing was stored. The terminal says the same; run the "
                   "sign-in again from there.")


_STYLE = """
:root{color-scheme:light dark;--bg:#f6f4ef;--card:#fff;--text:#1d1b18;--muted:#5d5850;
--line:#e2ddd2;--ok:#1f7a4d;--bad:#a3392b}
@media (prefers-color-scheme:dark){:root{--bg:#141311;--card:#1d1b18;--text:#eeeae2;
--muted:#a9a397;--line:#34312b;--ok:#5cc28f;--bad:#e8806f}}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;display:grid;place-items:center;padding:24px 16px;
background:var(--bg);color:var(--text);font:16px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif}
main{width:100%;max-width:30rem;background:var(--card);border:1px solid var(--line);
border-radius:14px;padding:32px 28px}
.mark{width:44px;height:44px;border-radius:50%;display:grid;place-items:center;
font-size:22px;font-weight:700;color:var(--card);margin-bottom:18px}
.ok .mark{background:var(--ok)}.bad .mark{background:var(--bad)}
h1{font-size:1.35rem;line-height:1.3;margin:0 0 10px}
p{margin:0;color:var(--muted)}
footer{margin-top:24px;padding-top:14px;border-top:1px solid var(--line);font-size:.85rem;color:var(--muted)}
"""


def render(outcome: Outcome) -> bytes:
    """The whole page, as bytes ready to send. Every word from outside is escaped."""
    state = "ok" if outcome.ok else "bad"
    mark = "✓" if outcome.ok else "!"
    page = (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        "<meta name=\"referrer\" content=\"no-referrer\">"
        f"<title>{html.escape(outcome.heading)} — {TITLE}</title><style>{_STYLE}</style></head>"
        f"<body><main class=\"{state}\" role=\"status\">"
        f"<div class=\"mark\" aria-hidden=\"true\">{mark}</div>"
        f"<h1>{html.escape(outcome.heading)}</h1><p>{html.escape(outcome.detail)}</p>"
        f"<footer>{TITLE} · bible-study-framework</footer></main>"
        # The address bar still holds the one-time code; it is spent, but there is
        # no reason to leave it in the history either.
        "<script>history.replaceState(null,'',location.pathname)</script>"
        "</body></html>"
    )
    return page.encode("utf-8")
