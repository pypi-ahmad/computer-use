# Technical Architecture — current tree (v3.1.1 + Unreleased)

Grounded in the files named below. Package version is `3.1.1`
(`pyproject.toml`, `frontend/package.json`). Unreleased work in this
tree is listed in `CHANGELOG.md` under `## [Unreleased]`.

## Runtime boundary

The application is a local, single-operator modular monolith plus one
Docker sandbox. FastAPI runs as **one process** because the credential
vault, active execution handles, WebSocket subscribers, frame broker,
safety nonces, traces, and circuit breaker are process-local. SQLite
WAL holds durable v2 records. The sandbox is the only component allowed
to execute OS input.

### How the stack starts

On Windows, `run.cmd` is the one-file entry: it installs missing host
tools (uv, Python 3.12, Node.js LTS, Docker Desktop), copies
`.env.example` to `.env` if needed, fills empty `AGENT_SERVICE_TOKEN`
and `VNC_PASSWORD` (the password is unused by x11vnc; see Sandbox),
runs `uv sync --frozen`, installs frontend deps when Vite is missing,
builds `cua-ubuntu:latest` only if that image is absent, then starts
`dev.py --open-browser`. `START.bat` always runs
`setup.bat --bootstrap-only` first.

`dev.py` (also invoked by `dev.bat` / `dev.sh`):

1. Optionally clears listeners on `PORT` (default 8100) and 8505.
2. Runs `docker compose down` then
   `docker compose up -d --wait --wait-timeout 90`. Compose healthcheck
   requires both `http://127.0.0.1:9222/health` and
   `http://127.0.0.1:6080/vnc.html`.
3. Starts `python -m backend.main` and waits for `GET /api/health`.
4. Starts Vite on `127.0.0.1:8505` (Windows: `node …/vite/bin/vite.js`;
   elsewhere: `npm run dev`). `frontend/vite.config.ts` proxies `/api`,
   `/api/v2/ws` (WebSocket), and `/vnc` (HTTP + WebSocket) to
   `127.0.0.1:${PORT}`.
5. With `--open-browser`, opens `http://127.0.0.1:8505` once that URL
   responds.
6. On Ctrl+C, stops frontend, backend, then `docker compose down`.

`backend/main.py` enforces the public-bind guardrail, then runs Uvicorn
on `HOST`/`PORT` with WebSocket protocol pings disabled
(`ws_ping_interval=None`). `CUA_RELOAD=1` enables `--reload`; `DEBUG`
only changes log level.

`backend/infra/config.py` snapshots `GOOGLE_API_KEY`, `GEMINI_API_KEY`,
`OPENAI_API_KEY`, and `ANTHROPIC_API_KEY` from the process environment
into `_USER_ENV` **before** loading the **repository-root** `.env` with
`load_dotenv(..., override=False)`. `resolve_api_key()` prefers
`_USER_ENV` over a `.env` assignment, so a user-env `GOOGLE_API_KEY`
wins. Default CORS/WebSocket origins in `backend/server/__init__.py`
are localhost/127.0.0.1 on ports 5173, 3000, 8505, and 8100 (override
with `CORS_ORIGINS`).

Production-style: build `frontend/dist`, start the sandbox, run
`uv run python -m backend.main`, open `http://127.0.0.1:8100`.

## Operator surface

`frontend/src/App.tsx` is a six-route SPA. The shell owns
`liveSession` and `credentialSessionId` so leaving Live does not stop
a run. On `/`, Mission control is portaled into
`.sidebar-control` under the CONTROL nav. The main pane is the
viewport plus pipeline rail.

| Path | Tab |
|---|---|
| `/` | Live session — Mission control (sidebar), noVNC viewport, pipeline stages |
| `/audit` | Audit trail — SQLite actions and events |
| `/cost` | Session cost — list-rate USD from recorded tokens (`frontend/src/pricing.ts`) |
| `/workflows` | Workflow library — versioned step lists; **Use in live session** calls `POST /api/v2/workflows/{id}/compile` |
| `/providers` | Provider routes and ephemeral credentials |
| `/analytics` | Aggregate token/latency telemetry and retention prune |

