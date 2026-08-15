# Testing

## 1) Test Stack and Commands

- Backend: pytest 8.4, pytest-asyncio, pytest-cov, FastAPI/Starlette `TestClient`, and standard monkeypatch/mock facilities.
- Frontend: Vitest 3 with jsdom, Testing Library, user-event, and jest-dom matchers.
- Static gates: Ruff, Ruff format, mypy, TypeScript, and ESLint.
- Supply-chain/runtime gates: `pip-audit`, `npm audit --audit-level=high`, Docker build, and Trivy HIGH/CRITICAL scan.

```powershell
uv run pytest -p no:warnings --tb=short
uv run pytest -m integration --tb=short
uv run pytest -p no:warnings --tb=short -o addopts='' evals/
uv run pytest --cov=backend --cov-report=term-missing --cov-fail-under=60
npm --prefix frontend run test:run
```

## 2) Test Layout

- `tests/engine/` tests OpenAI, Anthropic, Gemini, and the engine facade.
- `tests/docker/test_agent_service.py` loads and tests the action service without requiring a running Docker container.
- `tests/test_server.py`, `tests/test_v2_platform.py`, and provider-run contract tests cover HTTP/WebSocket, OAuth/credential, safety, and v2 domain contracts.
- Other root tests cover files, infrastructure, models, executor boundaries, provider run contracts, regression fixes, and hot paths.
- `tests/test_windows_launcher.py` covers dashboard health probing, the Vite `127.0.0.1` bind, Windows Vite spawn through Node, and `setup.bat` installer/esbuild contracts.
- `tests/test_v2_platform.py` covers the idle `/api/v2/ws/desktop` stream (capture retry, no audit-frame retention).
- `tests/test_infra.py` asserts the repository-root `.env` path; `tests/test_audit_fixes.py` asserts `http://127.0.0.1:8100` is an allowed WebSocket origin.
- `tests/integration/test_gemini_live_sdk.py` is the opt-in live Gemini Interactions test.
- `evals/test_degraded_container_startup.py` tests a degraded startup scenario offline with Docker/provider boundaries mocked.
- `frontend/src/api.test.ts`, `frontend/src/App.test.tsx`, `frontend/src/protocol.test.ts`, `frontend/src/useLiveStream.test.ts`, and `frontend/src/pricing.test.ts` cover HTTP behavior, the six-tab dashboard, idle desktop stream, CUAF decoding, and session-cost list rates.
- `tests/test_v2_platform.py` also asserts Gemini 3.7 Flash / 3.5 Flash-Lite catalog entries and that `GOOGLE_API_KEY` in the process environment marks the Google route configured.

Setup is centralized in `tests/conftest.py`, `evals/conftest.py`, and `frontend/src/test/setup.ts`.

## 3) Test Scope Matrix

| Scope | Covered? | Typical target | Notes |
|---|---|---|---|
| Unit | yes | engine helpers, models, action service, protocols | mocked SDK/Docker boundaries |
| Integration/contract | yes | FastAPI, SQLite, provider-run/executor boundaries | mostly offline with TestClient/fakes |
| Live integration | opt-in | Google SDK transport | `integration` marker, key/network required |
| End-to-end UI + real sandbox/provider | [TODO] no automated suite identified | complete operator flow | manual smoke test documented in `USAGE.md` and `TESTING.md` |

### Default and CI behavior

- `pyproject.toml` discovers `tests/` and excludes `integration` by default; async mode is automatic.
- CI runs the backend suite on Python 3.12, 3.13, and 3.14 with backend coverage and a 60% failure threshold.
- Offline evals run separately with the default pytest `addopts` cleared.
- Frontend CI runs install, lint, typecheck, tests, build, audit, and uploads the built bundle.
- Live SDK tests require a real key/network and are intentionally not part of normal CI.

## 4) Mocking and Isolation Strategy

- API tests construct FastAPI `TestClient` instances around `backend.server.app`.
- Provider SDK calls, Docker state, action execution, and timing are replaced with fixtures, monkeypatches, or fake clients for deterministic unit/contract tests.
- The v2 store accepts `:memory:`/temporary SQLite paths for isolated persistence tests.
- Frontend tests replace network behavior at the API boundary and run under jsdom.
- Offline evals validate behavior at runtime boundaries rather than contacting providers or Docker.
- No recurring flaky test is documented. The principal instability boundary is intentionally excluded live SDK/network execution.

### Additional focused commands

```powershell
# Default backend suite
uv run pytest -p no:warnings --tb=short

# Focused contracts
uv run pytest tests/test_v2_platform.py --tb=short
uv run pytest tests/test_provider_run_contract.py --tb=short
uv run pytest tests/test_server.py --tb=short
uv run pytest tests/engine --tb=short
uv run pytest tests/docker/test_agent_service.py --tb=short

# Offline eval and opt-in integration
uv run pytest -p no:warnings --tb=short -o addopts='' evals/
uv run pytest -m integration --tb=short

# CI-equivalent coverage/static checks
uv run pytest -p no:warnings --tb=short --cov=backend --cov-report=term-missing --cov-fail-under=60
uv run ruff check .
uv run ruff format --check .
uv run mypy
npm --prefix frontend run lint
npm --prefix frontend run typecheck
npm --prefix frontend run test:run
npm --prefix frontend run build
```

## 5) Coverage and Quality Signals

- Backend CI enforces 60% line coverage for `backend`.
- The frontend defines a V8 coverage script, but CI does not enforce a frontend coverage threshold.
- [TODO] Record current measured backend/frontend coverage percentages when a baseline report is intentionally generated; this documentation pass did not run the full suites.

## 6) Evidence

- `pyproject.toml:23-67` - test dependencies, discovery, markers, and static-analysis rules.
- `.github/workflows/ci.yml:17-91` - Python matrix, coverage threshold, evals, frontend gates, audits, and Trivy.
- `frontend/package.json:6-35` - frontend test and coverage scripts/tooling.
- `tests/test_server.py:15-19`, `tests/test_v2_platform.py`, `tests/engine/`, and `tests/docker/test_agent_service.py` - representative test organization.
- `evals/README.md:1-17` and `evals/test_degraded_container_startup.py` - evaluation boundary and isolation.
- `USAGE.md:661-684` - documented local verification commands.
