"""Standalone AI-browsing/research service for manifold-deck.

Runs as its own process (see scripts/browser-*.sh and `./manifold browser-up`),
deliberately decoupled from the main app's `connections` table/router.py —
it's callable over HTTP, not a `kind` the router dispatches to. See
browser_service/main.py for the request/response contract.
"""