Live defaults in `frontend/src/App.tsx`: model `gemini-3.7-flash`,
`preferredRoute` `gemini-direct`, fallback
`gemini-3.5-flash-lite@gemini-direct`. Start run posts `maxSteps: 50`.
`GET /api/v2/desktop` returns
`/vnc/vnc.html?autoconnect=1&reconnect=1&resize=scale&path=vnc/websockify`
with **no** `password` query (`backend/v2/api.py` `desktop()`).
`desktopViewerSrc()` in `frontend/src/api.ts` deletes `password` and
`token` query params, then sets `path` to `vnc/websockify` or
`vnc/websockify?token=…` when a workbench token is in sessionStorage.
`waitForNovnc()` probes `/vnc/vnc.html` until HTTP 200 (40 × 500 ms).
A bare `path=websockify` would hit unproxied
`ws://127.0.0.1:8505/websockify` and fail.

The sidebar **Stop app** button posts `POST /api/v2/system/shutdown`
(202, then SIGINT in a background task).

## Model catalog and request path

Canonical files: `backend/models/allowed_models.json` and
`backend/models/computer_use_models.v2.json` (`version` 2.1.0,
`verified_at` 2026-08-14). Five Computer Use models on three executable
routes:

| Logical ID | Display | Route | Transport | Reasoning in catalog |
|---|---|---|---|---|
| `gemini-3.7-flash` | Gemini 3.7 Flash | `gemini-direct` | `GEMINI_INTERACTIONS` | low / medium / high |
| `gemini-3.5-flash-lite` | Gemini 3.5 Flash-Lite | `gemini-direct` | `GEMINI_INTERACTIONS` | low / medium / high |
| `claude-sonnet-5` | Claude Sonnet 5 | `anthropic-direct` | `ANTHROPIC_MESSAGES` (`computer_20251124`) | none listed |
| `gpt-5.6-luna` | GPT-5.6 Luna | `openai-direct` | `OPENAI_RESPONSES` | none / low / medium / high / xhigh / max |
| `gpt-5.6-terra` | GPT-5.6 Terra | `openai-direct` | `OPENAI_RESPONSES` | none / low / medium / high / xhigh / max |

1. `POST /api/v2/sessions` validates the model, compatible primary
   route, ordered explicit fallbacks (max 10), attached files (max 20),
   and runtime options (`maxSteps` 1–200, default 50; `safetyPolicy`;
   `useBuiltinSearch`; `reasoningEffort`; `credentialSessionId`;
   `retainAuditFrames`; `executionTarget` `"docker"` or `"host"`, default `"docker"`).
2. The coordinator runs the primary route, then only the supplied
   fallbacks. It does not pick routes by cost or latency.
   `backend/v2/api.py` builds `RouteSpec(..., max_attempts=1)`.
   `backend/v2/routing.py` `CircuitBreaker` opens after 3 failures
   for 30 s.
3. `backend/engine/` talks to each vendor’s Computer Use protocol.
   Gemini continues with `previous_interaction_id`. Claude and OpenAI
   use their native computer tools. `backend/executor.py`
   `normalize_desktop_action()` maps Gemini 3.x names (`click`, `type`,
   `hotkey`, `press_key`, …) onto existing handlers. `reasoning_effort`
   is forwarded only for `OPENAI` and `GEMINI` families.
4. Safety policies `provider_default`, `confirm_mutating`, and
   `read_only` govern execution. Pending decisions carry a nonce and
   are answered at `POST /api/v2/sessions/{id}/safety-decisions`.
5. After the run, v2 writes an `EXECUTION` metric with
   `input_tokens` / `output_tokens` (`backend/v2/api.py`). Confirmed
   actions are journalled during the run when the event stream emits
   them. Ambiguous post-action failure is not replayed or failed over.
6. Optional `CUA_ALLOWED_NAV_HOSTS` restricts `navigate` / `open_url`
   hosts in `backend/executor.py`.

`useBuiltinSearch` is opt-in from the Live toggle. It does **not**
attach provider-native `web_search` / Google Search tools to the
Computer Use loop. `backend/engine/__init__.py` constructs CU clients
with `use_builtin_search=False` and sets `_planner_use_builtin_search`
separately. `backend/providers/_common.py`
`maybe_plan_with_web_search()` calls
`backend/providers/planner.py`: extract or ask for up to 3 public
`http(s)` URLs, fetch them via `backend/infra/mcp_fetch.py`
(`uvx mcp-server-fetch` unless `CUA_MCP_FETCH_CMD` is set).
`_is_public_http_url` skips non-`http(s)`, `localhost`, `*.local`,
hostnames without a dot, and non-global IP literals. Cap: 3 pages,
4000 characters. MCP spawn/fetch failure returns an empty page list
and the run continues computer-only. Host needs `uvx` on `PATH`.
Fetches run from the **host** process, not the sandbox.

