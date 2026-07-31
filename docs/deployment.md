# Deployment Guide

v2 targets one trusted operator on a workstation. Do not expose it as a public multi-tenant service.

## Production-style local deployment

1. Install Docker Desktop, Node.js 22, and uv.
2. Copy `.env.example` to `.env`; set provider credentials and strong `AGENT_SERVICE_TOKEN` and `VNC_PASSWORD` values.
3. Install locked dependencies: `uv sync --frozen` and `npm --prefix frontend ci`.
4. Build the dashboard: `npm --prefix frontend run build`.
5. Start the sandbox: `docker compose up -d --build`.
6. Start FastAPI: `uv run --frozen python -m backend.main`.
7. Open `http://127.0.0.1:8100`. FastAPI serves `frontend/dist`; `/docs` exposes OpenAPI.

Use one backend worker. Multi-worker execution splits in-memory credentials, active tasks, WebSocket clients, and circuit state.

## State and backup

Stop new sessions, then copy `data/computer-use-v2.sqlite3` plus its `-wal` and `-shm` files together, and copy `data/frames` if audit images are required. Credential sessions are intentionally not recoverable after restart.

## Cloud authentication

AWS routes use the default credential chain, Google routes use Application Default Credentials, and Azure routes prefer Entra with an API-key fallback. In v2.0.0 these cloud routes are configuration-visible but not executable; only direct OpenAI, Anthropic, and Google routes execute.

## Network hardening

Keep all ports loopback-bound. External binding requires `CUA_ALLOW_PUBLIC_BIND=1` and `CUA_WS_TOKEN`, plus an authenticated TLS reverse proxy. The Docker socket and host filesystem must never be exposed to the model container.

## Health and logs

- `/api/health`: process liveness.
- `/api/ready`: Docker, provider credential, and sandbox readiness.
- `/api/v2/provider-routes`: route configuration and executable status.
- `LOG_FORMAT=json`: structured production logs; credentials and tokens are redacted.

Follow [Rollback](rollback-v2.md) before deploying and retain the previous release archive.
