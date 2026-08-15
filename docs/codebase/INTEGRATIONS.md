# Integrations

## 1) Integration Inventory

| System | Type | Purpose | Auth model | Criticality | Evidence |
|---|---|---|---|---|---|
| OpenAI API | HTTPS API/SDK | Computer Use, optional planning/files | API key | high | `backend/engine/openai.py`; `backend/providers/openai.py` |
| Anthropic API | HTTPS API/SDK | Computer Use, optional planning/files | API key | high | `backend/engine/claude.py`; `backend/providers/anthropic.py` |
| Google Gen AI API | HTTPS API/SDK | Interactions Computer Use and optional planning | API key or browser OAuth | high | `backend/engine/gemini.py`; `backend/providers/gemini.py` |
| Docker Engine | local process/API | build/start/stop sandbox | host Docker access | high | `backend/infra/docker.py`; Compose |
| Sandbox action service | loopback HTTP | screenshots and OS actions | `X-Agent-Token` shared secret | critical | `backend/executor.py`; `docker/agent_service.py:72-97` |
| VNC/noVNC | TCP/WebSocket | human desktop observation/control | VNC password + optional WS token | high | Dashboard uses `/vnc/vnc.html?path=vnc/websockify`; `docker-compose.yml`; `frontend/src/pricing.ts` is local list-rate math, not a billing API |
| GitHub Actions | hosted automation | CI, audits, image scan, releases | repository permissions | medium | `.github/workflows/` |

## 2) Data Stores

| Store | Role | Access layer | Key risk | Evidence |
|---|---|---|---|---|
| SQLite/WAL | v2 sessions, actions, events, metrics, workflows, checkpoints | `backend/v2/persistence.py` | history has no automatic eviction | `backend/v2/persistence.py:49-176`; `USAGE.md:719-723` |
| Frame directory | audit image bytes outside SQLite | `backend/v2/retention.py` | sensitive screen content on disk | `TECHNICAL.md:56-62` |
| Process memory | credentials, tasks, clients, prompts, traces, circuit state | server/v2/infra registries | lost on restart; one-worker constraint | `docs/deployment.md:26` |
| Provider file stores | attached-file/vector artifacts | `backend/files.py` | data leaves host/provider lifecycle | `USAGE.md:801-820` |

## 3) Secrets and Credentials Handling

- Direct provider keys come from the process environment or an ephemeral v2 credential session. Google uses `GOOGLE_API_KEY` first, then `GEMINI_API_KEY`. `load_dotenv(..., override=False)` keeps a user/system `GOOGLE_API_KEY` already set. Credential responses expose readiness/expiry metadata, never the secret.
- Ephemeral credential sessions expire within eight hours, can be deleted early, and are not written to SQLite.
- The backend sends provider credentials only to the selected provider SDK/API.
- `AGENT_SERVICE_TOKEN` protects the in-container action service and is required by Compose. `VNC_PASSWORD` is also required unless an explicit insecure development escape hatch is used.
- Google OAuth uses a state- and PKCE-bound browser flow; refreshable credentials stay in the process-local vault with the same eight-hour maximum session lifetime.
- `CUA_API_TOKEN` gates REST, WebSockets, and noVNC; `CUA_WS_TOKEN` is a deprecated fallback. External binding still requires an authenticated TLS reverse proxy.

No hardcoded production credentials were found in the inspected configuration or source; `.env.example` contains names/placeholders only. Rotation is manual for environment secrets; v2 credential sessions expire within eight hours and support early deletion.

## 4) Reliability and Failure Behavior

- Route selection is explicit. Ordered fallbacks use a circuit breaker and at most one attempt per route.
- Missing credentials fail without retry; the catalog exposes only executable direct routes.
- Once an action may have executed, failure is treated as uncertain and is not replayed or failed over.
- Container startup waits for action-service readiness; Compose and the image both define health checks.
- WebSocket frame fan-out coalesces capture requests and drops stale pending previews for slow clients.
- Application shutdown cancels and awaits active tasks before closing shared clients.

Timeouts/limits are environment-configured and clamped (`STEP_TIMEOUT`, max steps, container readiness/backoff). Provider fallback is partial by design: retry/fallback stops when action state is uncertain.

## 5) Observability for Integrations

- `/api/health` reports process liveness; `/api/ready` includes Docker/provider/sandbox readiness; `/api/v2/provider-routes` exposes route configuration/executability.
- Console logging is default; `LOG_FORMAT=json` emits structured session-correlated logs.
- SQLite events and metrics back the audit and analytics APIs. In-memory traces add stage/event timing and redacted payloads.
- CI runs dependency audits for Python/npm and a Trivy HIGH/CRITICAL image scan.

## 6) Evidence

- `TECHNICAL.md:11-70` - provider routes, frames, persistence, credentials, and contracts.
- `backend/v2/api.py:195-285` - credential resolution, executable providers, route attempts, fallback, and audit writes.
- `backend/v2/persistence.py:49-176` - SQLite schema access and WAL behavior.
- `backend/engine/__init__.py:752-848` - provider client and sandbox executor selection.
- `backend/providers/anthropic.py:31-96` and sibling provider modules - provider run boundaries.
- `.env.example:1-45`, `docs/deployment.md:36-52`, and `USAGE.md:309-329,533-567` - credential and operational contracts.
- `docker-compose.yml:1-73` and `docker/agent_service.py:72-97` - sandbox service and token boundary.
- `.github/workflows/ci.yml:1-91` - audits and image scanning.