File attachments (`.md`, `.txt`, `.pdf`, `.docx` via
`backend/files.py`) are validated for every selected route before
start. Gemini Computer Use sessions reject attachments (File Search
cannot combine with Computer Use here). The Live tab hides the file
input when the selected family is `GEMINI`.

## Frames, WebSockets, and noVNC

One `FrameBroker` (`backend/v2/frames.py`) coalesces screenshot
demand. The canonical PNG remains the model input. Browser previews
are WebP/JPEG `CUAF` frames (magic, version, codec, sequence, width,
height, timestamp) decoded in `frontend/src/protocol.ts`. The Live
viewport does **not** display CUAF frames; it is noVNC.

- Idle desktop: WebSocket `/api/v2/ws/desktop` (`DESKTOP_STREAM_ID`).
  Not retained as audit frames.
- Active run: WebSocket `/api/v2/ws/{session_id}` plus JSON lifecycle,
  safety, routing, and log events (`frontend/src/useLiveStream.ts`).
- Screenshot transport errors retry in `backend/executor.py` before
  `docker exec` fallback. A failed capture keeps the socket open.
- Slow clients keep only the latest pending preview.

noVNC HTTP is proxied at `GET /vnc/{path}` with allowlisted prefixes
and connect retries. WebSocket `/vnc/websockify` proxies RFB to
`ws://127.0.0.1:6080/websockify`, gated by the same Origin and
optional `token` checks as `/ws` (`CUA_API_TOKEN` / `CUA_WS_TOKEN`).

## Persistence, audit, cost, and retention

`CUA_V2_DB_PATH` defaults to `data/computer-use-v2.sqlite3`. Tables:
sessions, actions, events, metrics, workflow_versions. SQLite history
has **no automatic eviction**.

Audit image bytes live under `CUA_V2_FRAME_PATH` (default
`data/audit-frames`), referenced by hash. Default eviction: seven days
or one GiB. Sessions may set `retainAuditFrames: false`. Deleting a
session purges its frames.

v2 HTTP: per-session actions/events/metrics, `GET /api/v2/analytics`
(optional `sessionId` / `model` / `route`), diagnostics, ZIP export,
retention preview/prune.

The Session cost tab polls `GET /api/v2/analytics?sessionId=` every
2 s while the selected session is `RUNNING`. Estimate is
`tokens / 1_000_000 × list rate` in `frontend/src/pricing.ts`:

| Logical ID | Input / 1M USD | Output / 1M USD |
|---|---:|---:|
| `claude-sonnet-5` | 2.00 | 10.00 |
| `gemini-3.7-flash` | 0.75 | 3.75 |
| `gemini-3.5-flash-lite` | 0.30 | 2.50 |
| `gpt-5.6-luna` | 0.20 | 1.20 |
| `gpt-5.6-terra` | 2.00 | 12.00 |

Batch, prompt-cache, and Terra long-context doubling are **not**
applied; those meters are not stored. Cost is an estimate, not an
invoice. Totals appear after the `EXECUTION` metric is written.

Workflows are versioned step lists. Compile substitutes `${var}` and
returns instructions the dashboard pastes into the Live task box.

## Credentials and workbench authentication

Resolution for a v2 run (`backend/v2/api.py`): credential-session
secret if `credentialSessionId` is set; else `resolve_api_key()`.
`resolve_api_key()` in `backend/infra/config.py` order: UI key, then
`_USER_ENV` (Google: `GOOGLE_API_KEY` then `GEMINI_API_KEY`; also
`OPENAI_API_KEY` / `ANTHROPIC_API_KEY`), then values dotenv loaded
into the process. Responses never include secrets. Vault entries are
process-local and expire within eight hours (28_800 s).

