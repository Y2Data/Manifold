# manifold-deck

A local router that classifies each prompt's complexity and sends it to the right Claude
model tier (haiku / sonnet / opus) — reusing your **existing `claude` CLI subscription
login**. No API keys, no BYOK, no third-party proxy in the loop.

Since it's subscription-billed, not pay-per-token, the value isn't "save money" — it's
**conserving your Sonnet/Opus usage quota** by keeping simple questions on Haiku.

## Setup

Requires the [`claude` CLI](https://docs.claude.com/en/docs/claude-code) (and optionally
[`codex`](https://github.com/openai/codex)) already installed and logged in — this project
never touches API keys, it just shells out to your existing subscription auth.

```bash
./manifold up
```

That's it — one command bootstraps deps (via [`uv`](https://docs.astral.sh/uv/)) and
starts the dashboard at `http://localhost:8080`. Run it again any time; it's idempotent.

<details>
<summary>Setting up via a coding agent instead</summary>

Paste this into Claude Code (or any coding agent) from a clone of this repo:

> Set up manifold-deck: confirm `claude` (and optionally `codex`) is installed and logged
> in, then run `./manifold up` and check `./manifold status` reports it running. Show me
> the dashboard URL when done.

</details>

## Using it

```bash
curl -X POST http://localhost:8080/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"auto","messages":[{"role":"user","content":"What is the capital of France?"}]}'
```

`model` can be `"auto"` (route by classifier), or a pinned model name (e.g. `"opus"`) to
skip classification entirely. `"backend":"codex"` in the request body forces Codex instead
of Claude (Codex execution exists in `backends.py`; it isn't in the default auto-routing
table yet since its model alias names haven't been verified live the way Claude's have).

Or just open `http://localhost:8080` for the dashboard — recent routing decisions, tier
breakdown, running cost estimate.

## How it works

- `app/routing/classifier.py` — cheap heuristic (word count, code blocks, keyword
  markers) sorts a prompt into SIMPLE / MEDIUM / COMPLEX. No embedding model, no network
  call, tune the thresholds once `app/store.py`'s logged history shows real usage patterns.
- `app/routing/backends.py` — runs `claude -p` (or `codex exec`) headlessly via subprocess,
  parses the JSON result.
- `app/routing/router.py` — ties classification to a model (`SIMPLE→haiku`,
  `MEDIUM→sonnet`, `COMPLEX→opus`), logs every decision to SQLite (`data/decisions.db`).
- `app/main.py` — `POST /v1/chat/completions` (OpenAI-shaped request/response, so any
  OpenAI-client-based tool can point at this as a drop-in), plus a dashboard at `/`.

Built from scratch — an earlier attempt glued together two existing OSS tools, but that
required per-token API keys and interactive credential wizards for auth your subscription
already handles for free. This just shells out to the already-authenticated `claude`/`codex`
CLIs directly (`claude -p ... --model <tier> --output-format json`, reusing OAuth/keychain
auth — never pass `--bare`, that flag forces API-key-only auth and defeats the point).

## Web research (optional)

A standalone service — separate process, opt-in dependencies — that gives the dashboard a
Perplexity-style "search the web, read pages, write a cited answer" mode, without touching
the `connections` table/router that the rest of manifold-deck uses for models. See
`browser_service/` for the code.

```bash
./manifold browser-bootstrap   # one-time: installs playwright + trafilatura + a real Chromium binary
./manifold browser-up          # starts it at http://localhost:8090
```

Then check the "web research" box next to the composer in the dashboard, or call it
directly:

```bash
curl -X POST http://localhost:8090/research \
  -H 'Content-Type: application/json' \
  -d '{"question":"What is the capital of France?"}'
```

How it works: `browser_service/search.py` scrapes DuckDuckGo's HTML results page (no search
API key needed — matches this project's "no new API keys" ethos, and works out of the box);
`fetch.py` tries a plain HTTP GET first and only escalates to a real headless Chromium
(`playwright`) when the page turns out to need JS rendering; `extract.py` pulls clean article
text out via `trafilatura`; `synthesize.py` writes the cited answer by reusing the same
subscription-authenticated `claude -p` call the classifier already uses internally — no
second API key, no second auth path.

**Honest caveat**: scraping DuckDuckGo's HTML endpoint is automated access to a service whose
own ToS doesn't explicitly permit it. It's meaningfully more tolerant of low-volume,
single-user, sequential traffic than Google (which CAPTCHA-gates non-browser traffic almost
immediately) — fine for a personal local research tool used at low volume, not something to
scale up or parallelize. If you outgrow that, point `browser_service/search.py` at a paid
search API instead (same function signature, no caller changes needed).

Deliberately out of scope for this pass (see `browser_service/` for where each would slot
in): clicking/filling forms or otherwise acting on pages, logging into sites, caching search
results, and multi-hop "search again based on what I found" loops. Today's loop is
search → read → cite, once, per question.

## Other commands

```bash
./manifold status          # is it running?
./manifold down             # stop it
./manifold dev              # run in foreground with live-reload, for development
./manifold test             # run the test suite
./manifold browser-status   # is the web research service running?
./manifold browser-down     # stop it
```
