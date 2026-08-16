# Changelog

All notable changes to this project are documented in this file.

## [Unreleased]

## [3.2.0] - 2026-08-16

### Added

- Live **Desktop** dropdown: `Sandbox (Docker)` (default) or
  `Native host`. Host runs Computer Use on the operator machine via
  `HostDesktopExecutor`. Sandbox path is unchanged. Host start skips
  the Docker ready gate. `"local"` is still rejected.
- Live **Session log** under the viewport, with Copy logs. Audit
  trail last-8 strip uses newest events (`events.slice(-8)`).
- Community wording across README, SUPPORT, CONTRIBUTING, and
  SECURITY: testing, bugs, ideas, and PRs are welcome. No donations,
  sponsorship, bounties, or paid support. There is no `FUNDING.yml`.

### Changed

- noVNC / x11vnc connect with no VNC password. `GET /api/v2/desktop`
  returns `/vnc/vnc.html?…&path=vnc/websockify` with no `password=`.
  `desktopViewerSrc()` strips leftover `password` / `token` query
  params. x11vnc starts with `-nopw`. `VNC_PASSWORD` is unused and is
  not passed into Compose.
- Provider web search planning fetches public pages through
  `uvx mcp-server-fetch` (`backend/infra/mcp_fetch.py`, override
  `CUA_MCP_FETCH_CMD`) instead of provider-native `web_search` /
  Google Search tools. Planner skips localhost, `.local`, and
  non-global IP literals (max 3 public `http(s)` URLs).
- `resolve_api_key()` reads `_USER_ENV` (process env snapshotted
  before dotenv) so a user/system `GOOGLE_API_KEY` wins over a
  repo-root `.env` value. Google alias remains `GEMINI_API_KEY`.
- Live tab defaults: model `gemini-3.7-flash`, `preferredRoute`
  `gemini-direct`, fallback `gemini-3.5-flash-lite@gemini-direct`.
- README, TECHNICAL, SECURITY, CONTRIBUTING, and CODE_OF_CONDUCT
  rewritten against the current tree (MCP fetch, `_USER_ENV`, Live
  Gemini defaults, no-VNC-password connect).

## [3.1.1] - 2026-08-16

### Added

- Root community docs for testing, support, and operator data
  responsibility (`TESTING.md`, `SUPPORT.md`, `DATA.md`) plus GitHub
  issue templates for bugs and feature/improvement ideas. No donations
  or paid-support path.
- README open-source notice: clones and local use welcome; operator
  owns all in-app data including PDF and other file uploads.
- Session cost tab at `/cost`. Estimates USD from recorded `EXECUTION`
  token totals (`GET /api/v2/analytics?sessionId=`) and list rates in
  `frontend/src/pricing.ts`. The live session object is lifted to the
  app shell so Cost can read the current run.
- FastAPI SPA fallback includes `/cost` (`_SPA_ROUTES` in
  `backend/server/__init__.py`).
- USAGE.md, TECHNICAL.md, SECURITY.md, CONTRIBUTING.md, and `docs/`
  rewritten against the current six-tab dashboard and start path.

### Changed

- Live tab `preferredRoute` defaults to `gemini-direct` (catalog first
  model is `gemini-3.7-flash`). At 3.1.0 this was an empty string and
  fell through to the model's first route.
- Operator docs no longer claim the dashboard lacks web search, file
  attachments, fallback routes, reasoning, or safety Approve/Deny —
  those controls shipped in 3.1.0 (`frontend/src/App.tsx`).
- Mission control sits in the left CONTROL sidebar. The Live main pane
  is only the noVNC viewport.

### Fixed

- Process-level `GOOGLE_API_KEY` wins over a repo-root `.env` value
  (`load_dotenv(..., override=False)`).

## [3.1.0] - 2026-08-16

### Added

- `run.cmd`, a single Windows file that installs missing host tools and
  project dependencies, then starts the workbench. Already-present
  tools, `cua-ubuntu:latest`, and a working Vite install are skipped.