Google OAuth: `POST /api/v2/credential-sessions/google/oauth/start`
issues state + PKCE; the callback exchanges the code and stores
refreshable credentials in the same vault. Needs
`GOOGLE_OAUTH_CLIENT_ID` and `GOOGLE_OAUTH_CLIENT_SECRET` (or
`GOOGLE_OAUTH_CLIENT_SECRET_FILE`). Optional: `GOOGLE_CLOUD_PROJECT`,
`CUA_GOOGLE_OAUTH_REDIRECT_URI`.

When `CUA_API_TOKEN` is set, `_guard_api` in
`backend/server/__init__.py` gates `/api/*` reads and writes except
the Google OAuth callback, plus `/ws`, `/api/v2/ws/*`, and
`/vnc/websockify` (`_ws_token_ok`). HTTP: `X-CUA-Token` or `?token=`.
Browser WS and noVNC: `token` query on `/vnc/websockify`.
`CUA_WS_TOKEN` is a deprecated fallback. Loopback with no token is
default-open. The OAuth callback is bound to short-lived state + PKCE.

Non-loopback `HOST` requires `CUA_ALLOW_PUBLIC_BIND=1` **and**
`CUA_API_TOKEN` (or `CUA_WS_TOKEN`); otherwise `backend/main.py`
exits 2.

`AGENT_SERVICE_TOKEN` is required between the backend and
`docker/agent_service.py` (`X-Agent-Token`). Compose requires it.
x11vnc starts with `-nopw` (`docker/entrypoint.sh`). `VNC_PASSWORD`
is not passed into the container and is not put on the viewer URL.

## Sandbox

Compose service `cua-environment` (`cua-ubuntu:latest`):

- Loopback publishes `5900` (x11vnc), `6080` (websockify/noVNC),
  `9222` (agent service).
- `cap_drop: ALL`, `no-new-privileges`, pids 256, memory 4g, 2 CPUs,
  tmpfs `/tmp` and `/var/run`.
- `docker/entrypoint.sh` starts DBus, Xvfb `:99`, XFCE, x11vnc
  (`-nopw`), websockify, then `exec`s `docker/agent_service.py`.
- Default geometry 1440×900 (`SCREEN_WIDTH` / `SCREEN_HEIGHT`).

`GET /api/health` is process liveness only (`{"status": "ok"}`).
`GET /api/ready` checks Docker daemon (`docker version`), at least one
provider env key (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`,
`GOOGLE_API_KEY`, `GEMINI_API_KEY`), and sandbox startability. Failure
is HTTP 503 + `reasons`. Compose healthcheck is **not** `/api/ready`;
it is in-container agent + noVNC as above.

## Production frontend

If `frontend/dist/index.html` exists, FastAPI mounts the bundle last
so API and WebSocket routes keep precedence. SPA fallback to
`index.html` is only for `GET`/`HEAD` of `/audit`, `/cost`,
`/workflows`, `/providers`, and `/analytics` (and those paths with a
trailing segment) — `_SPA_ROUTES` in `backend/server/__init__.py`.
Unknown paths, `/api/*`, `/vnc/*`, and `/ws` stay 404. A missing
bundle is non-fatal in development. `CUA_FRONTEND_DIST` overrides the
directory.

## Public contracts

Prefer `/api/v2`. v1 REST (`/api/agent/start`, `/api/screenshot`, …)
and `/ws` remain for compatibility. JSON is camelCase with upper-snake
event enums. Errors: `code`, `message`, `details`, `isRetryable`,
`requestId`. Lists use cursor pagination. `/api/v2/models` and
`/api/v2/provider-routes` expose catalog, readiness, auth mode, and
circuit state. Live OpenAPI: `/docs`. Operator examples: `USAGE.md`.

## Quality gates

`uv.lock` and `frontend/package-lock.json` are authoritative. CI
(`.github/workflows/ci.yml`) runs `uv sync --frozen` and `npm ci`,
then Ruff, format, mypy, `pip-audit`, pytest on Python 3.12–3.14 with
`--cov=backend --cov-fail-under=60`, offline evals, frontend
lint/typecheck/tests/build, `npm audit --audit-level=high`, sandbox
image build, and a blocking HIGH/CRITICAL Trivy scan
(`ignore-unfixed: true`). Live SDK tests are opt-in
(`pytest -m integration`); missing credentials are not a pass.

Contributor commands: `CONTRIBUTING.md`. Operator data ownership:
`DATA.md`. Document catalog: `README.md` § Documentation.
