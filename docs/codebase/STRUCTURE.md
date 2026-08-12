# Repository Structure

## 1) Top-Level Map

| Path | Purpose | Evidence |
|---|---|---|
| `backend/` | Python app, providers, API, persistence | package inventory; `pyproject.toml:27-28` |
| `backend/engine/` | Provider-specific Computer Use SDK clients | `backend/engine/__init__.py:734-943` |
| `backend/infra/` | Configuration, Docker control, storage, observability | `backend/infra/` inventory |
| `backend/models/` | Contracts and model/capability registries | `backend/models/*.json`; `backend/models/schemas.py` |
| `backend/providers/` | Provider-owned run interfaces and planning adapters | `backend/providers/_common.py` |
| `backend/server/` | FastAPI app, legacy API, WS, frontend mount | `backend/server/__init__.py` |
| `backend/v2/` | v2 API, routing, credentials, frames, SQLite | `backend/v2/` inventory |
| `docker/` | Sandbox image, entrypoint, action service | `docker/Dockerfile`; `docker/agent_service.py` |
| `frontend/` | React/Vite dashboard and browser tests | `frontend/package.json`; `frontend/src/` |
| `tests/`, `evals/` | Python suites and offline evals | `pyproject.toml:33-40`; `evals/README.md` |
| `scripts/`, `docs/` | Build/watch tooling and operator docs | directory inventory |
| `.github/workflows/` | CI and release automation | `.github/workflows/ci.yml`; `.github/workflows/release.yml` |

## 2) Entry Points

- `backend/main.py` configures logging, enforces the public-bind guardrail, and launches `backend.server:app` with Uvicorn.
- `backend/server/__init__.py` constructs the FastAPI app, registers v1/v2 HTTP and WebSocket surfaces, bridges v2 sessions to `AgentLoop`, and optionally serves `frontend/dist`.
- `dev.py`, `dev.bat`, and `dev.sh` coordinate the local Docker sandbox, backend, and Vite development server.
- `frontend/src/main.tsx` mounts the React application; `frontend/src/App.tsx` owns the five routes/views.
- `docker/entrypoint.sh` starts the virtual desktop services and `docker/agent_service.py` inside the sandbox.
- `scripts/build_release.py` and `scripts/build_docs_site.py` are packaging/documentation entrypoints.

## 3) Module Boundaries

| Boundary | What belongs here | What must not be here |
|---|---|---|
| `backend/v2/` | v2 contracts, persistence, routing, credential/frame state | direct OS input execution |
| `backend/server/` | HTTP/WS transport, middleware, app lifecycle | provider SDK protocol details |
| `backend/loop.py` | session/step orchestration | provider-specific HTTP contracts |
| `backend/engine/`, `providers/` | provider tool loops and normalization | FastAPI response construction |
| `backend/executor.py` | canonical action-service adapter | model selection and API routing |
| `backend/infra/` | operational adapters/config/logging | public domain contracts |
| `docker/` | isolated desktop and allowlisted OS actions | backend/provider credentials beyond service token |
| `frontend/src/` | operator UI and browser protocols | secret persistence or provider SDK calls |

### Backend details

- `backend/v2/api.py` owns the public v2 HTTP contract, Google OAuth callback, safety decisions, audit export, retention controls, workflows, and background session coordination.
- `backend/v2/orchestrator.py` is a small bridge from the v2 API to a configured execution starter.
- `backend/loop.py` owns the legacy session loop used by both the original API and the v2 bridge.
- `backend/engine/` owns provider SDK conversation/tool loops; `backend/providers/` exposes the provider-neutral run contract around them.
- `backend/executor.py` translates canonical Computer Use actions into calls to the sandbox action service.
- `backend/v2/persistence.py` is the durable domain store; `backend/v2/credentials.py` intentionally remains process-local for API keys and Google OAuth; `backend/v2/retention.py` and the server frame broker handle live/audit images.
- `backend/infra/` contains operational adapters rather than domain/API contracts.

### Frontend details

- `frontend/src/api.ts` is the HTTP client boundary, including the session-scoped workbench token, and `frontend/src/useLiveStream.ts` is the WebSocket boundary.
- `frontend/src/protocol.ts` decodes the binary CUAF preview-frame protocol.
- `frontend/src/types.ts` describes API-facing frontend data.
- `frontend/src/App.tsx` currently contains routing and all five page components; visual styling is in `frontend/src/index.css` and `frontend/src/pages/Workbench.css`.

### Test layout

- `tests/engine/` isolates provider engine behavior.
- `tests/docker/` tests the sandbox action service without requiring a live container.
- `tests/integration/` contains opt-in live SDK tests.
- Root `tests/test_*.py` files cover API, v2 contracts, infrastructure, files, provider-run contracts, and regression fixes.
- `frontend/src/*.test.ts(x)` covers API, protocol, and dashboard behavior.
- `evals/` contains a separately invoked offline degraded-container-startup evaluation.

## 4) Naming and Organization Rules

- Python modules and functions use `snake_case`; classes and Pydantic models use `PascalCase`; constants use `UPPER_SNAKE_CASE`.
- React components and TypeScript types use `PascalCase`; hooks use the `use...` prefix; browser modules use short lower/camel-case filenames.
- Tests mirror the runtime area and use `test_*.py` or `*.test.ts(x)`.
- API JSON uses camelCase aliases and upper-snake enum values even though Python fields remain snake_case.
- Provider/model metadata lives in JSON registries under `backend/models/`; avoid duplicating it in route handlers.

## 5) Evidence

- `backend/main.py:1-96` - production backend entrypoint.
- `backend/server/__init__.py:79-148,1264-1300` - application lifecycle and v2 execution bridge.
- `backend/v2/api.py:195-285` and `backend/v2/orchestrator.py:10-56` - v2 session boundary and coordinator.
- `backend/engine/__init__.py:734-943` and `backend/executor.py:148-159,336-337` - engine facade and action boundary.
- `frontend/src/App.tsx:1-81`, `frontend/src/api.ts`, `frontend/src/protocol.ts`, and `frontend/src/useLiveStream.ts` - UI structure and client boundaries.
- `pyproject.toml:35-67` and `.github/workflows/ci.yml:32-72` - test discovery and validation boundaries.
- Repository file inventory under `backend/`, `docker/`, `frontend/src/`, `tests/`, `evals/`, and `scripts/`.