- Live viewport is a noVNC iframe (`/vnc/vnc.html?path=vnc/websockify`)
  as soon as the sandbox is ready. You do not start a run to see XFCE.
  `/api/v2/ws/desktop` carries idle pipeline/safety events; it is not
  the desktop picture.
- Live session controls for optional fallback `model@route`, catalog
  reasoning effort, safety policy, provider web-search planning, and
  non-Gemini reference files, plus Approve/Deny on
  `POST /api/v2/sessions/{id}/safety-decisions`.

### Fixed

- The backend loads the repository-root `.env`, so `AGENT_SERVICE_TOKEN`
  matches the sandbox and screenshots no longer return `401`.
- Default CORS/WebSocket origins include `http://127.0.0.1:8100` so the
  production bundle can open the live stream.
- A failed screenshot keeps the v2 frame socket open and retries.
- `docker compose up --wait` now blocks until agent `/health` and
  noVNC `vnc.html` are up, so the dashboard is not opened mid-boot.
- noVNC connects through `/vnc/websockify` instead of the unproxied
  `/websockify` path that showed "Failed to connect to server".
- Screenshot capture retries agent-service disconnects during sandbox
  warmup before falling back to `docker exec`.

### Changed

- Replaced Gemini 3.6 Flash with Gemini 3.7 Flash as the default Google
  Computer Use model.
- Added Gemini 3.5 Flash-Lite as a second selectable Gemini Computer Use
  model on the existing `gemini-direct` route.
- Added GPT-5.6 Terra as a second selectable OpenAI Computer Use model on
  `openai-direct`. GPT-5.6 Luna remains the default OpenAI model on that
  route.
- OpenAI Computer Use now holds click/drag/move/scroll modifier `keys`,
  accepts drag paths as `{x,y}` objects or `[x,y]` pairs, and optionally
  restricts navigation with `CUA_ALLOWED_NAV_HOSTS`.
- Claude Computer Use accepts official `left_click`, holds click/scroll
  modifiers (`key` / `text`), accepts `scroll_direction`/`scroll_amount`,
  and sends X11 `display_number` from `DISPLAY`.

## [3.0.3] - 2026-08-13

### Fixed

- Patched FastAPI/Starlette, Pillow, pypdf, python-multipart, cryptography,
  and pytest so `pip-audit` is clean.
- The agent-finished broadcast test matches formatted source, so the
  Python 3.12–3.14 CI jobs pass.
- The sandbox image keeps the Node runtime but removes npm/corepack so
  Trivy is not blocked by unused CLI packages.

## [3.0.2] - 2026-08-13

### Fixed

- Python quality CI now passes Ruff and mypy on the current tree.
- Frontend ESLint no longer fails CI (`setState` in effects, unused
  imports, and async handler types).
- The sandbox image no longer installs the host workbench Python stack.
  Node in the image is 22 with a current npm, so the HIGH/CRITICAL Trivy
  scan no longer fails on unused FastAPI/Pillow/npm CLI packages.

## [3.0.1] - 2026-08-13

### Fixed

- Windows local launcher now waits for backend health before opening the
  dashboard, binds Vite to 127.0.0.1, starts Vite through Node instead of
  `npm.cmd`, and rebuilds esbuild during setup so the frontend starts
  reliably.

## [3.0.0] - 2026-08-12

### Added

- Exactly three provider-native Computer Use routes: GPT-5.6 Luna through
  OpenAI Responses, Claude Sonnet 5 through Anthropic Messages, and Gemini 3.6
  Flash through Google Interactions.
- Ephemeral API-key credential sessions for every provider and a state- and
  PKCE-bound Google OAuth flow. Credentials remain process-local and are never
  returned by the API.
- Optional `CUA_API_TOKEN` protection for sensitive REST operations,
  WebSockets, and noVNC, with `CUA_WS_TOKEN` retained as a deprecated fallback.
