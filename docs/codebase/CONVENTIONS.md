# Conventions

## 1) Naming Rules

| Item | Rule | Example | Evidence |
|---|---|---|---|
| Python files | lowercase snake-case module names | `backend/v2/persistence.py` | repository inventory |
| Python functions/methods | `snake_case` | `create_session` | `backend/v2/api.py:196` |
| Python classes/models | `PascalCase` | `SqliteStore`, `ErrorEnvelope` | `backend/v2/persistence.py:49`; `backend/v2/api.py:76` |
| Constants/env vars | `UPPER_SNAKE_CASE` | `DEFAULT_TURN_LIMIT`, `CUA_V2_DB_PATH` | `backend/engine/__init__.py`; `.env.example` |
| React components/types | `PascalCase` | `LivePage`, `Session` | `frontend/src/App.tsx`; `frontend/src/types.ts` |
| Hooks/functions/values | `use...` / camelCase | `useLiveStream`, `createSession` | `frontend/src/useLiveStream.ts`; `frontend/src/api.ts` |
| Tests | `test_*.py`, `*.test.ts(x)` | `tests/test_server.py`, `frontend/src/api.test.ts` | test inventory |

## 2) Formatting and Linting

### Python

- Ruff is the formatter and linter with a 100-character target, Python 3.12 syntax, import sorting, bugbear, async, security, upgrade, and Ruff-specific rules.
- mypy checks `backend`, `docker`, `scripts`, `tests`, and `evals`. Definitions must be typed; implicit optionals are disallowed; return/unreachable/unused-ignore diagnostics are enabled.
- First-party imports are grouped under `backend`. Runtime modules generally use future annotations, standard library, third-party, then project imports.
- Prefer dataclasses for small internal value objects and Pydantic models for external contracts.

### TypeScript and React

- TypeScript is strict with `noUncheckedIndexedAccess` and `exactOptionalPropertyTypes`; builds emit no JavaScript from `tsc`.
- ESLint applies recommended JavaScript/TypeScript, React Hooks, and React Refresh rules with zero warnings in CI.
- Components and types use `PascalCase`; hooks use `use...`; values and functions use camelCase.
- The current UI favors small local functions/components in `frontend/src/App.tsx` and keeps HTTP, WebSocket, and binary-protocol concerns in separate modules.

Run `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`, `npm --prefix frontend run lint`, and `npm --prefix frontend run typecheck`.

## 3) Import and Module Conventions

- Ruff/isort groups standard-library, third-party, then first-party imports; `backend` is the configured first-party root.
- Backend imports normally use absolute `backend.*` paths. Provider clients are re-exported from `backend.engine` where compatibility requires a public facade.
- Frontend source uses relative local imports (`./api`, `./types`, `./useLiveStream`, `./pricing`) and package imports for React/router/icons.
- Model/capability data belongs in JSON registries and external schemas belong in Pydantic/type modules rather than route-local duplicate constants.

### API and data contracts

- Python fields use snake_case; Pydantic aliases serialize v2 JSON as camelCase.
- Public enum values are upper-snake strings. v2 errors are `{error: {code, message, details, isRetryable, requestId}}`.
- Unknown request fields are forbidden. List endpoints use `data` plus a cursor where applicable.
- WebSocket control/lifecycle messages are JSON; preview images use the versioned binary CUAF envelope.
- Model and route capabilities belong in `backend/models/*.json`; the supported direct catalog is intentionally limited to GPT-5.6 Luna, GPT-5.6 Terra, Claude Sonnet 5, Gemini 3.7 Flash, and Gemini 3.5 Flash-Lite.

## 4) Error and Logging Conventions

### Errors

- Translate expected input/domain failures into explicit HTTP status codes and stable error codes at the API boundary.
- Preserve uncertainty after an OS action: do not retry/fail over when execution may already have happened.
- Cleanup uses `finally`; cancellation is handled separately from ordinary exceptions.
- External-service failures are logged with context and mapped to bounded user-facing messages rather than leaking credentials or raw internals.

### Logging and observability

- Use module loggers from Python `logging`; avoid printing operational data.
- `configure_logging` honors `LOG_LEVEL` and `LOG_FORMAT=console|json` and is idempotent.
- A context-variable filter attaches a shortened session ID. JSON logs contain timestamp, level, logger, message, session ID, and optional exception data.
- Trace payloads redact API-key-shaped strings, replace screenshot blobs with hashes/lengths, cap recursion, and truncate long text.

## 5) Testing Conventions

- Python tests use pytest functions/classes, fixtures, `TestClient`, monkeypatch/mocks, and async tests under `asyncio_mode=auto`.
- Live SDK checks use the `integration` marker and are excluded by default.
- Frontend tests are colocated as `*.test.ts(x)` and use Vitest/Testing Library.
- Behavior changes are expected to update both tests and documentation; focused checks are preferred before the full matrix.

### Change discipline documented by the repository

- Keep pull requests focused and reviewable; avoid unrelated refactors.
- Keep code and documentation synchronized and never commit secrets.
- Install from lockfiles in CI and local verification (`uv sync --frozen`, `npm ci`).

## 6) Evidence

- `pyproject.toml:35-67` - pytest, Ruff, import sorting, and mypy rules.
- `frontend/tsconfig.json:1-10` and `frontend/eslint.config.js:1-27` - frontend strictness and lint rules.
- `backend/v2/models.py` and `backend/v2/api.py:76-87` - aliasing and error-envelope conventions.
- `backend/v2/api.py:252-281` - uncertain-action and cancellation/error handling.
- `backend/infra/observability.py:39-139,307-335` - structured logging, correlation, and redaction.
- `tests/test_server.py:15-19`, `tests/integration/test_gemini_live_sdk.py`, and `frontend/src/*.test.ts(x)` - test patterns.
- `CONTRIBUTING.md:1-34` - contribution and change-scope expectations.
