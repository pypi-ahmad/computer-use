# Testing this workbench

Testers are welcome. Run the app on **your own machine** with **your
own API keys**. File what you find. Do not send money.

There is no automated end-to-end UI plus live-provider suite. CI and
local pytest/Vitest cover contracts offline. You verify a running
install by hand.

Engineering layout, markers, and mocks:
[docs/codebase/TESTING.md](docs/codebase/TESTING.md). How to file a
bug: [SUPPORT.md](SUPPORT.md). Data you use in the app is yours only:
[DATA.md](DATA.md).

## Manual smoke (running app)

1. Start with `run.cmd` (Windows) or `uv run python dev.py --open-browser`.
2. Open `http://127.0.0.1:8505` (use `127.0.0.1`, not `localhost`).
3. Confirm the six sidebar tabs: Live session, Audit trail, Session
   cost, Workflow library, Providers, Analytics.
4. On Live, Mission control is in the left CONTROL sidebar. The main
   pane should show the XFCE desktop in noVNC **without** starting a
   run. The header should read **Stream linked**. There is no VNC
   password.
5. **Session cost** (`/cost`) should show the list-rate table
   (Sonnet 5, Gemini Flash 3.7, Gemini 3.5 Flash Lite, GPT 5.6 Luna,
   GPT 5.6 Terra) even before a run.
6. On **Providers**, create a credential session for one route (API
   key, or Google OAuth), **or** set a process-level key
   (`GOOGLE_API_KEY` preferred for Gemini). Keys stay in the
   process-local vault or env. Prefer a test account.
7. On Live, defaults are `gemini-3.7-flash` / `gemini-direct` /
   fallback `gemini-3.5-flash-lite@gemini-direct`. Leave **Provider
   web search planning** off. Run:

   > Open the file manager. Stop when the file manager window is visible.

8. The viewport should show the file manager. The session badge should
   reach `COMPLETED`.
9. Open **Audit trail** and confirm the session, action journal, and
   events are listed.
10. Open **Session cost**, leave the current session selected, and
    confirm input/output token totals appear after the `EXECUTION`
    metric is written. Estimate = tokens / 1,000,000 × list rate.

If the viewport stays on **Connecting to sandbox**, follow
[USAGE.md troubleshooting](USAGE.md#viewport-says-connecting-to-sandbox-or-never-shows-a-desktop).
Do not treat a missing provider key as a product bug.

## Report what you find

- Defect: [Bug report](.github/ISSUE_TEMPLATE/bug.yml)
- Idea: [Feature or improvement](.github/ISSUE_TEMPLATE/feature.yml)
- How we take reports: [SUPPORT.md](SUPPORT.md)

Do not paste API keys, `AGENT_SERVICE_TOKEN`, `CUA_API_TOKEN`, or live
desktop screenshots. Do not attach real PDFs or other files that
contain secrets or personal data — you own that data ([DATA.md](DATA.md)).

## Offline automated checks

These match `.github/workflows/ci.yml`. They do not start Docker or
call a provider.

```powershell
uv sync --frozen
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest -p no:warnings --tb=short --cov=backend --cov-report=term-missing --cov-fail-under=60
uv run pytest -p no:warnings --tb=short -o addopts='' evals/
uv run pip-audit

npm --prefix frontend ci
npm --prefix frontend run lint
npm --prefix frontend run typecheck
npm --prefix frontend run test:run
npm --prefix frontend run build
npm --prefix frontend audit --audit-level=high
```

Focused planner/cost checks:

```powershell
uv run pytest tests/test_mcp_fetch.py tests/test_v2_platform.py --tb=short
npm --prefix frontend run test:run -- src/pricing.test.ts src/App.test.tsx
```

`evals/` is offline: Docker and provider keys are mocked. Example:
`evals/test_degraded_container_startup.py` asserts `POST /api/agent/start`
returns HTTP 409 when the agent is unready.

## Opt-in live tests

`tests/integration/test_gemini_live_sdk.py` needs a real Google key and
network. It is marked `integration` and is **not** part of default
pytest or CI. Missing credentials are a skip/disclosure, not a pass.

If you change the sandbox image or `docker-compose.yml`, also run
`docker compose build` locally. Image build and Trivy HIGH/CRITICAL
scan run in CI.

## What not to report as a test failure

- `HOST != 127.0.0.1` without both `CUA_ALLOW_PUBLIC_BIND=1` and
  `CUA_API_TOKEN` — the process is required to exit.
- Screenshot `401` when repo-root `.env` `AGENT_SERVICE_TOKEN` does not
  match the `cua-environment` container.
- A route that is configured but `OPEN` on the Providers circuit badge
  after repeated provider failures.
- Session cost showing **No token metrics yet** while a run is still
  `RUNNING` — totals are written with the `EXECUTION` metric after the
  run.
- A missing `GOOGLE_API_KEY` / `OPENAI_API_KEY` / `ANTHROPIC_API_KEY`.
