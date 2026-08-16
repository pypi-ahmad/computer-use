# Architecture

## 1) Architectural Style

The project is a local, single-operator modular monolith paired with one Docker sandbox. FastAPI hosts APIs, orchestration, in-memory runtime state, WebSockets, and the production React bundle in a single process. SQLite and the frame directory provide durable audit state; the sandbox is the only component intended to execute OS input.

The v2 layer is additive rather than a separate runtime: it validates and
persists richer v2 contracts, then bridges execution into the existing
`AgentLoop` and provider engines. Its catalog contains only GPT-5.6 Luna or
GPT-5.6 Terra via OpenAI Responses, Claude Sonnet 5 via Anthropic Messages,
and Gemini 3.7 Flash or Gemini 3.5 Flash-Lite via Google Interactions.

## 2) System Flow

```text
React dashboard -> FastAPI v2 contract -> SQLite session/coordinator -> AgentLoop/provider engine -> sandbox action service -> audit/status/WS output
```

1. The dashboard (Mission control in the CONTROL sidebar) posts a task, logical model, explicit route/fallbacks, step limit, and optional credential reference to `/api/v2/sessions`. Live defaults: model `gemini-3.7-flash`, route `gemini-direct`, fallback `gemini-3.5-flash-lite@gemini-direct`.
2. The v2 API validates the catalog selection, creates/starts a SQLite session, records an event, and launches a background coordinator.
3. The coordinator resolves credentials (`credentialSessionId` vault, else `resolve_api_key()` / `_USER_ENV`), including Google OAuth when selected, and dispatches through the v2 orchestrator into `AgentLoop` after sandbox readiness succeeds.
4. If `useBuiltinSearch` is on, `maybe_plan_with_web_search()` fetches up to 3 public URLs via `backend/infra/mcp_fetch.py` (`uvx mcp-server-fetch`) and prepends a text brief. `ComputerUseEngine` then constructs CU clients with `use_builtin_search=False`. The provider loop alternates inference with canonical actions sent by `DesktopExecutor` to the token-protected sandbox service. Safety prompts pause on an in-memory nonce handshake and the dashboard can approve or deny them.
5. v2 journals confirmed actions as `ACTION` events arrive, then writes metrics, events, audit frames, and terminal state; uncertain post-action failures are not replayed or failed over.
6. The API returns/query exposes durable audit state. The Live tab embeds noVNC at `/vnc/vnc.html?autoconnect=1&reconnect=1&resize=scale&path=vnc/websockify` (no `password=`; Vite/backend proxy to container websockify). It also opens `/api/v2/ws/desktop` immediately, then `/api/v2/ws/{session_id}` during a run. Those sockets send newest-only binary CUAF frames; the session socket also carries JSON control events. Capture failures retry without closing the socket.
7. The Session cost tab (`/cost`) estimates USD from SQLite `metrics` token totals (`GET /api/v2/analytics?sessionId=`) and the list rates in `frontend/src/pricing.ts`. It is not a provider invoice.

## 3) Layer/Module Responsibilities

| Layer or module | Owns | Must not own | Evidence |
|---|---|---|---|
| `backend/server` | HTTP/WS app, middleware, v1 API, v2 bridge | provider SDK protocol implementation | `backend/server/__init__.py` |
| `backend/v2/*` | v2 contract, routing, persistence, credential/frame state | OS input execution | `backend/v2/` |
| `backend/loop.py` | step-limited session lifecycle | HTTP response schemas | `backend/loop.py` |
| `backend/engine/*`, `providers/*` | provider requests, tool parsing, safety callbacks | FastAPI routing | `backend/engine/`; `providers/` |
| `backend/executor.py` | canonical action-to-service adapter | provider selection | `backend/executor.py` |
| `backend/infra/*` | config, Docker lifecycle, logs/traces | public v2 domain contracts | `backend/infra/` |
| `docker/agent_service.py` | allowlisted desktop/action execution | provider inference | `docker/agent_service.py` |
| `frontend/src/*` | operator UI, API/WS clients, CUAF decoding | credential persistence | `frontend/src/` |

## 4) Reused Patterns

| Pattern | Where found | Why it exists |
|---|---|---|
| Repository/store | `backend/v2/persistence.py` | isolate durable SQLite records |
| Adapter/facade | `ComputerUseEngine`, `DesktopExecutor` | normalize provider/action boundaries |
| Registry | `backend/models/*.json` | centralize model/capability validation |
| Singleton process state | v2 store/orchestrator/vault/breaker | coordinate one local operator process |
| Circuit breaker + ordered fallback | `backend/v2/routing.py`, `api.py` | bound route failures without dynamic vendor choice |
| Structured contract envelope | v2 models/API/frames | stable camelCase errors, pagination, WS/CUAF protocols |
| Fail-closed boundary | main guardrail, agent service token | keep OS-action surface local and explicit |

### Startup, shutdown, and scaling constraints

- Startup validates tool parity; failure is logged as a warning rather than aborting the process.
- Local development runs `docker compose up --wait` (agent `/health` and noVNC `vnc.html`), then waits for `GET /api/health` before starting Vite. Vite binds `127.0.0.1`; on Windows it is spawned through Node rather than `npm.cmd`.
- Shutdown cancels and awaits in-flight agent/broadcast/screenshot tasks, clears registries, and closes shared clients.
- Exactly one backend worker is required because credentials, active tasks, WebSocket clients, safety state, traces, and circuit state are process-local.
- The Docker desktop is a shared named container. The application is designed for a trusted workstation, not multi-tenant isolation.

## 5) Known Architectural Risks

- A crash between an executed desktop action and the next `ACTION` event can still omit that step from the SQLite action journal.
- `backend/server/__init__.py` combines app construction, security middleware, API endpoints, WebSockets, noVNC proxying, task registries, and execution bridging in one ~1,900-line module.
- Host-side MCP fetch (`mcp_fetch.py`) is URL fetch from the backend process, not the sandbox. DNS names with a dot pass `_is_public_http_url`; it is not a complete SSRF guarantee.
- The v2 surface depends on legacy `AgentLoop`, so changes to the original runtime can affect both API generations.
- Process-local state prevents horizontal scaling and is intentionally lost on restart.

## 6) Evidence

- `README.md` sections “Open source”, “Installation and setup”, “How it works”, and “Community”.
- `TECHNICAL.md` - stated runtime, request, frame, persistence, credential, and public-contract architecture.
- `backend/v2/api.py:195-285` - session validation, dispatch, fallback, and post-run persistence.
- `backend/server/__init__.py:79-145,1264-1300` - lifecycle and bridge into `AgentLoop`.
- `backend/engine/__init__.py:734-943` - provider selection, executor construction, and provider run dispatch.
- `backend/providers/_common.py:120-198` - event-stream bridge and safety callback propagation.
- `backend/v2/persistence.py:49-176` - SQLite WAL store and domain records.
- `docker-compose.yml:1-73` and `docker/agent_service.py:72-97` - sandbox topology and authentication boundary.
