# Deployment Guide

v3.0.1 targets one trusted operator on a workstation. Do not expose it as a public multi-tenant service.

## Local development

On Windows, `START.bat` is the supported one-click path. It installs
dependencies, rebuilds esbuild, starts the stack, waits for `GET /api/health`,
and opens `http://127.0.0.1:8505`. Vite listens on IPv4 loopback during
development. The production-style path below still serves the built SPA from
FastAPI on port `8100`.

## Production-style local deployment

1. Install Docker Desktop, Node.js 22, and uv.
2. Copy `.env.example` to `.env`; set an OpenAI, Anthropic, or Google API
   key, or configure Google OAuth with `GOOGLE_OAUTH_CLIENT_ID` and
   `GOOGLE_OAUTH_CLIENT_SECRET`. Set strong `AGENT_SERVICE_TOKEN` and
   `VNC_PASSWORD` values.
3. Install locked dependencies: `uv sync --frozen` and `npm --prefix frontend ci`.
4. Build the dashboard: `npm --prefix frontend run build`.
5. Start the sandbox: `docker compose up -d --build`.
6. Start FastAPI: `uv run --frozen python -m backend.main`.
7. Open `http://127.0.0.1:8100`. FastAPI serves `frontend/dist`; `/docs` exposes OpenAPI.

Use one backend worker. Multi-worker execution splits in-memory credentials, active tasks, WebSocket clients, and circuit state.

## State and backup

Stop new sessions, then copy `data/computer-use-v2.sqlite3` plus its `-wal`
and `-shm` files together, and copy `data/audit-frames` (or the configured
`CUA_V2_FRAME_PATH`) if audit images are required. Credential sessions,
including Google OAuth refresh credentials, are intentionally not recoverable
after restart.

## Provider authentication

The supported catalog contains only three direct routes: GPT-5.6 Luna through
OpenAI Responses, Claude Sonnet 5 through Anthropic Messages, and Gemini 3.6
Flash through Google Interactions. All three accept API keys from the
environment or a process-local credential session. Gemini also supports a
browser OAuth flow; set `GOOGLE_OAUTH_CLIENT_ID` and
`GOOGLE_OAUTH_CLIENT_SECRET` (or `GOOGLE_OAUTH_CLIENT_SECRET_FILE`), with
optional `GOOGLE_CLOUD_PROJECT` and `CUA_GOOGLE_OAUTH_REDIRECT_URI`.

## Network hardening

Keep all ports loopback-bound. External binding requires
`CUA_ALLOW_PUBLIC_BIND=1` and `CUA_API_TOKEN`, plus an authenticated TLS
reverse proxy. The same token protects REST, WebSocket, and noVNC access;
`CUA_WS_TOKEN` is a deprecated fallback. The Docker socket and host filesystem
must never be exposed to the model container.

## Health and logs

- `/api/health`: process liveness.
- `/api/ready`: Docker, provider credential, and sandbox readiness.
- `/api/v2/provider-routes`: route configuration and executable status.
- `LOG_FORMAT=json`: structured production logs; credentials and tokens are redacted.

Follow [Rollback](rollback-v2.md) before deploying and retain the previous release archive.