- Explicit safety policies and nonce-bound safety decisions, deterministic
  fallback routes, SQLite audit records, binary `CUAF` frame streaming, and
  bounded on-disk audit-frame retention.
- `START.bat`, which checks host prerequisites, installs missing project
  dependencies through `setup.bat`, starts the workbench, and opens the
  dashboard.
- A self-contained Zero to Hero handbook website with user, technical, and
  business learning tracks.
- A richer Ubuntu sandbox application set for desktop demos and evaluations.

### Changed

- The model catalog, UI, provider adapters, tests, and documentation now expose
  only the three supported direct routes. Removed cloud routes are no longer
  advertised as unavailable catalog entries.
- v1 REST and WebSocket endpoints remain available for compatibility and for
  features not yet exposed by the v2 dashboard; new typed contracts live under
  `/api/v2`.
- Provider-specific attachment validation now rejects incompatible route/file
  combinations before execution. Gemini Computer Use sessions reject reference
  files; OpenAI and Anthropic keep their documented provider-native flows.
- Anthropic computer-tool configuration is driven by model-registry metadata,
  and organization web-search readiness is probed and cached per API key.
- Gemini history pruning retains atomic tool-call/tool-response turns.
- Documentation now reflects the current launcher, credential, authentication,
  safety, retention, and provider behavior.

### Removed

- GPT-5.5 Pro, GPT-5.4 Nano, `computer-use-preview`, Gemini 2.5 Computer Use
  Preview, Gemini 3.x preview and Flash-Lite entries, retired Claude entries,
  OpenRouter, Azure OpenAI, Bedrock, and Vertex execution routes.
- Obsolete pricing, reasoning-default, and migration guidance tied to removed
  model identifiers.

### Security

- Non-loopback binding requires both explicit public-bind consent and a shared
  workbench token.
- Sensitive state-changing endpoints, interactive WebSockets, and the noVNC
  proxy share constant-time token validation.
- Provider secrets are redacted from responses and logs, kept out of SQLite,
  and removed when their process-local credential session expires.
- Ambiguous OS actions are not replayed automatically during provider failover.

## [2.0.0] - 2026-07-23

### Added

- Versioned Computer Use-only model catalog with transport-specific identifiers, coordinate spaces, limits, and lifecycle metadata.
- `/api/v2` sessions, credential sessions, provider readiness, workflows, analytics, cursor-paginated audit records, and structured errors.
- Deterministic primary/fallback routing, transient retry, per-route circuit breaking, and provider-neutral checkpoints.
- SQLite WAL session/action/event/metric persistence and bounded audit-frame retention.
- Coalesced frame capture, canonical full-frame inference input, ROI supplements, compressed previews, and binary `CUAF` WebSocket frames.
- Five-tab TypeScript dashboard for live execution, audit history, workflows, providers, and analytics.
- Locked uv project, Python 3.12-3.14 CI, strict frontend checks, dependency audits, image scanning, release packaging, deployment, migration, and rollback guides.

### Changed

- Production FastAPI serves the built React SPA when `frontend/dist` exists.
- Provider credentials use environment variables or non-persistent credential sessions with an eight-hour maximum lifetime.
- Provider selection is explicit and deterministic; there is no implicit price/latency router.
- Direct OpenAI, Anthropic, and Google routes execute. Azure OpenAI, Bedrock, Vertex Gemini, and Vertex Claude are visible but unavailable until verified execution bridges ship.

### Removed

- v1 request/WebSocket compatibility and inline `api_key` session payloads.
- OpenAI `computer-use-preview`, GPT-5.5 Pro, GPT-5.4 Nano, Gemini 2.5 Computer Use Preview, Gemini 3.5 Flash-Lite, and retired/non-CU Claude entries.
- OpenRouter integration; its documented API does not expose a vendor-native Computer Use protocol.

### Security

- Secrets are never persisted or returned, uncertain OS actions are not replayed during failover, and release CI blocks on high/critical dependency and image findings.
