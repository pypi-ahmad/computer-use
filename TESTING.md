# Testing this workbench

There is no automated end-to-end UI plus live-provider suite. CI and
local pytest/Vitest cover contracts offline. You verify a running
install by hand, as documented in [USAGE.md](USAGE.md).

Engineering layout, markers, and mocks live in
[docs/codebase/TESTING.md](docs/codebase/TESTING.md).

## Manual smoke (running app)

1. Start with `run.cmd` (Windows) or `uv run python dev.py --open-browser`.
2. Open `http://127.0.0.1:8505`.
3. Confirm the Live session viewport shows the XFCE desktop without
   starting a run, and the header reads **Stream linked**.
4. On the Providers tab, create a credential session for one route
   (API key, or Google OAuth). Keys stay in the process-local vault.
5. On Live session, run the local task from USAGE.md:

   > Open the file manager. Stop when the file manager window is visible.

6. The viewport should show the file manager. The session badge should
   reach `COMPLETED`.
7. Open Audit trail and confirm the session, action journal, and events
   are listed.

If the viewport stays on **Connecting to sandbox**, follow
[USAGE.md troubleshooting](USAGE.md#viewport-says-connecting-to-sandbox-or-never-shows-a-desktop).
Do not treat a missing provider key as a product bug.

Found a defect or a gap? Open a [bug report](.github/ISSUE_TEMPLATE/bug.yml)
or a [feature idea](.github/ISSUE_TEMPLATE/feature.yml). See
[SUPPORT.md](SUPPORT.md). Do not send money.

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
