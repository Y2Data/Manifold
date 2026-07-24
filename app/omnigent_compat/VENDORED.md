# Vendored: Omnigent web UI

`static/` is the compiled web UI bundle from [Omnigent](https://pypi.org/project/omnigent/)
(`omnigent==0.6.0`, by Databricks, Inc.), copied verbatim — unmodified — from:

```
<venv>/lib/python3.12/site-packages/omnigent/server/static/web-ui/
```

Licensed under the **Apache License, Version 2.0** — see `LICENSE` and `NOTICE` in this
directory, carried along per the license's redistribution terms. No source changes were
made to any file under `static/`.

This is a static frontend build only. Everything under `app/omnigent_compat/` *outside*
`static/` (`ids.py`, `mapping.py`, `routes_*.py`) is original manifold-deck code — a
compatibility layer translating manifold-deck's own data (projects, turns, connections)
into the REST/SSE contract this bundle expects, so it can run against manifold-deck's
backend instead of Omnigent's own server. Mounted at `/app`; the original manifold-deck
dashboard stays at `/`, unaffected.
